"""User-facing copy only: retain review, transport and local-boundary semantics."""
from datetime import timedelta
import ast
from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient

from studio.app import create_app
from studio.core import Store, now, validate_product
from test_core import product
from test_jobs import ready
from test_publication import wp_server
from test_ai import ai_server
from test_measurements import payload


def test_review_messages_keep_source_checks_and_limits(tmp_path):
    store, p, d = ready(tmp_path)
    p = dict(p, provider='toss', observed_at=(now() - timedelta(days=2)).isoformat())
    d = dict(d, body='직접 사용, 999kg')
    checks = store.checks(d, p)
    assert checks == {
        'errors': [
            '가격 정보 확인 후 24시간 경과: 상품을 수정해 실제 정보를 재확인하거나 가격 삭제 필요',
            '근거 확인이 필요한 후기/단정 표현 제거 필요: 직접 사용',
            '등록된 근거에 없는 수치는 제거하거나 출처 확인 후 상품 근거에 기록 필요: 999kg',
        ],
        'warnings': [
            '이 점검은 문구와 일부 수치만 휴리스틱으로 확인하며, 모든 허위 주장이나 법률·약관 준수, 상품 정보의 진실성은 검증하지 않음',
            '링크 소유권·수익 귀속·가격·이미지 사용 권리와 게시 매체 등록은 직접 확인 필요',
            '토스 링크의 블로그 게시 허용 여부와 최신 대가성 표시 문구는 토스 앱 이용조건에서 확인 필요',
        ],
    }
    assert store.detail(d['id'])['draft']['body'] != d['body']
    assert store.checks(dict(d, body='구매 조건 확인'), dict(p, price=None, provider='coupang')) == {
        'errors': [], 'warnings': checks['warnings'][:2],
    }


@pytest.mark.parametrize('change, message', [
    ({'name': ''}, '빈 값 또는 너무 긴 텍스트'),
    ({'observed_at': '2026-01-01'}, '시간대가 포함된 올바른 날짜 필요'),
    ({'source_url': 'http://example.com'}, '로그인 정보 없는 HTTPS 공개 도메인 링크 필요'),
    ({'provider': 'other'}, '쿠팡 또는 토스 선택 필요'),
    ({'ownership_confirmed': False}, '본인 제휴 링크와 허용된 게시 매체는 직접 확인 필요'),
    ({'price': -1}, '가격은 양의 정수 원 단위로 입력하거나 비워두기'),
    ({'observed_at': '2099-01-01T00:00:00Z'}, '미래 시각은 확인 시점으로 기록 불가'),
    ({'facts': []}, '출처에서 확인한 사실 1~20개 필요'),
])
def test_product_validation_messages(change, message):
    with pytest.raises(ValueError) as error:
        validate_product(product(**change))
    assert str(error.value) == message


@pytest.mark.parametrize('operation, message', [
    (lambda s, p, d: s.package(d['id'], 1), '현재 버전의 검토 승인 필요'),
    (lambda s, p, d: s.approve(d['id'], 1, False), '출처·내용·제휴 링크·광고 표시를 직접 검토했다는 확인 필요'),
    (lambda s, p, d: s.approve(d['id'], 2, True), '버전 변경됨: 새로고침 후 다시 검토 필요'),
    (lambda s, p, d: s.detail('missing'), '항목을 찾을 수 없음'),
    (lambda s, p, d: s.add_product(p), '같은 출처 URL의 중복 상품'),
    (lambda s, p, d: s.schedule(d['id'], 1, '2099-01-01T00:00:00Z'), '예약 시각은 현재 이후 30일 이내로 지정 필요'),
    (lambda s, p, d: s.edit_draft(d['id'], 1, '제목', '본문', 'unsupported'), '지원하지 않는 본문 서식'),
    (lambda s, p, d: s.wordpress_draft(d['id'], 1, False, None), 'WordPress 연결 설정과 외부 초안 전송 확인 필요'),
    (lambda s, p, d: s.generate_ai(d['id'], 1, 'guide', False, None), 'AI 연결 설정과 자료 전송 확인 필요'),
])
def test_store_messages_preserve_required_review_and_consent(tmp_path, operation, message):
    store, p, d = ready(tmp_path)
    before = store.state()
    with pytest.raises(ValueError) as error:
        operation(store, p, d)
    assert str(error.value) == message
    assert store.state() == before


