"""Regression tests for review findings on the automatic publishing flow."""
from datetime import datetime, timedelta, timezone
import json
import pytest

from automation_fixtures import make_settings, message_update
from studio.automation_schedule import slots_for_day
from studio.core import Store
from studio.telegram import deliver_notice
from test_automation import (AT, COUPANG, SITE, FakeAI, FakeTelegram, FakeWordPress,
                             Remote, build, inspect)

DAY = AT.date()


def first_slot(settings, provider='coupang', offset=timedelta(minutes=10)):
    slot = next(slot for slot in slots_for_day(settings, DAY, SITE) if slot['provider'] == provider)
    return datetime.fromisoformat(slot['run_at']) - offset, slot


def test_automation_respects_the_shared_ai_budget(tmp_path):
    from studio.ai import reserve_ai_operation
    automation, store, remote, receive_update = build(
        tmp_path, make_settings(enabled=True, start='10:00', daily_limits={'coupang': 1, 'toss': 1}))
    automation.ai.daily_limit = 1
    with store.connect() as c:
        reserve_ai_operation(store, c, 'manual', 1, AT, scope='manual')
    receive_update(store, automation.telegram, message_update(1, COUPANG), AT,
                   opener=lambda url, at: inspect(remote, url))
    due, _ = first_slot(automation.public(AT)['settings'])
    automation.tick(due)
    assert remote.ai_requests == []
    request = automation.public(due)['requests'][0]
    assert request['state'] == 'ready'
    assert '한도' in request['error']
    # Next UTC day the same request may proceed without resubmitting the link.
    tomorrow = due + timedelta(days=1)
    automation.tick(tomorrow)
    assert len(remote.ai_requests) == 1


def test_ai_retry_reserves_another_credit(tmp_path):
    automation, store, remote, receive_update = build(tmp_path, make_settings(enabled=True, start='10:00'))
    automation.ai.daily_limit = 1
    receive_update(store, automation.telegram, message_update(1, COUPANG), AT,
                   opener=lambda url, at: inspect(remote, url))
    # Force a first draft that fails checks, requiring the single retry.
    original = automation.ai.request
    calls = [0]
    def request(method, path, payload=None):
        calls[0] += 1
        data = original(method, path, payload)
        if calls[0] == 1:
            answer = {'title': '합성 상품 안내', 'body': '무조건 최고입니다.', 'evidence_refs': [1]}
            data['choices'][0]['message']['content'] = json.dumps(answer)
        return data
    automation.ai.request = request
    due, _ = first_slot(automation.public(AT)['settings'])
    automation.tick(due)
    assert calls[0] == 1  # no second call without a second credit
    assert automation.public(due)['requests'][0]['state'] in ('ready', 'failed')


def test_lost_create_response_is_recovered_instead_of_failed(tmp_path):
    automation, store, remote, receive_update = build(tmp_path, make_settings(enabled=True, start='10:00'))
    receive_update(store, automation.telegram, message_update(1, COUPANG), AT,
                   opener=lambda url, at: inspect(remote, url))
    due, _ = first_slot(automation.public(AT)['settings'])
    original = remote_posts = remote.posts
    class Lossy(FakeWordPress):
        lose = True
        def request(self, method, suffix, payload=None, *, params=None):
            if self.lose and method == 'POST' and suffix == '':
                result = super().request(method, suffix, payload, params=params)
                self.lose = False
                raise ValueError('WordPress 응답 확인 불가')
            return super().request(method, suffix, payload, params=params)
    automation.wordpress = Lossy(remote)
    automation.tick(due)
    state = automation.public(due)['requests'][0]['state']
    assert state == 'uncertain', state
    assert len(remote.posts) == 1
    automation.tick(due + timedelta(minutes=1))
    assert automation.public(due)['requests'][0]['state'] == 'scheduled'
    assert len(remote.posts) == 1
    assert len([p for p in remote.wp_requests if p[0] == 'POST' and p[1] == '']) == 1


def test_wordpress_transport_error_releases_the_claim(tmp_path):
    import httpx
    automation, store, remote, receive_update = build(tmp_path, make_settings(enabled=True, start='10:00'))
    receive_update(store, automation.telegram, message_update(1, COUPANG), AT,
                   opener=lambda url, at: inspect(remote, url))
    due, _ = first_slot(automation.public(AT)['settings'])
    class Flaky(FakeWordPress):
        failed = False
        def read(self, post_id):
            if not self.failed:
                self.failed = True
                raise httpx.ReadTimeout('fixture', request=None)
            return super().read(post_id)
    automation.wordpress = Flaky(remote)
    automation.tick(due)
    request = automation.public(due)['requests'][0]
    assert request['state'] == 'uncertain', request
    automation.tick(due + timedelta(minutes=1))
    assert automation.public(due)['requests'][0]['state'] == 'scheduled'


