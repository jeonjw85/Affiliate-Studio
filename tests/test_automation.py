"""End-to-end automation: link in, verified WordPress publication, notice out."""
from datetime import datetime, timedelta, timezone
import json
import pytest

from automation_fixtures import message_update
from studio.core import Store

AT = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
COUPANG = 'https://link.coupang.com/a/fixture'
TOSS = 'https://toss.im/_m/fixture'
SITE = 'https://wp.example.test'


class Remote:
    """Synthetic Telegram + AI + WordPress servers, with request inspection."""
    def __init__(self):
        self.updates = []
        self.sent = []
        self.ai_requests = []
        self.wp_requests = []
        self.posts = {}
        self.next_post = 42
        self.next_bot_message = 500
        self.publish_status = 'future'
        self.fail_ai = 0

    def add(self, update):
        self.updates.append(update)


class FakeTelegram:
    chat_id = 10
    user_id = 10
    bot_id = 900
    def __init__(self, remote):
        self.remote = remote
    def updates(self, offset):
        return [update for update in self.remote.updates if update['update_id'] >= offset]
    def send(self, text, *, reply_to_message_id=None, force_reply=False):
        self.remote.next_bot_message += 1
        self.remote.sent.append({'text': text, 'reply_to': reply_to_message_id,
                                 'force': force_reply, 'id': self.remote.next_bot_message})
        return self.remote.next_bot_message


class FakeAI:
    mode = 'local'
    model = 'fixture-model'
    daily_limit = 10
    def __init__(self, remote):
        self.remote = remote
    def request(self, method, path, payload=None):
        self.remote.ai_requests.append(payload)
        if self.remote.fail_ai:
            self.remote.fail_ai -= 1
            raise ValueError('AI 생성 실패')
        count = len(json.loads(payload['messages'][1]['content'])['facts'])
        answer = {'title': '합성 상품 안내', 'body': '## 확인한 내용\n확인한 사실 1과 확인한 사실 2를 살펴보세요.',
                  'evidence_refs': [1] if count == 1 else [1, 2]}
        return {'choices': [{'message': {'content': json.dumps(answer)}, 'finish_reason': 'stop'}]}


class FakeWordPress:
    url = SITE
    def __init__(self, remote):
        self.remote = remote
    def request(self, method, suffix, payload=None, *, params=None):
        self.remote.wp_requests.append((method, suffix, payload))
        if method == 'GET' and suffix == '':
            slug = (params or {}).get('slug')
            return [dict(post) for post in self.remote.posts.values()
                    if slug is None or post.get('slug') == slug]
        if method == 'POST' and suffix == '':
            post_id = self.remote.next_post
            self.remote.next_post += 1
            self.remote.posts[post_id] = {'id': post_id, 'status': 'draft',
                                          'slug': payload['slug'], 'title': {'raw': payload['title']},
                                          'content': {'raw': payload['content']}, 'date_gmt': ''}
            return {'id': post_id, 'status': 'draft'}
        post_id = int(suffix.strip('/'))
        post = self.remote.posts[post_id]
        if method == 'POST':
            post.update(status=payload.get('status', post['status']),
                        date_gmt=payload.get('date_gmt', post['date_gmt']),
                        title={'raw': payload.get('title', post['title']['raw'])},
                        content={'raw': payload.get('content', post['content']['raw'])})
            return {'id': post_id, 'status': post['status']}
        return dict(post)
    def read(self, post_id):
        return dict(self.remote.posts[post_id])
    def verify_snapshot(self, data, snapshot):
        if data['title']['raw'] != snapshot['title'] or data['content']['raw'] != snapshot['html']:
            raise ValueError('원격 본문 불일치')
        return data


def build(tmp_path, settings=None):
    from studio.automation import Automation
    from studio.automation_store import get_settings, save_settings
    from automation_fixtures import make_settings
    from studio.telegram_inbox import receive_update
    remote = Remote()
    store = Store(tmp_path / 'data.db')
    automation = Automation(store, FakeTelegram(remote), FakeAI(remote), FakeWordPress(remote),
                            clock=lambda: AT)
    save_settings(store, settings or make_settings(enabled=True, start='10:00'),
                  get_settings(store, AT)['version'], AT)
    return automation, store, remote, receive_update