def test_uncertain_delivery_message_keeps_manual_check_and_no_retry(tmp_path):
    store, p, d = ready(tmp_path)
    store.approve(d['id'], 1, True)
    with wp_server() as (wp, remote):
        remote.lose = True
        result = store.wordpress_draft(d['id'], 1, True, wp)
        assert result['state'] == 'uncertain'
        assert len(remote.requests) == 1
        assert store.wordpress_draft(d['id'], 1, True, wp) == result
        assert len(remote.requests) == 1
        assert result['error'] == '전송 결과 불명: WordPress 관리자에서 확인 필요 (중복 방지를 위해 자동 재전송 안 함)'


def test_interrupted_delivery_message_keeps_remote_check_and_no_retry(tmp_path):
    store, p, d = ready(tmp_path)
    with store.connect() as c:
        store.put(c, 'delivery', dict(id='interrupted', draft_id=d['id'], state='sending', _pid=2147483647))
    reopened = Store(store.path)
    with reopened.connect() as c:
        record = reopened.get(c, 'interrupted', 'delivery')
    assert record['state'] == 'uncertain'
    assert record['error'] == '이전 실행 프로세스 종료됨: 자동 재전송 안 함, 원격 초안은 WordPress에서 확인 필요'


def test_ai_evidence_warning_is_not_fact_verification_and_body_is_untouched(tmp_path):
    from studio.ai import LocalAI
    store, p, d = ready(tmp_path)
    with ai_server() as (url, remote):
        candidate = store.generate_ai(d['id'], 1, 'guide', True, LocalAI(url, 'local-test'))
        assert candidate['body'] == remote.answer['body']
        assert store.detail(d['id'])['draft'] == d
        assert candidate['warnings'] == ['AI 근거 번호는 사실 검증이 아님: 원문과 대조해 검토 필요']


def _message_literals(filename):
    """Copy lint, restricted to message sinks; never lint generated article/prompt text."""
    source = Path(__file__).resolve().parents[1] / 'studio' / filename
    tree = ast.parse(source.read_text())
    values = []
    for node in ast.walk(tree):
        roots = []
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            roots = node.exc.args
        elif isinstance(node, ast.keyword) and node.arg in ('error', 'warnings'):
            roots = [node.value]
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and (t.id.endswith('_error') or t.id == 'warnings')
            for t in node.targets
        ):
            roots = [node.value]
        elif isinstance(node, ast.Dict):
            roots = [v for k, v in zip(node.keys, node.values)
                     if isinstance(k, ast.Constant) and k.value == 'error']
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr == 'append' and isinstance(node.func.value, ast.Name)
              and node.func.value.id in ('warnings', 'errors')):
            roots = node.args
        elif (filename == 'measurements.py' and isinstance(node, ast.Tuple)
              and len(node.elts) == 3):
            roots = [node.elts[-1]]
        for root in roots:
            values.extend(n.value for n in ast.walk(root)
                          if isinstance(n, ast.Constant) and isinstance(n.value, str)
                          and re.search('[가-힣]', n.value))
    return values


@pytest.mark.parametrize('filename', ['core.py', 'exporting.py', 'publication.py', 'wordpress.py', 'ai.py', 'app.py', 'measurements.py'])
def test_message_literals_use_brief_copy_not_runtime_stripping(filename):
    messages = _message_literals(filename)
    assert messages
    assert not [m for m in messages if re.search(r'(?:합니다|습니다|입니다|세요|됩니다)', m) or m.endswith('.')]


@pytest.mark.parametrize('confirmed, minutes, message', [
    (False, 15, '실제 공개 게시 예약 확인 필요'),
    (True, 4, '공개 예약은 5분 이후부터 30일 이내로 지정 필요'),
    (True, 1500, '예약 시점이 가격 확인 후 24시간 초과: 가격 제거 또는 재확인 필요'),
    (True, 15, '같은 버전과 사이트에 WordPress 초안 먼저 전송 필요'),
])
def test_publication_requirements_still_block_before_network(tmp_path, confirmed, minutes, message):
    store, p, d = ready(tmp_path)
    store.approve(d['id'], 1, True)
    with wp_server() as (wp, remote):
        with pytest.raises(ValueError) as error:
            store.publish_schedule(d['id'], 1, (now() + timedelta(minutes=minutes)).isoformat(), confirmed, wp)
        assert remote.requests == []
        assert store.state()['publications'] == []
        assert str(error.value) == message


