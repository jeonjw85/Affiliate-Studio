"""Manual measurements: temporary SQLite, loopback TestClient, no real services."""
import importlib.util
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from studio.app import create_app
from test_core import product


def setup_client(tmp_path, monkeypatch):
    for key in ('WP_URL', 'WP_USERNAME', 'WP_APP_PASSWORD', 'AI_MODEL'):
        monkeypatch.delenv(key, raising=False)
    assert importlib.util.find_spec('studio.measurements') is not None, 'measurement module missing'
    from studio.measurements import install_routes
    app = create_app(tmp_path)
    install_routes(app, app.state.store)
    client = TestClient(app, base_url='http://127.0.0.1:8787')
    client.headers['X-CSRF-Token'] = client.get('/api/state').json()['csrf']
    store = app.state.store
    p = store.add_product(product())
    draft = store.create_draft(p['id'])
    return client, store, draft


def payload(draft, **changes):
    return dict(dict(draft_id=draft['id'], channel='wordpress', page_url='https://public.example/report',
                     period_start='2026-01-01', period_end='2026-01-31',
                     source='관리자 월간보고서 / UTC / 클릭 후 7일 귀속', views=300, clicks=3, orders=1,
                     revenue_won='10.00', cost_won='2.50', note='수동 입력'), **changes)


def test_create_list_snapshot_rates_persist_without_draft_mutation(tmp_path, monkeypatch):
    client, store, draft = setup_client(tmp_path, monkeypatch)
    before = store.state()
    response = client.post('/api/measurements', json=payload(draft))
    assert response.status_code == 200, response.text
    row = response.json()
    assert row['version'] == 1
    assert row['draft_version'] == draft['version'] and row['draft_title'] == draft['title']
    assert row['rates'] == dict(click_rate='1.00', order_rate='33.33', epc_won='3.33', balance_won='7.50')
    assert row['warnings'] == []
    assert row['created_at'] == row['updated_at']
    assert set(row) == set(payload(draft)) | {'id', 'version', 'draft_version', 'draft_title', 'created_at', 'updated_at', 'rates', 'warnings'}
    assert store.state() == before
    with store.connect() as c:
        assert len(store.rows(c, 'measurement')) == 1
    from studio.measurements import install_routes
    reopened = create_app(tmp_path)
    install_routes(reopened, reopened.state.store)
    assert TestClient(reopened, base_url='http://localhost').get('/api/measurements').json() == {'items': [row]}
    client.close()


@pytest.mark.parametrize('change', [
    {'draft_id': True}, {'draft_id': []}, {'draft_id': 'missing'}, {'draft_id': 'x' * 65},
    {'channel': 'bad'}, {'channel': []}, {'channel': True},
    {'page_url': None}, {'page_url': 5}, {'page_url': 'http://public.example'},
    {'page_url': 'https://127.0.0.1/x'}, {'page_url': 'javascript:alert(1)'},
    {'source': ''}, {'source': ' '}, {'source': True}, {'source': []}, {'source': 'x' * 501},
    {'note': None}, {'note': True}, {'note': 'x' * 1001},
    {'views': True}, {'views': '1'}, {'clicks': 0.1}, {'orders': -1}, {'orders': 1000000001},
    {'revenue_won': True}, {'revenue_won': 1}, {'revenue_won': 1.2},
    {'revenue_won': 'NaN'}, {'revenue_won': 'Infinity'}, {'revenue_won': '1e2'},
    {'revenue_won': '1.001'}, {'revenue_won': '-1'}, {'cost_won': '1000000000000.01'},
    {'period_start': '2026-02-30'}, {'period_start': '20260101'}, {'period_start': True},
    {'period_start': '2026-1-01'}, {'period_end': '2025-12-31'}, {'period_end': '9999-12-31'},
])
def test_strict_validation_is_atomic(tmp_path, monkeypatch, change):
    client, store, draft = setup_client(tmp_path, monkeypatch)
    response = client.post('/api/measurements', json=payload(draft, **change))
    assert response.status_code == 400, response.text
    with store.connect() as c:
        assert store.rows(c, 'measurement') == []


@pytest.mark.parametrize('raw', ['[]', 'null', 'true', '12', '"text"', '{', '{"views":NaN}'])
def test_wrong_json_shapes_return_400(tmp_path, monkeypatch, raw):
    client, _, _ = setup_client(tmp_path, monkeypatch)
    response = client.post('/api/measurements', content=raw, headers={'Content-Type': 'application/json'})
    assert response.status_code == 400, response.text


