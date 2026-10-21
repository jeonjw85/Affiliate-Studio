"""Durable versioned settings, exclusive claims and notification outbox."""
from datetime import date, datetime, timedelta, timezone
import pytest

from automation_fixtures import make_settings
from studio.core import Store

AT = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)


def test_settings_are_versioned_and_given_defaults(tmp_path):
    from studio.automation_store import get_settings, save_settings
    store = Store(tmp_path / 'data.db')
    settings = get_settings(store, AT)
    assert settings['version'] == 1
    assert settings['enabled'] is False
    assert settings['schedule']['daily_limits'] == {'coupang': 0, 'toss': 0}
    body = make_settings(enabled=True)
    saved = save_settings(store, body, settings['version'], AT)
    assert saved['version'] == 2 and saved['enabled'] is True
    with pytest.raises(ValueError):
        save_settings(store, body, settings['version'], AT)


def test_schedule_change_waits_for_next_local_day_and_enabled_is_immediate(tmp_path):
    from studio.automation_store import get_settings, save_settings
    store = Store(tmp_path / 'data.db')
    current = get_settings(store, AT)
    current = save_settings(store, make_settings(enabled=True), current['version'], AT)

    afternoon = datetime(2026, 10, 20, 14, 50, tzinfo=timezone.utc)
    changed = make_settings(enabled=False, daily_limits={'coupang': 5, 'toss': 2})
    saved = save_settings(store, changed, current['version'], afternoon)
    assert saved['enabled'] is False
    assert saved['schedule']['daily_limits'] == {'coupang': 1, 'toss': 1}
    # 14:50Z is 23:50 KST, so the next KST midnight is 15:00Z.
    assert saved['pending_schedule']['effective_at'] == '2026-10-20T15:00:00+00:00'
    # Before midnight the previous schedule still applies.
    active = get_settings(store, afternoon + timedelta(minutes=1))
    assert active['schedule']['daily_limits'] == {'coupang': 1, 'toss': 1}
    # At local midnight the pending schedule takes effect once.
    at_midnight = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
    active = get_settings(store, at_midnight)
    assert active['schedule']['daily_limits'] == {'coupang': 5, 'toss': 2}
    assert active['pending_schedule'] is None
    assert active['version'] == saved['version']


def test_writing_change_applies_to_new_work_immediately(tmp_path):
    from studio.automation_store import get_settings, save_settings
    store = Store(tmp_path / 'data.db')
    current = get_settings(store, AT)
    saved = save_settings(store, make_settings(writing={'tone': '짧은 말투'}), current['version'], AT)
    assert saved['writing']['tone'] == '짧은 말투'
    assert saved['pending_schedule'] is None


def test_setting_timezone_change_waits_for_existing_zone_midnight(tmp_path):
    from studio.automation_store import get_settings, save_settings
    store = Store(tmp_path / 'data.db')
    current = get_settings(store, AT)
    current = save_settings(store, make_settings(), current['version'], AT)
    changed = make_settings(timezone='America/New_York')
    saved = save_settings(store, changed, current['version'],
                          datetime(2026, 10, 20, 14, 50, tzinfo=timezone.utc))
    assert saved['pending_schedule']['effective_at'] == '2026-10-20T15:00:00+00:00'


def test_claim_is_exclusive_and_stale_token_cannot_finish(tmp_path):
    from studio.automation_store import claim, finish, get_optional, record_id
    path = tmp_path / 'data.db'
    store, other = Store(path), Store(path)
    item = {'id': record_id('req', 'a'), 'state': 'ready', 'version': 1}
    with store.connect() as c:
        store.put(c, 'automation_request', item)
    with store.connect() as c:
        claimed = claim(store, c, 'automation_request', item['id'], ('ready',), AT)
    assert claimed and claimed['_claim']
    with other.connect() as c:
        assert claim(other, c, 'automation_request', item['id'], ('ready',), AT) is None
    with other.connect() as c:
        assert claim(other, c, 'automation_request', item['id'], ('ready',), AT,
                     steal_after=AT + timedelta(hours=1)) is None  # owner process is alive
    with store.connect() as c:
        with pytest.raises(ValueError):
            finish(store, c, 'automation_request', item['id'], 'wrong-token', {'state': 'failed'})
        done = finish(store, c, 'automation_request', item['id'], claimed['_claim'], {'state': 'composing'})
    assert done['state'] == 'composing' and '_claim' not in done
    with store.connect() as c:
        assert get_optional(store, c, 'automation_request', item['id'])['state'] == 'composing'
        assert get_optional(store, c, 'automation_request', 'missing') is None


def test_notice_outbox_deduplicates_event_and_survives_restart(tmp_path):
    from studio.automation_store import enqueue_notice, record_id
    path = tmp_path / 'data.db'
    store = Store(path)
    for _ in range(2):
        with store.connect() as c:
            notice = enqueue_notice(store, c, 'req1', 'accepted', '접수', chat_id=10)
    assert notice['state'] == 'pending'
    with Store(path).connect() as c:
        rows = store.rows(c, 'notice')
    assert len(rows) == 1 and rows[0]['id'] == record_id('notice', 'req1', 'accepted')
    with store.connect() as c:
        system = enqueue_notice(store, c, None, 'start:10:1', '사용 방법', chat_id=10)
    assert system['request_id'] is None


def test_public_state_counts_by_service_and_hides_owners(tmp_path):
    from studio.automation_store import public_state
    store = Store(tmp_path / 'data.db')
    with store.connect() as c:
        store.put(c, 'automation_request', {'id': 'req_a', 'provider': 'coupang', 'state': 'published',
                                            'affiliate_url': 'https://link.coupang.com/a/fixture',
                                            '_claim': 'secret', '_pid': 1})
        store.put(c, 'automation_request', {'id': 'req_b', 'provider': 'toss', 'state': 'awaiting_info',
                                            'affiliate_url': 'https://toss.im/_m/fixture'})
    state = public_state(store, AT, target='https://wp.example.test')
    assert state['counts']['coupang']['published'] == 1
    assert state['counts']['toss']['awaiting_info'] == 1
    assert {r['id'] for r in state['requests']} == {'req_a', 'req_b'}
    assert all('_claim' not in r and '_pid' not in r for r in state['requests'])
    assert 'csrf' not in state and 'token' not in str(state).lower()
