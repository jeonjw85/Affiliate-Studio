"""Automation HTTP surface: settings, status, detail, CSRF and privacy."""
from datetime import datetime, timedelta, timezone
import json
import pytest
from fastapi.testclient import TestClient

from automation_fixtures import make_settings
from studio.app import create_app

AT = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
ENV = {'TELEGRAM_BOT_TOKEN': '123456789:fixture_bot_token_value_abc',
       'TELEGRAM_CHAT_ID': '1234', 'TELEGRAM_ALLOWED_USER_ID': '5678'}


@pytest.fixture
def client(tmp_path, monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    with TestClient(create_app(tmp_path), base_url='http://127.0.0.1:8787') as c:
        c.headers['X-CSRF-Token'] = c.get('/api/state').json()['csrf']
        yield c


def test_settings_roundtrip_requires_csrf_and_version(client):
    state = client.get('/api/automation').json()
    settings = state['settings']
    assert settings['enabled'] is False
    assert settings['schedule']['daily_limits'] == {'coupang': 0, 'toss': 0}
    body = make_settings(enabled=True, daily_limits={'coupang': 10, 'toss': 5})
    assert client.put('/api/automation/settings', json=body).status_code == 200
    saved = client.get('/api/automation').json()['settings']
    assert saved['enabled'] is True
    assert saved['schedule']['daily_limits'] == {'coupang': 10, 'toss': 5}
    stale = client.put('/api/automation/settings', json=body)
    assert stale.status_code == 409
    assert client.put('/api/automation/settings', json=body,
                      headers={'X-CSRF-Token': ''}).status_code == 403
    for invalid in ({'enabled': True}, {**body, 'enabled': 'yes'},
                    {**body, 'schedule': {**body['schedule'], 'daily_limits': {'coupang': True, 'toss': 1}}}):
        assert client.put('/api/automation/settings', json=invalid).status_code == 400


def test_state_and_detail_do_not_leak_secrets(client):
    state = client.get('/api/automation').json()
    text = json.dumps(state)
    assert 'fixture_bot_token_value_abc' not in text
    assert 'csrf' not in text
    assert state['connections']['telegram'] is True
    assert state['connections']['affiliate_required'] is False
    response = client.get('/api/automation/requests/missing')
    assert response.status_code == 400
    assert 'fixture_bot_token_value_abc' not in response.text


def test_requests_and_counts_are_service_specific(tmp_path, monkeypatch):
    from studio.core import Store
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    with TestClient(create_app(tmp_path), base_url='http://127.0.0.1:8787') as c:
        state = c.get('/api/automation').json()
        store = c.app.state.store
        with store.connect() as conn:
            store.put(conn, 'automation_request', {
                'id': 'req_1', 'provider': 'coupang', 'state': 'scheduled',
                'affiliate_url': 'https://link.coupang.com/a/fixture',
                'evidence': {'name': '합성 상품', 'facts': []}})
            store.put(conn, 'automation_request', {
                'id': 'req_2', 'provider': 'toss', 'state': 'awaiting_info',
                'affiliate_url': 'https://toss.im/_m/fixture',
                'evidence': {'name': '', 'facts': []}})
        payload = c.get('/api/automation').json()
        assert payload['counts']['coupang']['scheduled'] == 1
        assert payload['counts']['toss']['awaiting_info'] == 1
        detail = c.get('/api/automation/requests/req_1')
        assert detail.status_code == 200
        assert detail.json()['request']['affiliate_url'] == 'https://link.coupang.com/a/fixture'


def test_update_retry_requires_connection(tmp_path, monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    with TestClient(create_app(tmp_path), base_url='http://127.0.0.1:8787') as c:
        c.headers['X-CSRF-Token'] = c.get('/api/state').json()['csrf']
        assert c.post('/api/automation/requests/req/update', json={}).status_code == 400
        assert c.post('/api/automation/requests/req/update', json={},
                      headers={'X-CSRF-Token': ''}).status_code == 403


def test_daily_quota_is_enforced_per_service(tmp_path):
    """Only as many requests as the service's daily limit may be composed."""
    from studio.core import Store
    from studio.automation import Automation
    from studio.automation_store import get_settings, save_settings
    from test_automation import FakeAI, FakeTelegram, FakeWordPress, Remote, inspect
    remote = Remote()
    store = Store(tmp_path / 'data.db')
    automation = Automation(store, FakeTelegram(remote), FakeAI(remote), FakeWordPress(remote),
                            clock=lambda: AT)
    save_settings(store, make_settings(enabled=True, start='10:00', daily_limits={'coupang': 1, 'toss': 0}),
                  get_settings(store, AT)['version'], AT)
    with store.connect() as c:
        for index in range(3):
            store.put(c, 'automation_request', {
                'id': f'req_{index}', 'provider': 'coupang', 'state': 'ready',
                'affiliate_url': f'https://link.coupang.com/a/fixture{index}',
                'evidence': inspect(remote, 'https://link.coupang.com/a/fixture')['evidence'],
                'version': 1, 'created_at': AT.isoformat()})
    from studio.automation_schedule import slots_for_day
    slot = next(slot for slot in slots_for_day(automation.public(AT)['settings'], AT.date(),
                                               automation.wordpress.url)
                if slot['provider'] == 'coupang')
    automation.tick(datetime.fromisoformat(slot['run_at']) - timedelta(minutes=10))
    states = [row['state'] for row in automation.public(AT)['requests']]
    assert states.count('scheduled') + states.count('checking') <= 1
    assert states.count('ready') == 2


def test_worker_lifecycle_does_not_block_startup(tmp_path, monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    with TestClient(create_app(tmp_path), base_url='http://127.0.0.1:8787') as c:
        state = c.get('/api/state')
        assert state.status_code == 200
        assert state.json()['worker_running'] is True
        assert 'affiliate' in state.json()['connections']
        automation = c.get('/api/automation').json()
        assert 'worker' in automation
        assert automation['worker']['running'] is True


def test_partial_telegram_config_does_not_break_startup(tmp_path, monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', '123456789:fixture_bot_token_value_abc')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', '')
    monkeypatch.delenv('TELEGRAM_ALLOWED_USER_ID', raising=False)
    with TestClient(create_app(tmp_path), base_url='http://127.0.0.1:8787') as c:
        state = c.get('/api/state').json()
        assert state['connections']['telegram'] is False
        assert 'Telegram' in state['connections']['telegram_error']
        assert 'fixture_bot_token_value_abc' not in json.dumps(state)