def prepare(automation, provider='coupang', at=AT):
    """Return the moment inside a slot's 15-to-5-minute preparation window."""
    from studio.automation_schedule import slots_for_day
    settings = automation.public(at)['settings']
    slot = next(slot for slot in slots_for_day(settings, at.date(), SITE)
                if slot['provider'] == provider)
    return datetime.fromisoformat(slot['run_at']) - timedelta(minutes=10), slot


def inspect(remote, url, *, blocked=False):
    if blocked:
        return {'provider': 'coupang' if 'coupang' in url else 'toss', 'affiliate_url': url,
                'outcome': 'needs_info',
                'evidence': {'name': '', 'facts': [], 'price': None, 'category': '', 'audience': '',
                             'source_url': None, 'product_key': None, 'conflicts': []}, 'reason': '403'}
    return {'provider': 'coupang' if 'coupang' in url else 'toss', 'affiliate_url': url,
            'outcome': 'ready',
            'evidence': {'name': '합성 상품', 'facts': [
                {'text': '확인한 사실 1', 'source': 'page', 'observed_at': AT.isoformat()},
                {'text': '확인한 사실 2', 'source': 'page', 'observed_at': AT.isoformat()}],
                'price': None, 'category': '', 'audience': '', 'source_url': None,
                'product_key': None, 'conflicts': []}, 'reason': ''}


def test_link_only_and_blocked_reply_publish_without_affiliate_keys(tmp_path):
    automation, store, remote, receive_update = build(tmp_path)
    receive_update(store, automation.telegram, message_update(1, COUPANG), AT, opener=lambda url, at: inspect(remote, url))
    received = receive_update(store, automation.telegram, message_update(2, TOSS), AT,
                              opener=lambda url, at: inspect(remote, url, blocked=True))
    waiting = automation.public(AT)
    assert waiting['counts']['coupang']['ready'] == 1
    assert waiting['counts']['toss']['awaiting_info'] == 1
    automation.deliver(AT)
    reply = {'message_id': remote.sent[-1]['id'], 'from': {'id': 900, 'is_bot': True},
             'chat': {'id': 10}}
    answer = message_update(3, '상품명: 토스 합성 상품\n특징: 확인한 사실 하나\n특징: 확인한 사실 둘',
                            reply_to=reply)
    receive_update(store, automation.telegram, answer, AT, opener=lambda url, at: inspect(remote, url, blocked=True))
    state = automation.public(AT)
    assert state['counts']['toss']['ready'] == 1
    due, _ = prepare(automation)
    automation.tick(due)
    automation.deliver(due)
    state = automation.public(AT)
    assert {row['provider']: row['state'] for row in state['requests']} == {
        'coupang': 'scheduled', 'toss': 'scheduled'}
    assert len(remote.posts) == 2
    assert all('원본 링크' not in json.dumps(payload) for payload in remote.ai_requests)
    assert 'affiliate_url' not in json.dumps(remote.ai_requests)
    assert remote.posts[42]['status'] == 'future'
    raw = '\n'.join(post['content']['raw'] for post in remote.posts.values())
    assert COUPANG in raw and TOSS in raw
    assert any('예약 확인' in note['text'] for note in remote.sent)
    with store.connect() as c:
        assert store.rows(c, 'draft') == []


def test_stop_resume_and_service_failure_are_independent(tmp_path):
    from studio.automation_store import save_settings
    from automation_fixtures import make_settings
    automation, store, remote, receive_update = build(tmp_path)
    receive_update(store, automation.telegram, message_update(1, COUPANG), AT, opener=lambda url, at: inspect(remote, url))
    receive_update(store, automation.telegram, message_update(2, TOSS), AT, opener=lambda url, at: inspect(remote, url))
    current = automation.public(AT)['settings']
    save_settings(store, make_settings(enabled=False), current['version'], AT)
    assert automation.tick(AT) == 0
    assert remote.posts == {}
    save_settings(store, make_settings(enabled=True), current['version'] + 1, AT)
    state = automation.public(AT)
    assert all(row['state'] == 'ready' for row in state['requests'])


