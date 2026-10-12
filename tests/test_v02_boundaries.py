"""Safety regressions using real loopback transports, no site credentials."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import pytest
from studio.ai import LocalAI
from studio.core import now, Store
from test_ai import ai_server
from test_publication import wp_server
from test_jobs import ready


@pytest.mark.parametrize('url', ['https://127.0.0.1/v1', 'http://192.168.1.2/v1', 'http://example.com/v1',
    'http://127.1/v1', 'http://127.0.0.1.evil.com/v1', 'http://u@localhost/v1',
    'http://@localhost/v1', 'http://localhost/v1?', 'http://localhost/v1#', 'http://localhost/v1?x=y'])
def test_ai_rejects_nonliteral_or_ambiguous_url(url):
    with pytest.raises(ValueError): LocalAI(url, 'test')


@pytest.mark.parametrize('answer', [None, [], {'title': 'x', 'body': 'y', 'evidence_refs': [True]},
    {'title': 'x', 'body': 'y', 'evidence_refs': [3]}, {'title': '', 'body': 'y', 'evidence_refs': [1]},
    {'title': 'x', 'body': 'y' * 8001, 'evidence_refs': [1]}])
def test_invalid_ai_output_never_changes_original_and_counts_attempt(tmp_path, answer):
    s, p, d = ready(tmp_path)
    with ai_server() as (url, remote):
        ai = LocalAI(url, 'local-test', daily_limit=1); remote.answer = answer
        with pytest.raises(ValueError): s.generate_ai(d['id'], 1, 'guide', True, ai)
        assert s.detail(d['id'])['draft'] == d
        assert s.detail(d['id'])['candidates'] == []
        with pytest.raises(ValueError, match='한도'): s.generate_ai(d['id'], 1, 'guide', True, ai)
        assert len(remote.requests) == 1


def test_ai_concurrent_claim_and_snapshot_change(tmp_path):
    s, p, d = ready(tmp_path)
    with ai_server() as (url, remote):
        ai = LocalAI(url, 'local-test')
        def while_generating():
            with pytest.raises(ValueError, match='진행'): s.generate_ai(d['id'], 1, 'guide', True, ai)
            s.update_product(p['id'], dict(p, name='새 이름'))
        remote.hook = while_generating
        with pytest.raises(ValueError, match='버전'): s.generate_ai(d['id'], 1, 'guide', True, ai)
        assert s.detail(d['id'])['candidates'] == [] and len(remote.requests) == 1


def test_draft_delivery_freezes_edit_and_product_during_network(tmp_path):
    s, p, d = ready(tmp_path); s.approve(d['id'], 1, True)
    with wp_server() as (wp, remote):
        def during_send():
            with pytest.raises(ValueError): s.edit_draft(d['id'], 1, '변경', '본문')
            with pytest.raises(ValueError): s.update_product(p['id'], dict(p, name='변경'))
            assert s.wordpress_draft(d['id'], 1, True, wp)['state'] == 'sending'
        remote.hook = during_send
        assert s.wordpress_draft(d['id'], 1, True, wp)['state'] == 'completed'
        assert len(remote.requests) == 2


@pytest.mark.parametrize('minutes', [-1, 4, 31 * 24 * 60, 25 * 60])
def test_invalid_schedule_time_or_price_expiry_never_sends(tmp_path, minutes):
    s, p, d = ready(tmp_path); s.approve(d['id'], 1, True)
    with wp_server() as (wp, remote):
        s.wordpress_draft(d['id'], 1, True, wp); count = len(remote.requests)
        with pytest.raises(ValueError):
            s.publish_schedule(d['id'], 1, (now() + timedelta(minutes=minutes)).isoformat(), True, wp)
        assert len(remote.requests) == count and s.state()['publications'] == []


def test_lost_schedule_response_no_retry_then_explicit_readback(tmp_path):
    s, p, d = ready(tmp_path); s.approve(d['id'], 1, True)
    with wp_server() as (wp, remote):
        s.wordpress_draft(d['id'], 1, True, wp); remote.lose = True
        due = (now() + timedelta(minutes=15)).isoformat()
        pub = s.publish_schedule(d['id'], 1, due, True, wp)
        assert pub['state'] == 'uncertain' and s.detail(d['id'])['publication_lock']
        count = len(remote.requests)
        assert s.publish_schedule(d['id'], 1, due, True, wp)['id'] == pub['id']
        assert len(remote.requests) == count
        remote.lose = False
        assert s.refresh_publication(pub['id'], wp)['state'] == 'future'
        wp.url += '/other-site'
        with pytest.raises(ValueError): s.cancel_publication(pub['id'], True, wp)
        with pytest.raises(ValueError, match='목적지'): s.refresh_publication(pub['id'], wp)
        assert len(remote.requests) == count + 1


def test_cancel_never_unpublishes_and_historical_snapshot_is_immutable(tmp_path):
    s, p, d = ready(tmp_path); s.approve(d['id'], 1, True)
    with wp_server() as (wp, remote):
        s.wordpress_draft(d['id'], 1, True, wp)
        pub = s.publish_schedule(d['id'], 1, (now() + timedelta(minutes=15)).isoformat(), True, wp)
        remote.post['status'] = 'publish'; count = len(remote.requests)
        with pytest.raises(ValueError, match='WordPress 관리자'): s.cancel_publication(pub['id'], True, wp)
        assert len(remote.requests) == count and remote.post['status'] == 'publish'
        assert s.refresh_publication(pub['id'], wp)['state'] == 'publish'
        assert len(remote.requests) == count + 1 and remote.requests[-1][0] == 'GET'
        s.edit_draft(d['id'], 1, '로컬 새 글', '내용')
        assert s.refresh_publication(pub['id'], wp)['state'] == 'publish'


def test_mutations_claimed_before_remote_and_manual_change_is_uncertain(tmp_path):
    s, p, d = ready(tmp_path); s.approve(d['id'], 1, True)
    with wp_server() as (wp, remote):
        s.wordpress_draft(d['id'], 1, True, wp)
        due = (now() + timedelta(minutes=15)).isoformat()
        def during_schedule():
            pub = s.state()['publications'][0]
            assert s.publish_schedule(d['id'], 1, due, True, wp)['id'] == pub['id']
            with pytest.raises(ValueError): s.cancel_publication(pub['id'], True, wp)
            with pytest.raises(ValueError): s.refresh_publication(pub['id'], wp)
        remote.hook = during_schedule
        pub = s.publish_schedule(d['id'], 1, due, True, wp)
        assert pub['state'] == 'future'
        remote.hook = None; remote.corrupt = True
        assert s.refresh_publication(pub['id'], wp)['state'] == 'uncertain'
        count = len(remote.requests)
        with pytest.raises(ValueError, match='WordPress 관리자'): s.cancel_publication(pub['id'], True, wp)
        assert len(remote.requests) == count


def test_explicit_refresh_can_recover_claim_of_dead_process(tmp_path):
    s, p, d = ready(tmp_path); s.approve(d['id'], 1, True)
    with wp_server() as (wp, remote):
        s.wordpress_draft(d['id'], 1, True, wp)
        pub = s.publish_schedule(d['id'], 1, (now() + timedelta(minutes=15)).isoformat(), True, wp)
        with s.connect() as c:
            record = s.get(c, pub['id'], 'publication')
            record.update(state='sending', _claim='interrupted', _pid=2147483647)
            s.put(c, 'publication', record)
        fresh = Store(s.path)
        assert fresh.state()['publications'][0]['state'] == 'uncertain'
        assert fresh.detail(d['id'])['publication_lock']
        assert fresh.refresh_publication(pub['id'], wp)['state'] == 'future'
        assert sum(r[0] == 'POST' for r in remote.requests) == 2


def test_fresh_refresh_detects_manually_reactivated_cancelled_post(tmp_path):
    s, p, d = ready(tmp_path); s.approve(d['id'], 1, True)
    with wp_server() as (wp, remote):
        s.wordpress_draft(d['id'], 1, True, wp)
        pub = s.publish_schedule(d['id'], 1, (now() + timedelta(minutes=15)).isoformat(), True, wp)
        remote.post['status'] = 'draft'  # Explicit WordPress administrator action.
        count = len(remote.requests)
        assert s.refresh_publication(pub['id'], wp)['state'] == 'cancelled'
        assert all(r[0] == 'GET' for r in remote.requests[count:])
        remote.post['status'] = 'future'
        assert s.refresh_publication(pub['id'], wp)['state'] == 'uncertain'
        assert s.detail(d['id'])['publication_lock']