def test_null_zero_decimal_rounding_and_date_boundary(tmp_path, monkeypatch):
    client, _, draft = setup_client(tmp_path, monkeypatch)
    tomorrow = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
    response = client.post('/api/measurements', json=payload(draft, period_start=tomorrow, period_end=tomorrow,
                           views=0, clicks=0, orders=None, revenue_won=None, cost_won='0', page_url=''))
    assert response.status_code == 200, response.text
    row = response.json()
    assert row['revenue_won'] is None and row['cost_won'] == '0.00'
    assert all(value is None for value in row['rates'].values())
    body = payload(draft, revenue_won='0.01', cost_won='0', clicks=8, orders=0, views=1000000000)
    row = client.post('/api/measurements', json=body).json()
    assert row['rates'] == dict(click_rate='0.00', order_rate='0.00', epc_won='0.00', balance_won='0.01')
    body.update(page_url='', revenue_won='1.005')
    assert client.post('/api/measurements', json=body).status_code == 400
    body.update(page_url='https://public.example/half', revenue_won='1.00')
    assert client.post('/api/measurements', json=body).json()['rates']['epc_won'] == '0.13'
    body.update(page_url='https://public.example/max', revenue_won='1000000000000', cost_won=None)
    row = client.post('/api/measurements', json=body).json()
    assert row['revenue_won'] == '1000000000000.00' and row['rates']['balance_won'] is None
    body.update(page_url='https://public.example/optional')
    for field in ('views', 'clicks', 'orders', 'revenue_won', 'cost_won', 'note'):
        body.pop(field)
    row = client.post('/api/measurements', json=body).json()
    assert row['views'] is None and row['note'] == ''


def test_edit_snapshot_duplicate_conflict_delete_and_warnings(tmp_path, monkeypatch):
    client, store, draft = setup_client(tmp_path, monkeypatch)
    body = payload(draft, views=1, clicks=3, orders=4)
    first = client.post('/api/measurements', json=body).json()
    assert len(first['warnings']) == 2
    assert client.post('/api/measurements', json=body).status_code == 409
    second_body = payload(draft, period_start='2026-01-31', period_end='2026-02-01')
    second = client.post('/api/measurements', json=second_body).json()
    assert any('기간' in w for w in second['warnings'])
    items = client.get('/api/measurements').json()['items']
    assert all(any('기간' in w for w in row['warnings']) for row in items)
    path = '/api/measurements/' + second['id']
    assert client.put(path, json=dict(body, version=1)).status_code == 409
    assert client.put(path, json=dict(second_body, draft_id='other', version=1)).status_code == 400
    store.edit_draft(draft['id'], draft['version'], '수정된 제목', draft['body'])
    response = client.put(path, json=dict(second_body, version=1, clicks=0, orders=0,
                          note='수정', period_start='2026-02-01', revenue_won='0', cost_won='1'))
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited['version'] == 2 and edited['created_at'] == second['created_at']
    assert edited['draft_title'] == draft['title'] and edited['draft_version'] == draft['version']
    assert edited['rates']['balance_won'] == '-1.00' and edited['warnings'] == []
    assert client.put(path, json=dict(second_body, version=1)).status_code == 409
    assert client.request('DELETE', path, json={'version': 1}).status_code == 409
    assert client.request('DELETE', path, json={'version': 2}).status_code == 200
    items = client.get('/api/measurements').json()['items']
    assert len(items) == 1 and len(items[0]['warnings']) == 2
    assert client.request('DELETE', path, json={'version': 2}).status_code == 400
    assert client.put('/api/measurements/missing', json=dict(body, version=1)).status_code == 400


@pytest.mark.parametrize('version', [None, True, False, '1', 1.0, 0, -1, [], {}])
def test_invalid_mutation_versions_cannot_write(tmp_path, monkeypatch, version):
    client, _, draft = setup_client(tmp_path, monkeypatch)
    row = client.post('/api/measurements', json=payload(draft)).json()
    path = '/api/measurements/' + row['id']
    assert client.put(path, json=dict(payload(draft), version=version)).status_code == 400
    assert client.request('DELETE', path, json={'version': version}).status_code == 400
    assert client.get('/api/measurements').json()['items'] == [row]