def test_publication_unknown_result_keeps_lock_and_manual_recovery(tmp_path):
    store, p, d = ready(tmp_path)
    store.approve(d['id'], 1, True)
    with wp_server() as (wp, remote):
        store.wordpress_draft(d['id'], 1, True, wp)
        remote.lose = True
        due = (now() + timedelta(minutes=15)).isoformat()
        result = store.publish_schedule(d['id'], 1, due, True, wp)
        count = len(remote.requests)
        assert store.publish_schedule(d['id'], 1, due, True, wp) == result
        assert len(remote.requests) == count
        assert result['state'] == 'uncertain'
        assert store.detail(d['id'])['publication_lock']
        assert result['error'] == '공개 예약 결과 불명: 원격 상태 조회 필요 (자동 재전송 안 함)'
        remote.lose = False
        assert store.refresh_publication(result['id'], wp)['state'] == 'future'
        remote.corrupt = True
        refreshed = store.refresh_publication(result['id'], wp)
        assert refreshed['state'] == 'uncertain'
        assert refreshed['error'] == '원격 상태·제목·본문·예약 시각 확인 불가: WordPress에서 확인 필요'


def test_cancel_message_keeps_wordpress_only_steps_and_published_boundary(tmp_path):
    store, p, d = ready(tmp_path)
    with wp_server() as (wp, remote):
        with pytest.raises(ValueError) as error:
            store.cancel_publication('not-needed', True, wp)
        assert remote.requests == []
        assert str(error.value) == ('앱에서 직접 취소 불가: WordPress 관리자에서 현재 상태 확인 후 예약 글을 초안으로 변경하고 '
                                    '앱의 원격 상태 조회 실행 필요 (이미 공개된 글은 앱에서 취소하지 않음)')


def test_wordpress_verify_message_retains_approved_content_boundary(tmp_path):
    store, p, d = ready(tmp_path)
    with wp_server() as (wp, remote):
        with pytest.raises(ValueError) as error:
            wp.verify({'title': {'raw': d['title']}, 'content': {'raw': '다른 본문'}}, d, p)
        assert str(error.value) == '원격 제목·본문·광고 표시가 승인한 내용과 다름: WordPress에서 확인 필요'
        assert remote.requests == []


@pytest.mark.parametrize('url, model, message', [
    ('https://example.com/v1', 'model', 'AI는 로컬 http loopback /v1 주소, 1~240초 제한, 1~100회 한도 필요'),
    ('http://127.0.0.1:12345/v1', 'model:cloud', '클라우드 모델 대신 다운로드한 로컬 모델 지정 필요'),
])
def test_ai_config_messages_keep_local_only_boundary(url, model, message):
    from studio.ai import LocalAI
    with pytest.raises(ValueError) as error:
        LocalAI(url, model)
    assert str(error.value) == message


def test_ai_bad_reply_message_does_not_retry_or_change_saved_body(tmp_path):
    from studio.ai import LocalAI
    store, p, d = ready(tmp_path)
    with ai_server() as (url, remote):
        ai = LocalAI(url, 'missing-model')
        assert ai.check() == {'available': False, 'model': 'missing-model', 'models': ['local-test'],
                              'error': '설정한 모델을 로컬 서버에서 찾을 수 없음'}
        remote.answer = None
        with pytest.raises(ValueError) as error:
            store.generate_ai(d['id'], 1, 'guide', True, ai)
        assert len(remote.requests) == 2  # One model check and one generation, no retries.
        assert store.detail(d['id'])['draft'] == d
        assert str(error.value) == 'AI 생성 실패: 연결, 제한 또는 JSON 제목·본문·근거 번호 확인 필요 (자동 재시도 안 함)'


@pytest.fixture
def ui_client(tmp_path, monkeypatch):
    for key in ('WP_URL', 'WP_USERNAME', 'WP_APP_PASSWORD', 'AI_MODEL', 'AI_BASE_URL', 'AI_TIMEOUT_SECONDS', 'AI_DAILY_LIMIT'):
        monkeypatch.delenv(key, raising=False)
    with TestClient(create_app(tmp_path), base_url='http://127.0.0.1:8787') as client:
        client.headers['X-CSRF-Token'] = client.get('/api/state').json()['csrf']
        yield client


@pytest.mark.parametrize('headers, content, status, message', [
    ({'Host': 'external.example'}, '{}', 403, '로컬 접속만 지원'),
    ({'Origin': 'https://external.example'}, '{}', 403, '다른 출처의 요청은 허용 안 함'),
    ({'Sec-Fetch-Site': 'cross-site'}, '{}', 403, '외부 사이트 요청 차단'),
    ({'X-CSRF-Token': ''}, '{}', 403, '화면 새로고침 후 다시 시도 필요'),
    ({'Content-Type': 'text/plain'}, '{}', 415, 'JSON 요청만 지원'),
    ({}, 'x' * 131073, 413, '요청 크기 초과'),
], ids=['host', 'origin', 'cross-site', 'csrf', 'content-type', 'size'])
def test_http_boundary_messages_preserve_status_and_block_writes(ui_client, headers, content, status, message):
    response = ui_client.post('/api/products', content=content, headers={'Content-Type': 'application/json', **headers})
    assert response.status_code == status
    assert response.json() == {'error': message}
    assert ui_client.app.state.store.state()['products'] == []


