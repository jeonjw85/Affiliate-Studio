"""Automatic publication: one draft per request, readback-first recovery."""
from datetime import datetime, timedelta, timezone
import json
import pytest

from studio.core import Store
from studio.exporting import DISCLOSURES

AT = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
TARGET = 'https://wp.example.test'
SNAPSHOT = {'title': '합성 제목',
            'html': '<p class="disclosure">' + DISCLOSURES['coupang'] + '</p>'
                    '<p>본문 <a href="https://link.coupang.com/a/fixture">링크</a></p>',
            'provider': 'coupang', 'affiliate_url': 'https://link.coupang.com/a/fixture'}


class FakeWordPress:
    url = TARGET

    def __init__(self):
        self.posts = {}
        self.creates = 0
        self.updates = []
        self.reads = []
        self.next_id = 42
        self.lose_create = False
        self.corrupt = False
        self.fail_read = False

    def request(self, method, suffix, payload=None, *, params=None):
        if method == 'POST' and suffix == '':
            self.creates += 1
            post_id = self.next_id
            self.posts[post_id] = {'id': post_id, 'status': 'draft', 'slug': payload.get('slug'),
                                   'title': {'raw': payload['title']},
                                   'content': {'raw': payload['content']}, 'date_gmt': ''}
            if self.lose_create:
                raise ValueError('WordPress 응답 확인 불가')
            return {'id': post_id, 'status': 'draft'}
        if method == 'POST':
            post_id = int(suffix.strip('/'))
            self.updates.append((post_id, payload))
            self.posts[post_id].update(status=payload.get('status', self.posts[post_id]['status']),
                                       date_gmt=payload.get('date_gmt', self.posts[post_id]['date_gmt']),
                                       title={'raw': payload.get('title', self.posts[post_id]['title']['raw'])},
                                       content={'raw': payload.get('content', self.posts[post_id]['content']['raw'])})
            return {'id': post_id, 'status': self.posts[post_id]['status']}
        assert method == 'GET'
        self.reads.append((suffix, params))
        if self.fail_read:
            raise ValueError('WordPress 응답 확인 불가')
        if suffix == '':
            slug = (params or {}).get('slug')
            return [dict(post, content={
                'raw': post['content']['raw'] + ('<p>외부 수정</p>' if self.corrupt else '')})
                for post in self.posts.values() if slug is None or post.get('slug') == slug]
        post = self.posts[int(suffix.strip('/'))]
        return dict(post, content={'raw': post['content']['raw'] + ('<p>외부 수정</p>' if self.corrupt else '')})

    def read(self, post_id):
        return self.request('GET', '/' + str(post_id))

    def verify_snapshot(self, data, snapshot):
        if data['title']['raw'] != snapshot['title'] or data['content']['raw'] != snapshot['html']:
            raise ValueError('원격 제목·본문이 스냅샷과 다름')
        return data


def make_request(store, **changes):
    row = {'id': 'req_fixture', 'provider': 'coupang', 'affiliate_url': SNAPSHOT['affiliate_url'],
           'state': 'checking', 'version': 1, 'target': TARGET, 'created_at': AT.isoformat()}
    row.update(changes)
    with store.connect() as c:
        store.put(c, 'automation_request', row)
    return row


def make_article(store, request_id='req_fixture', snapshot=None):
    row = {'id': 'article_fixture', 'request_id': request_id, 'title': (snapshot or SNAPSHOT)['title'],
           'html': (snapshot or SNAPSHOT)['html'], 'snapshot': snapshot or SNAPSHOT}
    with store.connect() as c:
        store.put(c, 'automation_article', row)
    return row


def test_one_post_for_repeated_ensure_and_schedule(tmp_path):
    from studio.automation_publish import ensure_draft, schedule_post
    store = Store(tmp_path / 'data.db')
    make_request(store)
    make_article(store)
    wp = FakeWordPress()
    draft = ensure_draft(store, 'req_fixture', wp, AT)
    assert draft['remote_id'] == 42 and draft['state'] == 'draft'
    assert ensure_draft(store, 'req_fixture', wp, AT)['remote_id'] == 42
    assert wp.creates == 1
    scheduled = schedule_post(store, 'req_fixture', wp, AT + timedelta(hours=1), AT)
    assert scheduled['state'] == 'scheduled'
    assert schedule_post(store, 'req_fixture', wp, AT + timedelta(hours=1), AT)['state'] == 'scheduled'
    assert len(wp.updates) == 1
    wp.posts[42]['status'] = 'publish'
    published = ensure_draft(store, 'req_fixture', wp, AT + timedelta(hours=2))
    assert published['state'] == 'published' or published['state'] == 'scheduled'


