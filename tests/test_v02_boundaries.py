"""Safety regressions using real loopback transports, no site credentials."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import pytest
from studio.ai import LocalAI
from studio.core import now, Store
from test_ai import ai_server
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
