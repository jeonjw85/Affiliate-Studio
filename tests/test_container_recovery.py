"""A recreated container can reuse the previous application's process ID."""
import pytest

from studio.ai import LocalAI
from studio.core import Store, now
from studio import publication
from test_jobs import ready
from test_publication import wp_server
from datetime import timedelta


@pytest.mark.parametrize('kind, active, recovered', [
    ('operation', 'running', 'failed'),
    ('delivery', 'sending', 'uncertain'),
    ('publication', 'sending', 'uncertain'),
])
def test_restart_with_reused_pid_recovers_interrupted_claims(tmp_path, monkeypatch, kind, active, recovered):
    store, p, draft = ready(tmp_path)
    store.approve(draft['id'], 1, True)
    def interrupted(*args, **kwargs):
        raise SystemExit('Fixture: process stopped during network request')
    with wp_server() as (wp, remote):
        if kind == 'publication':
            store.wordpress_draft(draft['id'], 1, True, wp)
        monkeypatch.setattr(wp, 'request', interrupted)
        with pytest.raises(SystemExit):
            if kind == 'operation':
                ai = LocalAI('http://127.0.0.1:11434/v1', 'fixture')
                monkeypatch.setattr(ai, 'request', interrupted)
                store.generate_ai(draft['id'], 1, 'guide', True, ai)
            elif kind == 'delivery':
                store.wordpress_draft(draft['id'], 1, True, wp)
            else:
                store.publish_schedule(draft['id'], 1, (now() + timedelta(minutes=15)).isoformat(), True, wp)
        live = Store(store.path)
        with live.connect() as c:
            record = live.rows(c, kind)[0]
        assert record['state'] == active, 'An active process must retain its claim'
        calls = len(remote.requests)
        # A fresh process has a different instance token, even when its PID is reused.
        monkeypatch.setattr(publication, 'PROCESS_OWNER', 'new-process-instance', raising=False)
        restarted = Store(store.path)
        with restarted.connect() as c:
            record = restarted.rows(c, kind)[0]
        assert record['state'] == recovered
        assert len(remote.requests) == calls, 'Recovery must not repeat a remote mutation'