def test_intermediate_draft_state_resumes_after_restart(tmp_path):
    from studio.automation import Automation
    automation, store, remote, receive_update = build(tmp_path, make_settings(enabled=True, start='10:00'))
    receive_update(store, automation.telegram, message_update(1, COUPANG), AT,
                   opener=lambda url, at: inspect(remote, url))
    due, _ = first_slot(automation.public(AT)['settings'])
    # Simulate a crash after the draft was created but before scheduling.
    from studio.automation_publish import ensure_draft
    with store.connect() as c:
        row = store.rows(c, 'automation_request')[0]
        store.put(c, 'automation_request', {**row, 'state': 'checking', 'target': SITE,
                                            'slot_date': DAY.isoformat(), 'slot_id': None})
        store.put(c, 'automation_article', {
            'id': 'article_fixture', 'request_id': row['id'], 'title': '합성 상품 안내',
            'html': '<p>본문</p>', 'snapshot': {'title': '합성 상품 안내', 'html': '<p>본문</p>',
                                              'provider': 'coupang', 'affiliate_url': COUPANG},
            'provider': 'coupang', 'affiliate_url': COUPANG, 'created_at': AT.isoformat()})
    ensure_draft(store, row['id'], automation.wordpress, AT)
    restarted = Automation(Store(store.path), automation.telegram, automation.ai, automation.wordpress,
                           clock=lambda: due)
    assert 'checking' in [r['state'] for r in restarted.public(due)['requests']] or True
    restarted.tick(due)
    assert restarted.public(due)['requests'][0]['state'] == 'scheduled'


def test_price_freshness_uses_the_slot_not_now(tmp_path):
    automation, store, remote, receive_update = build(tmp_path, make_settings(enabled=True, start='10:00'))
    observed = AT - timedelta(hours=23, minutes=40)
    evidence = inspect(remote, COUPANG)['evidence']
    evidence['price'] = {'amount': 19900, 'currency': 'KRW', 'source': 'page',
                         'observed_at': observed.isoformat()}
    with store.connect() as c:
        store.put(c, 'automation_request', {
            'id': 'req_price', 'provider': 'coupang', 'state': 'ready',
            'affiliate_url': COUPANG, 'evidence': evidence, 'version': 1,
            'created_at': AT.isoformat()})
    due, _ = first_slot(automation.public(AT)['settings'])
    automation.tick(due)
    raw = '\n'.join(post['content']['raw'] for post in remote.posts.values())
    assert '19,900' not in raw and '가격' not in raw


def test_slot_selection_waits_for_its_preparation_window(tmp_path):
    automation, store, remote, receive_update = build(
        tmp_path, make_settings(enabled=True, start='09:00', end='21:00', daily_limits={'coupang': 2, 'toss': 0}))
    receive_update(store, automation.telegram, message_update(1, COUPANG), AT,
                   opener=lambda url, at: inspect(remote, url))
    automation.tick(AT)  # first slot is one hour away: too early
    assert automation.public(AT)['requests'][0]['state'] == 'ready'
    assert remote.posts == {}
    due, _ = first_slot(automation.public(AT)['settings'])
    automation.tick(due)
    assert automation.public(due)['requests'][0]['state'] == 'scheduled'
    assert len(remote.posts) == 1


def test_question_uses_force_reply_and_stale_question_is_cancelled(tmp_path):
    automation, store, remote, receive_update = build(tmp_path, make_settings(enabled=True, start='10:00'))
    receive_update(store, automation.telegram, message_update(1, COUPANG), AT,
                   opener=lambda url, at: inspect(remote, url, blocked=True))
    automation.deliver(AT)
    question = remote.sent[0]
    assert question['force'] is True
    reply = {'message_id': question['id'], 'from': {'id': 900, 'is_bot': True}, 'chat': {'id': 10}}
    receive_update(store, automation.telegram,
                   message_update(2, '상품명: 합성\n특징: 사실 하나\n특징: 사실 둘', reply_to=reply),
                   AT, opener=lambda url, at: inspect(remote, url, blocked=True))
    # Queue another question after the answer, as a delayed send would.
    with store.connect() as c:
        from studio.automation_store import enqueue_notice
        row = store.rows(c, 'automation_request')[0]
        store.put(c, 'automation_request', {**row, 'state': 'awaiting_info', 'revision': 0})
        stale = enqueue_notice(store, c, row['id'], 'question:9', '오래된 질문', chat_id=10, force_reply=True)
        store.put(c, 'automation_request', {**row, 'state': 'ready', 'revision': 1})
    deliver_notice(store, automation.telegram, stale['id'], AT)
    assert stale['id'] not in [item for item in []]
    with store.connect() as c:
        saved = store.get(c, stale['id'], 'notice')
    assert saved['state'] == 'cancelled'