@pytest.mark.parametrize('env, key, message', [
    ({}, 'ai_error', '로컬 모델 서버 실행과 AI_MODEL 설정 필요'),
    ({'AI_MODEL': 'test', 'AI_BASE_URL': 'https://external.example/v1'}, 'ai_error', '로컬 AI 환경변수와 AI_LOCAL_HOSTS 확인 필요'),
    ({'WP_URL': 'https://example.com'}, 'wordpress_error', 'WordPress 환경변수 세 개 모두 설정 필요'),
    ({'WP_URL': 'http://example.com', 'WP_USERNAME': 'fixture', 'WP_APP_PASSWORD': 'fixture'}, 'wordpress_error', 'WordPress 서버 환경변수 형식 확인 필요'),
])
def test_connection_setup_messages(tmp_path, monkeypatch, env, key, message):
    for name in ('WP_URL', 'WP_USERNAME', 'WP_APP_PASSWORD', 'AI_MODEL', 'AI_BASE_URL', 'AI_TIMEOUT_SECONDS', 'AI_DAILY_LIMIT'):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with TestClient(create_app(tmp_path), base_url='http://localhost') as client:
        connections = client.get('/api/state').json()['connections']
        assert connections[key] == message
        assert connections['ai' if key == 'ai_error' else 'wordpress'] is False


@pytest.mark.parametrize('change, message', [
    ({'channel': 'bad'}, '올바른 채널 선택 필요'),
    ({'period_start': '20260101'}, '기간은 YYYY-MM-DD 형식으로 입력 필요'),
    ({'period_end': '2025-01-01'}, '기간 순서 또는 미래 날짜 확인 필요'),
    ({'note': None}, '메모는 1000자 이하 문자열로 입력 필요'),
    ({'views': -1}, '조회/클릭/주문은 null 또는 0~10억 정수로 입력 필요'),
    ({'revenue_won': '0.001'}, '금액은 소수 2자리 이하 숫자 문자열로 입력 필요'),
    ({'cost_won': '1000000000001'}, '금액은 1조 이하로 입력 필요'),
])
def test_measurement_validation_messages(ui_client, change, message):
    response = ui_client.post('/api/measurements', json=payload({'id': 'not-needed'}, **change))
    assert response.status_code == 400
    assert response.json() == {'error': message}
    assert ui_client.get('/api/measurements').json() == {'items': []}


@pytest.mark.parametrize('raw, message', [
    ('{"views":NaN}', '올바른 JSON 객체 필요'),
    ('[]', 'JSON 객체 필요'),
])
def test_measurement_json_messages(ui_client, raw, message):
    response = ui_client.post('/api/measurements', content=raw, headers={'Content-Type': 'application/json'})
    assert response.status_code == 400
    assert response.json() == {'error': message}


def test_measurement_warnings_keep_attribution_and_overlap_limits(ui_client):
    store = ui_client.app.state.store
    p = store.add_product(product())
    d = store.create_draft(p['id'])
    body = payload(d, views=1, clicks=3, orders=4, note='확인하세요. 필요합니다.')
    first = ui_client.post('/api/measurements', json=body).json()
    assert first['note'] == body['note']
    assert first['warnings'] == ['클릭이 조회보다 많음: 반복 클릭 또는 귀속 기준 확인 필요',
                                 '주문이 클릭보다 많음: 귀속 기준 확인 필요']
    second = ui_client.post('/api/measurements', json=dict(body, period_end='2026-02-01')).json()
    assert second['warnings'] == first['warnings'] + ['동일 콘텐츠/채널/URL의 기록 기간 중복: 단순 합산 금지']


@pytest.mark.parametrize('changes, status, message', [
    ({'version': 0}, 400, '버전은 양의 정수 필요'),
    ({'version': 2}, 409, '기록 변경됨: 새로고침 후 다시 시도 필요'),
    ({'version': 1, 'draft_id': 'other'}, 400, '연결된 콘텐츠는 변경 불가'),
])
def test_measurement_edit_errors_preserve_versions(ui_client, changes, status, message):
    store = ui_client.app.state.store
    p = store.add_product(product())
    d = store.create_draft(p['id'])
    body = payload(d)
    first = ui_client.post('/api/measurements', json=body).json()
    response = ui_client.put('/api/measurements/' + first['id'], json=dict(body, **changes))
    assert response.status_code == status
    assert response.json() == {'error': message}
    assert ui_client.get('/api/measurements').json() == {'items': [first]}