def test_create_response_loss_reconciles_by_slug(tmp_path):
    from studio.automation_publish import ensure_draft
    store = Store(tmp_path / 'data.db')
    make_request(store)
    make_article(store)
    wp = FakeWordPress()
    wp.lose_create = True
    first = ensure_draft(store, 'req_fixture', wp, AT)
    assert first['state'] == 'uncertain'
    assert 'remote_id' not in first or first['remote_id'] is None
    assert wp.creates == 1
    recovered = ensure_draft(store, 'req_fixture', wp, AT + timedelta(minutes=1))
    assert recovered['state'] == 'draft' and recovered['remote_id'] == 42
    assert wp.creates == 1
    assert any(params and params.get('slug') for suffix, params in wp.reads if suffix == '')


def test_modified_remote_body_is_uncertain_and_never_recreated(tmp_path):
    from studio.automation_publish import ensure_draft
    store = Store(tmp_path / 'data.db')
    make_request(store)
    make_article(store)
    wp = FakeWordPress()
    ensure_draft(store, 'req_fixture', wp, AT)
    wp.corrupt = True
    result = ensure_draft(store, 'req_fixture', wp, AT + timedelta(minutes=1))
    assert result['state'] == 'uncertain'
    assert wp.creates == 1


def test_id_is_persisted_before_readback(tmp_path):
    from studio.automation_publish import ensure_draft
    store = Store(tmp_path / 'data.db')
    make_request(store)
    make_article(store)
    wp = FakeWordPress()
    original_read = wp.read
    calls = [0]
    def failing_read(post_id):
        calls[0] += 1
        if calls[0] == 1:
            raise ValueError('WordPress 응답 확인 불가')
        return original_read(post_id)
    wp.read = failing_read
    first = ensure_draft(store, 'req_fixture', wp, AT)
    assert first['state'] == 'uncertain' and first.get('remote_id') == 42
    with store.connect() as c:
        assert store.rows(c, 'automation_publication')[0]['remote_id'] == 42
    assert ensure_draft(store, 'req_fixture', wp, AT + timedelta(minutes=5))['state'] == 'draft'
    assert wp.creates == 1


def test_rollover_removes_expired_price_from_same_draft(tmp_path):
    from studio.automation_publish import ensure_draft, replace_draft_snapshot
    store = Store(tmp_path / 'data.db')
    priced = dict(SNAPSHOT, html=SNAPSHOT['html'] + '<p>19,900원</p>')
    make_request(store)
    make_article(store, snapshot=priced)
    wp = FakeWordPress()
    draft = ensure_draft(store, 'req_fixture', wp, AT)
    cheaper = dict(SNAPSHOT)
    updated = replace_draft_snapshot(store, 'req_fixture', wp, cheaper, AT + timedelta(days=1))
    assert updated['remote_id'] == 42 and updated['state'] == 'draft'
    assert '19,900원' not in wp.posts[42]['content']['raw']
    assert all(post_id == 42 for post_id, _ in wp.updates)
    assert wp.creates == 1


def test_public_post_or_site_change_prevents_mutation(tmp_path):
    from studio.automation_publish import ensure_draft, schedule_post
    store = Store(tmp_path / 'data.db')
    make_request(store)
    make_article(store)
    wp = FakeWordPress()
    ensure_draft(store, 'req_fixture', wp, AT)
    wp.posts[42]['status'] = 'publish'
    published = schedule_post(store, 'req_fixture', wp, AT + timedelta(hours=1), AT)
    assert published['state'] == 'published'
    assert wp.updates == []  # a public post is never turned back into a future one

    class Other(FakeWordPress):
        url = 'https://other.example.test'
    with pytest.raises(ValueError):
        ensure_draft(store, 'req_fixture', Other(), AT)


def test_slug_and_schedule_are_stable_across_days(tmp_path):
    from studio.automation_publish import ensure_draft, schedule_post
    store = Store(tmp_path / 'data.db')
    make_request(store)
    make_article(store)
    wp = FakeWordPress()
    first = ensure_draft(store, 'req_fixture', wp, AT)
    again = ensure_draft(store, 'req_fixture', wp, AT + timedelta(days=2))
    assert first['slug'] == again['slug']
    assert schedule_post(store, 'req_fixture', wp, AT + timedelta(days=1), AT)['state'] == 'scheduled'