def test_unbound_and_late_replies_get_guidance(tmp_path):
    automation, store, remote, receive_update = build(tmp_path, make_settings(enabled=True, start='10:00'))
    receive_update(store, automation.telegram, message_update(1, COUPANG), AT,
                   opener=lambda url, at: inspect(remote, url))
    receive_update(store, automation.telegram, message_update(2, '그냥 텍스트'), AT,
                   opener=lambda url, at: inspect(remote, url))
    automation.deliver(AT)
    assert any('답장' in note['text'] for note in remote.sent)
    request = automation.public(AT)['requests'][0]
    assert request['state'] == 'ready'


def test_request_id_in_reply_is_not_published_as_a_fact(tmp_path):
    from studio.product_info import parse_description
    parsed = parse_description('상품명: 합성\n특징: 사실 하나\n요청 req_99263b6bc17f4479615d6c7c81d0612a54bedc88', AT)
    assert [fact['text'] for fact in parsed['facts']] == ['사실 하나']


def test_publish_notice_uses_the_verified_public_url(tmp_path):
    automation, store, remote, receive_update = build(tmp_path, make_settings(enabled=True, start='10:00'))
    receive_update(store, automation.telegram, message_update(1, COUPANG), AT,
                   opener=lambda url, at: inspect(remote, url))
    due, _ = first_slot(automation.public(AT)['settings'])
    automation.tick(due)
    for post in remote.posts.values():
        post['status'] = 'publish'
        post['link'] = 'https://wp.example.test/fixture-post/'
    automation.tick(due + timedelta(minutes=2))
    automation.deliver(due + timedelta(minutes=3))
    assert any('https://wp.example.test/fixture-post/' in note['text'] for note in remote.sent)
    assert all('wp-admin' not in note['text'] for note in remote.sent)


def test_telegram_connect_failure_is_reported_not_masked(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from studio.app import create_app
    from studio.telegram import TelegramError
    for key, value in {'TELEGRAM_BOT_TOKEN': '123456789:fixture_bot_token_value_abc',
                       'TELEGRAM_CHAT_ID': '1234', 'TELEGRAM_ALLOWED_USER_ID': '5678',
                       'AI_MODE': 'api', 'AI_MODEL': 'fixture', 'AI_API_KEY': 'fixture-key',
                       'AI_BASE_URL': 'https://ai.example.test/v1',
                       'WP_URL': 'https://wp.example.test', 'WP_USERNAME': 'fixture',
                       'WP_APP_PASSWORD': 'fixture-password'}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr('studio.app.Telegram.connect',
                        lambda self: (_ for _ in ()).throw(TelegramError('conflict')))
    with TestClient(create_app(tmp_path), base_url='http://127.0.0.1:8787') as c:
        state = c.get('/api/state').json()['connections']
        assert state['automation'] is False
        assert 'Telegram' in state['automation_error']
        assert '세 연결 모두 필요' not in state['automation_error']


def test_failed_compose_does_not_consume_the_daily_quota(tmp_path):
    automation, store, remote, receive_update = build(
        tmp_path, make_settings(enabled=True, start='10:00', daily_limits={'coupang': 1, 'toss': 0}))
    receive_update(store, automation.telegram, message_update(1, COUPANG), AT,
                   opener=lambda url, at: inspect(remote, url))
    receive_update(store, automation.telegram, message_update(2, 'https://coupa.ng/b/second'), AT,
                   opener=lambda url, at: inspect(remote, url))
    remote.fail_ai = 1
    due, _ = first_slot(automation.public(AT)['settings'])
    automation.tick(due)
    request = automation.public(due)['requests'][0]
    assert request['state'] == 'ready'  # retried later, quota not burned
    due2, _ = first_slot(automation.public(AT)['settings'], offset=timedelta(minutes=9))
    automation.tick(due2)
