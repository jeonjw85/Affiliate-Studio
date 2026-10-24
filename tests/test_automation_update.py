"""Updating the one post in place: only from the last verified snapshot."""
from datetime import datetime, timedelta, timezone
import pytest

from automation_fixtures import make_evidence
from studio.core import Store
from test_automation import AT, COUPANG, FakeWordPress, Remote, SITE

from studio.exporting import DISCLOSURES
OLD = {'title': '이전 제목', 'html': '<p class="disclosure">' + DISCLOSURES['coupang']
       + '</p><h1>이전 제목</h1><p>이전 본문</p>',
       'provider': 'coupang', 'affiliate_url': COUPANG}
NEW = {'title': '새 제목', 'html': '<p class="disclosure">' + DISCLOSURES['coupang']
       + '</p><h1>새 제목</h1><p>새 본문</p>',
       'provider': 'coupang', 'affiliate_url': COUPANG}


def setup(tmp_path, state='published'):
    store = Store(tmp_path / 'data.db')
    remote = Remote()
    wp = FakeWordPress(remote)
    with store.connect() as c:
        store.put(c, 'automation_request', {'id': 'req', 'provider': 'coupang',
                                            'affiliate_url': COUPANG, 'evidence': make_evidence(),
                                            'state': state, 'target': SITE, 'version': 1})
        store.put(c, 'automation_article', {'id': 'article', 'request_id': 'req', 'title': OLD['title'],
                                            'html': OLD['html'], 'snapshot': OLD,
                                            'provider': 'coupang', 'affiliate_url': COUPANG})
        from studio.automation_store import record_id
        store.put(c, 'automation_publication', {
            'id': record_id('pub', 'req'), 'request_id': 'req', 'target': SITE, 'slug': 'affiliate-x',
            'remote_id': 42, 'remote_url': SITE + '/wp-admin/post.php?post=42&action=edit',
            'state': state, 'run_at': (AT + timedelta(hours=1)).isoformat(),
            'snapshot': OLD, 'pending_snapshot': None, 'checked_at': None, 'error': ''})
    remote.posts[42] = {'id': 42, 'status': 'publish' if state == 'published' else 'future',
                        'slug': 'affiliate-x', 'title': {'raw': OLD['title']},
                        'content': {'raw': OLD['html']},
                        'date_gmt': (AT + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S'),
                        'link': 'https://wp.example.test/fixture-post/'}
    return store, wp, remote


def test_update_public_post_in_place(tmp_path):
    from studio.automation_publish import update_snapshot
    store, wp, remote = setup(tmp_path)
    updated = update_snapshot(store, 'req', wp, NEW, AT)
    assert updated['remote_id'] == 42
    assert remote.posts[42]['status'] == 'publish'
    assert len([r for r in remote.wp_requests if r[0] == 'POST' and r[1] == '']) == 0
    assert remote.posts[42]['content']['raw'] == NEW['html']
    assert updated['snapshot'] == NEW
    assert updated['remote_url'] == 'https://wp.example.test/fixture-post/'


def test_update_keeps_scheduled_run_at(tmp_path):
    from studio.automation_publish import update_snapshot
    store, wp, remote = setup(tmp_path, state='scheduled')
    updated = update_snapshot(store, 'req', wp, NEW, AT)
    assert updated['state'] == 'scheduled'
    assert updated['run_at'] == (AT + timedelta(hours=1)).isoformat()
    assert remote.posts[42]['status'] == 'future'


def test_external_edit_blocks_update(tmp_path):
    from studio.automation_publish import SnapshotConflict, update_snapshot
    store, wp, remote = setup(tmp_path)
    remote.posts[42]['content']['raw'] += '<p>외부 수정</p>'
    with pytest.raises(SnapshotConflict):
        update_snapshot(store, 'req', wp, NEW, AT)
    assert [r for r in remote.wp_requests if r[0] == 'POST'] == []


def test_already_updated_snapshot_sends_no_request(tmp_path):
    from studio.automation_publish import update_snapshot
    store, wp, remote = setup(tmp_path)
    remote.posts[42]['title']['raw'] = NEW['title']
    remote.posts[42]['content']['raw'] = NEW['html']
    updated = update_snapshot(store, 'req', wp, NEW, AT)
    assert updated['snapshot'] == NEW
    assert [r for r in remote.wp_requests if r[0] == 'POST'] == []


def test_lost_update_response_is_idempotent(tmp_path):
    from studio.automation_publish import update_snapshot
    store, wp, remote = setup(tmp_path)
    original = wp.request
    lost = [True]
    def lossy(method, suffix, payload=None, *, params=None):
        result = original(method, suffix, payload, params=params)
        if lost[0] and method == 'POST' and suffix == '/42':
            lost[0] = False
            raise ValueError('WordPress 응답 확인 불가')
        return result
    wp.request = lossy
    # The write may have landed; the function must read back and finish.
    updated = update_snapshot(store, 'req', wp, NEW, AT)
    assert updated['snapshot'] == NEW
    assert len([r for r in remote.wp_requests if r[0] == 'POST' and r[1] == '/42']) == 1


def test_mixed_disclosures_are_all_verified(tmp_path):
    from studio.automation_publish import update_snapshot
    store, wp, remote = setup(tmp_path)
    multi = {'title': '혼합', 'html': '<p class="disclosure">' + DISCLOSURES['coupang']
             + '</p><p>' + DISCLOSURES['toss'] + '</p>',
             'disclosures': ['이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.',
                             '이 글에는 토스쇼핑 제휴 링크가 포함되어 있으며, 링크를 통한 구매 시 작성자가 수수료를 받을 수 있습니다.'],
             'provider': 'coupang', 'affiliate_url': COUPANG}
    updated = update_snapshot(store, 'req', wp, multi, AT)
    assert updated['snapshot'] == multi


def test_update_without_remote_post_is_rejected(tmp_path):
    from studio.automation_publish import update_snapshot
    store = Store(tmp_path / 'data.db')
    with store.connect() as c:
        store.put(c, 'automation_request', {'id': 'req', 'provider': 'coupang',
                                            'affiliate_url': COUPANG, 'evidence': make_evidence(),
                                            'state': 'checking', 'target': SITE})
    with pytest.raises(ValueError):
        update_snapshot(store, 'req', FakeWordPress(Remote()), NEW, AT)
