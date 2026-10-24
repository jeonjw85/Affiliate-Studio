"""Automation wiring for multi-product posts and late updates."""
from datetime import datetime, timedelta, timezone
import pytest

from automation_fixtures import make_settings, message_update
from studio.automation_schedule import slots_for_day
from studio.core import Store
from test_automation import AT, COUPANG, FakeAI, FakeTelegram, FakeWordPress, Remote, build, inspect, prepare

TOSS = 'https://toss.im/_m/fixture'


def ready(url, at, name=''):
    result = inspect(Remote(), url)
    if name:
        result['evidence']['name'] = name
    return result


def test_mixed_providers_consume_one_slot_each(tmp_path):
    automation, store, remote, receive_update = build(
        tmp_path, make_settings(enabled=True, start='10:00', daily_limits={'coupang': 1, 'toss': 1}))
    receive_update(store, automation.telegram, message_update(1, f'{COUPANG} {TOSS}'), AT,
                   opener=lambda url, at: inspect(remote, url))
    due, _ = prepare(automation)
    automation.tick(due)
    state = automation.public(due)
    request = next(row for row in state['requests'] if row.get('products'))
    assert request['state'] == 'scheduled', request.get('error')
    assert state['counts']['coupang']['scheduled'] == 1
    assert state['counts']['toss']['scheduled'] == 1
    assert len(remote.posts) == 1


def test_service_without_quota_defers_the_whole_post(tmp_path):
    automation, store, remote, receive_update = build(
        tmp_path, make_settings(enabled=True, start='10:00', daily_limits={'coupang': 1, 'toss': 0}))
    receive_update(store, automation.telegram, message_update(1, f'{COUPANG} {TOSS}'), AT,
                   opener=lambda url, at: inspect(remote, url))
    due, _ = prepare(automation)
    automation.tick(due)
    assert automation.public(due)['requests'][0]['state'] == 'ready'
    assert remote.posts == {}


def test_partial_post_publishes_ready_products_only(tmp_path):
    automation, store, remote, receive_update = build(tmp_path, make_settings(enabled=True, start='10:00'))
    def opener(url, at):
        result = inspect(remote, url)
        if url == TOSS:
            result['outcome'] = 'needs_info'
            result['evidence'] = dict(result['evidence'], name='', facts=[])
        return result
    receive_update(store, automation.telegram, message_update(1, f'{COUPANG} {TOSS}'), AT, opener=opener)
    due, _ = prepare(automation)
    automation.tick(due)
    raw = next(iter(remote.posts.values()))['content']['raw']
    assert COUPANG in raw and TOSS not in raw
    request = next(row for row in automation.public(due)['requests'] if row.get('products'))
    assert request['state'] == 'scheduled'
    assert [p['state'] for p in request['products']] == ['ready', 'awaiting_info']


def test_late_description_updates_published_post(tmp_path):
    automation, store, remote, receive_update = build(tmp_path, make_settings(enabled=True, start='10:00'))
    def opener(url, at):
        result = inspect(remote, url)
        if url == TOSS:
            result['outcome'] = 'needs_info'
            result['evidence'] = dict(result['evidence'], name='', facts=[])
        return result
    request_id = receive_update(store, automation.telegram,
                                message_update(1, f'{COUPANG} {TOSS}'), AT, opener=opener)
    due, _ = prepare(automation)
    automation.tick(due)
    post = next(iter(remote.posts.values()))
    post['status'] = 'publish'
    post['link'] = 'https://wp.example.test/fixture-post/'
    automation.tick(due + timedelta(minutes=2))
    automation.deliver(due + timedelta(minutes=2))
    question = next(note for note in remote.sent if '상품명' in note['text'])
    reply = {'message_id': question['id'], 'from': {'id': 900, 'is_bot': True}, 'chat': {'id': 10}}
    receive_update(store, automation.telegram,
                   message_update(2, '상품명: 토스 상품\n특징: 토스 사실 하나', reply_to=reply),
                   AT, opener=opener)
    request = next(row for row in automation.public(due)['requests'] if row.get('products'))
    assert request['update_state'] == 'updating'
    automation.tick(due + timedelta(minutes=3))
    assert len(remote.posts) == 1
    updated = remote.posts[post['id']]
    assert updated['status'] == 'publish'
    assert '토스 사실 하나' in updated['content']['raw']
    assert TOSS in updated['content']['raw']
    request = next(row for row in automation.public(due)['requests'] if row.get('products'))
    assert request['update_state'] == 'updated'


def test_blocked_update_is_not_retried_automatically(tmp_path):
    automation, store, remote, receive_update = build(tmp_path, make_settings(enabled=True, start='10:00'))
    def opener(url, at):
        result = inspect(remote, url)
        if url == TOSS:
            result['outcome'] = 'needs_info'
            result['evidence'] = dict(result['evidence'], name='', facts=[])
        return result
    receive_update(store, automation.telegram, message_update(1, f'{COUPANG} {TOSS}'), AT, opener=opener)
    due, _ = prepare(automation)
    automation.tick(due)
    automation.deliver(due)
    post = next(iter(remote.posts.values()))
    post['status'] = 'publish'
    post['content']['raw'] += '<p>외부 수정</p>'
    automation.tick(due + timedelta(minutes=2))
    question = next(note for note in remote.sent if '상품명' in note['text'])
    reply = {'message_id': question['id'], 'from': {'id': 900, 'is_bot': True}, 'chat': {'id': 10}}
    receive_update(store, automation.telegram,
                   message_update(2, '상품명: 토스 상품\n특징: 토스 사실 하나', reply_to=reply),
                   AT, opener=opener)
    automation.tick(due + timedelta(minutes=3))
    request = next(row for row in automation.public(due)['requests'] if row.get('products'))
    assert request['update_state'] == 'update_blocked'
    posts = len([r for r in remote.wp_requests if r[0] == 'POST'])
    automation.tick(due + timedelta(minutes=30))
    assert len([r for r in remote.wp_requests if r[0] == 'POST']) == posts


def test_single_link_still_publishes_one_product(tmp_path):
    automation, store, remote, receive_update = build(tmp_path, make_settings(enabled=True, start='10:00'))
    receive_update(store, automation.telegram, message_update(1, COUPANG), AT,
                   opener=lambda url, at: inspect(remote, url))
    due, _ = prepare(automation)
    automation.tick(due)
    posts = list(remote.posts.values())
    assert len(posts) == 1
    assert '비교표' not in posts[0]['content']['raw']
    assert COUPANG in posts[0]['content']['raw']


def test_update_retry_requires_blocked_state(tmp_path):
    automation, store, remote, receive_update = build(tmp_path, make_settings(enabled=True, start='10:00'))
    receive_update(store, automation.telegram, message_update(1, COUPANG), AT,
                   opener=lambda url, at: inspect(remote, url))
    request_id = automation.public(AT)['requests'][0]['id']
    with pytest.raises(ValueError):
        automation.retry_update(request_id)