def test_ai_failure_is_bounded_and_reported(tmp_path):
    automation, store, remote, receive_update = build(tmp_path)
    receive_update(store, automation.telegram, message_update(1, COUPANG), AT, opener=lambda url, at: inspect(remote, url))
    remote.fail_ai = 5
    due, _ = prepare(automation)
    for index in range(3):
        automation.tick(due + timedelta(minutes=index))
    state = automation.public(AT)
    # Bounded retries: a transient AI failure is retried, then stops.
    assert [row['state'] for row in state['requests']] == ['failed']
    assert state['requests'][0]['error']
    assert remote.posts == {}
    assert len(remote.ai_requests) == 3  # exactly the bounded three attempts


def test_publication_uses_the_services_own_slot_time(tmp_path):
    from automation_fixtures import make_settings
    from studio.automation_schedule import slots_for_day
    automation, store, remote, receive_update = build(
        tmp_path, make_settings(enabled=True, start='10:00', daily_limits={'coupang': 2, 'toss': 1}))
    receive_update(store, automation.telegram, message_update(1, COUPANG), AT,
                   opener=lambda url, at: inspect(remote, url))
    due, _ = prepare(automation)
    automation.tick(due)
    with store.connect() as c:
        publications = store.rows(c, 'automation_publication')
    assert len(publications) == 1
    coupang = slots_for_day(automation.public(AT)['settings'], AT.date(), SITE)
    coupang = next(slot for slot in coupang if slot['provider'] == 'coupang')
    assert publications[0]['run_at'] == coupang['run_at']


def test_past_slots_are_skipped_instead_of_failing_the_request(tmp_path):
    from automation_fixtures import make_settings
    automation, store, remote, receive_update = build(
        tmp_path, make_settings(enabled=True, start='10:00', daily_limits={'coupang': 2, 'toss': 0}))
    receive_update(store, automation.telegram, message_update(1, COUPANG), AT,
                   opener=lambda url, at: inspect(remote, url))
    # Inside the second slot's window: the 10:00 slot has already passed.
    from studio.automation_schedule import slots_for_day as _slots
    second = [slot for slot in _slots(automation.public(AT)['settings'], AT.date(), SITE)
              if slot['provider'] == 'coupang'][1]
    later = datetime.fromisoformat(second['run_at']) - timedelta(minutes=10)
    automation.tick(later)
    with store.connect() as c:
        rows = store.rows(c, 'automation_request')
        posts = store.rows(c, 'automation_publication')
    assert rows[0]['state'] == 'scheduled', rows[0].get('error')
    assert posts[0]['run_at'] == second['run_at']


def test_next_day_quota_resets_and_old_published_rows_do_not_count(tmp_path):
    from automation_fixtures import make_settings
    from studio.automation_store import get_settings, save_settings
    automation, store, remote, receive_update = build(tmp_path)
    save_settings(store, make_settings(enabled=True, start='00:00', daily_limits={'coupang': 1, 'toss': 0}),
                  get_settings(store, AT)['version'], AT)
    with store.connect() as c:
        store.put(c, 'automation_request', {
            'id': 'yesterday', 'provider': 'coupang', 'state': 'published',
            'slot_date': '2026-10-19', 'affiliate_url': 'https://link.coupang.com/a/old',
            'evidence': inspect(remote, COUPANG)['evidence'], 'version': 1,
            'created_at': '2026-10-19T00:00:00+00:00'})
    receive_update(store, automation.telegram, message_update(1, COUPANG), AT,
                   opener=lambda url, at: inspect(remote, url))
    automation.tick(AT + timedelta(minutes=45))
    states = {row['id']: row['state'] for row in automation.public(AT)['requests']}
    assert states['yesterday'] == 'published'
    assert states[[key for key in states if key != 'yesterday'][0]] == 'scheduled'


def test_daily_limits_create_expected_slots_and_zero_disables(tmp_path):
    from automation_fixtures import make_settings
    from studio.automation_store import save_settings
    automation, store, remote, receive_update = build(
        tmp_path, make_settings(enabled=True, daily_limits={'coupang': 3, 'toss': 0}))
    state = automation.public(AT)
    assert len([slot for slot in state['next_slots'] if slot['provider'] == 'coupang']) == 3
    assert [slot for slot in state['next_slots'] if slot['provider'] == 'toss'] == []
