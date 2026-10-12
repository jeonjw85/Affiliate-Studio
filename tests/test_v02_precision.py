from datetime import timedelta
import pytest
from studio.core import Store, now
from test_core import product
from test_jobs import ready
from test_publication import wp_server
from test_ai import ai_server
from studio.ai import LocalAI


@pytest.mark.parametrize('offset, allowed', [(timedelta(minutes=5), True), (timedelta(hours=24), True),
    (timedelta(hours=24, microseconds=1), False)])
def test_precise_time_and_price_boundaries(tmp_path, monkeypatch, offset, allowed):
    fixed = now().replace(microsecond=0)
    monkeypatch.setattr('studio.core.now', lambda: fixed)
    monkeypatch.setattr('studio.publication.now', lambda: fixed)
    s = Store(tmp_path / 'db'); p = s.add_product(product(observed_at=fixed.isoformat()))
    d = s.create_draft(p['id']); s.approve(d['id'], 1, True)
    with wp_server() as (wp, remote):
        s.wordpress_draft(d['id'], 1, True, wp); count = len(remote.requests)
        if allowed:
            assert s.publish_schedule(d['id'], 1, (fixed + offset).isoformat(), True, wp)['state'] == 'future'
        else:
            with pytest.raises(ValueError): s.publish_schedule(d['id'], 1, (fixed + offset).isoformat(), True, wp)
            assert len(remote.requests) == count


@pytest.mark.parametrize('mode', ['unapproved', 'missing_delivery', 'missing_confirmation', 'naive', 'stale', 'missing_version'])
def test_publication_prerequisites_do_not_touch_network(tmp_path, mode):
    s, p, d = ready(tmp_path)
    if mode != 'unapproved': s.approve(d['id'], 1, True)
    with wp_server() as (wp, remote):
        due = (now() + timedelta(minutes=15)).isoformat()
        if mode == 'naive': due = '2026-10-11T20:00:00'
        version = 2 if mode == 'stale' else None if mode == 'missing_version' else 1
        with pytest.raises(ValueError): s.publish_schedule(d['id'], version, due, mode != 'missing_confirmation', wp)
        assert remote.requests == []


def test_oversized_source_is_rejected_before_ai_request(tmp_path):
    s, p, d = ready(tmp_path)
    s.edit_draft(d['id'], 1, '제목', '가' * 6000)
    with ai_server() as (url, remote):
        with pytest.raises(ValueError, match='AI 입력 길이 초과'): s.generate_ai(d['id'], 2, 'guide', True, LocalAI(url, 'local-test'))
        assert remote.requests == []


def test_foreign_candidate_cannot_be_applied(tmp_path):
    s, p, d = ready(tmp_path)
    other = s.add_product(product(source_url='https://example.com/other')); other_d = s.create_draft(other['id'])
    with ai_server() as (url, remote):
        candidate = s.generate_ai(d['id'], 1, 'guide', True, LocalAI(url, 'local-test'))
        with pytest.raises(ValueError): s.apply_candidate(other_d['id'], candidate['id'], 1)
        assert s.detail(other_d['id'])['draft'] == other_d
