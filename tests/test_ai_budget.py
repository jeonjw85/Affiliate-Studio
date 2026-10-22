"""Manual and automatic AI work share one durable daily budget."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import threading
import pytest

from studio.core import Store
from test_jobs import ready

AT = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)


def test_last_daily_credit_is_reserved_once_across_paths(tmp_path):
    from studio.ai import reserve_ai_operation
    s, p, d = ready(tmp_path)
    calls = []
    barrier = threading.Barrier(2)

    def reserve(subject):
        store = Store(s.path)
        barrier.wait()
        with store.connect() as c:
            return reserve_ai_operation(store, c, subject, 1, AT, scope='test')

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reserve, 'auto'), pool.submit(reserve, 'manual')]
    results = []
    for future in futures:
        try:
            results.append(future.result())
        except ValueError as error:
            results.append(error)
    assert sum(isinstance(item, dict) for item in results) == 1
    assert sum(isinstance(item, ValueError) for item in results) == 1


def test_manual_ai_records_have_no_draft_key_error(tmp_path, monkeypatch):
    """An automated operation row must not break the manual draft lookup."""
    from studio import ai as ai_module
    s, p, d = ready(tmp_path)
    with s.connect() as c:
        c.execute("INSERT OR REPLACE INTO records VALUES (?,?,?)", (
            'op_auto', 'operation', '{"id":"op_auto","type":"ai","scope":"automation","state":"completed","created_at":"%s"}' % AT.isoformat()))
    class Fake:
        daily_limit = 3
        model = 'fixture'
        def source(self, draft, product, style):
            return '{}'
        def generate(self, source, count):
            return {'title': '생성', 'body': '본문', 'evidence_refs': [1], 'body_format': 'markdown-v1'}
    # The manual path must not treat a completed automation row as running.
    candidate = s.generate_ai(d['id'], 1, 'guide', True, Fake())
    assert candidate['title'] == '생성'


def test_ai_usage_is_counted_per_utc_date(tmp_path):
    from studio.ai import reserve_ai_operation
    s = Store(tmp_path / 'data.db')
    next_day = datetime(2026, 10, 21, 0, 0, tzinfo=timezone.utc)
    with s.connect() as c:
        reserve_ai_operation(s, c, 'a', 1, AT, scope='test')
        with pytest.raises(ValueError):
            reserve_ai_operation(s, c, 'b', 1, AT, scope='test')
        assert reserve_ai_operation(s, c, 'c', 1, next_day, scope='test')['state'] == 'running'