def test_concurrent_duplicate_create_and_optimistic_update(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    client, store, draft = setup_client(tmp_path, monkeypatch)
    body = payload(draft)
    barrier = Barrier(2)
    def create(_):
        barrier.wait()
        return client.post('/api/measurements', json=body).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(create, range(2))) == [200, 409]
    row = client.get('/api/measurements').json()['items'][0]
    barrier = Barrier(2)
    def edit(index):
        barrier.wait()
        return client.put('/api/measurements/' + row['id'], json=dict(body, version=1, note=str(index))).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(edit, range(2))) == [200, 409]
    with store.connect() as c:
        assert len(store.rows(c, 'measurement')) == 1
        assert store.rows(c, 'measurement')[0]['version'] == 2


@pytest.mark.parametrize('channel', ['naver', 'tistory', 'other'])
def test_channel_and_url_separate_duplicate_keys(tmp_path, monkeypatch, channel):
    client, _, draft = setup_client(tmp_path, monkeypatch)
    assert client.post('/api/measurements', json=payload(draft)).status_code == 200
    row = client.post('/api/measurements', json=payload(draft, channel=channel)).json()
    assert row['warnings'] == []
    row = client.post('/api/measurements', json=payload(draft, page_url='')).json()
    assert row['warnings'] == []


def test_money_leading_zeroes_are_valid_decimal_strings(tmp_path, monkeypatch):
    client, _, draft = setup_client(tmp_path, monkeypatch)
    response = client.post('/api/measurements', json=payload(draft, revenue_won='000000000000001.00'))
    assert response.status_code == 200, response.text
    assert response.json()['revenue_won'] == '1.00'


@pytest.mark.parametrize('prefix', ['=', '+', '-', '@', ' \t=', '\n +', '\u2003@'])
def test_csv_bom_formula_defense_precision_unknown_and_no_fetch(tmp_path, monkeypatch, prefix):
    import csv
    import io
    import socket
    client, store, draft = setup_client(tmp_path, monkeypatch)
    def forbidden(*args, **kwargs):
        raise AssertionError('measurement must never fetch URLs')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    with store.connect() as c:
        draft['title'] = prefix + '1+1'
        store.put(c, 'draft', draft)
    body = payload(draft, source=prefix + 'SUM(1,2)', note=prefix + 'HYPERLINK("x")',
                   revenue_won='0.01', cost_won='1.00', orders=None)
    response = client.post('/api/measurements', json=body)
    assert response.status_code == 200, response.text
    exported = client.get('/api/measurements/export')
    assert exported.status_code == 200
    assert exported.content.startswith(b'\xef\xbb\xbf')
    assert 'attachment' in exported.headers['content-disposition']
    assert 'text/csv' in exported.headers['content-type']
    rows = list(csv.DictReader(io.StringIO(exported.content.decode('utf-8-sig'))))
    assert len(rows) == 1
    row = rows[0]
    for field in ('draft_title', 'source', 'note'):
        assert row[field].startswith("'"), (field, row[field])
    assert row['revenue_won'] == '0.01' and row['cost_won'] == '1.00'
    assert row['balance_won'] == '-0.99' and row['order_rate'] == '' and row['orders'] == ''
    assert row['source'][1:] == response.json()['source']
    assert row['period_start'] == body['period_start']
    assert client.get('/api/measurements').json()['items'][0]['source'] == response.json()['source']


def test_empty_csv_and_security_boundaries_cover_every_route(tmp_path, monkeypatch):
    import csv
    import io
    client, _, draft = setup_client(tmp_path, monkeypatch)
    response = client.get('/api/measurements/export')
    assert response.status_code == 200
    reader = csv.DictReader(io.StringIO(response.content.decode('utf-8-sig')))
    assert 'source' in reader.fieldnames and list(reader) == []
    row = client.post('/api/measurements', json=payload(draft)).json()
    for path in ('/api/measurements', '/api/measurements/export'):
        for headers in ({'Host': 'evil.example'}, {'Origin': 'https://evil.example'}, {'Sec-Fetch-Site': 'cross-site'}):
            assert client.get(path, headers=headers).status_code == 403
        assert client.get(path).headers['cache-control'] == 'no-store'
    for method, path in (('POST', '/api/measurements'), ('PUT', '/api/measurements/' + row['id']),
                         ('DELETE', '/api/measurements/' + row['id'])):
        assert client.request(method, path, json={'version': 1}, headers={'X-CSRF-Token': ''}).status_code == 403
        assert client.request(method, path, content='{}', headers={'Content-Type': 'text/plain'}).status_code == 415
    for method in ('PUT', 'DELETE'):
        assert client.request(method, '/api/measurements/' + row['id'], content='[]',
                              headers={'Content-Type': 'application/json'}).status_code == 400
    assert client.get('/api/measurements').json()['items'] == [row]
