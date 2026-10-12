import json
import threading
from contextlib import contextmanager
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from studio.core import now
from studio.wordpress import WordPress
from test_jobs import ready


@contextmanager
def wp_server():
    class Handler(BaseHTTPRequestHandler):
        post = {}; requests = []; corrupt = False; lose = False; hook = None
        def log_message(self, *args): pass
        def send(self, data, code=200):
            self.send_response(code); self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps(data).encode())
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.requests.append(('POST', self.path, payload))
            if type(self).hook: type(self).hook()
            self.post.update(payload)
            if self.lose:
                self.close_connection = True; return
            self.send({'id': 42, 'status': self.post['status']}, 201 if self.path.endswith('/posts') else 200)
        def do_GET(self):
            self.requests.append(('GET', self.path, None))
            self.send({'id': 42, 'status': self.post.get('status', 'draft'),
                       'title': {'raw': self.post.get('title', '')},
                       'content': {'raw': self.post.get('content', '') + ('<p>외부 수정</p>' if self.corrupt else '')},
                       'date_gmt': self.post.get('date_gmt', '')})
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try: yield WordPress(f'http://127.0.0.1:{server.server_port}', 'fixture', 'not-a-secret', allow_local_test=True), Handler
    finally: server.shutdown(); server.server_close(); thread.join()


def test_disclosure_alone_does_not_verify_remote_content(tmp_path):
    s, p, d = ready(tmp_path); s.approve(d['id'], 1, True)
    with wp_server() as (wp, remote):
        remote.corrupt = True
        result = s.wordpress_draft(d['id'], 1, True, wp)
        assert result['state'] == 'uncertain'


def test_schedule_readback_lock_and_cancel_lifecycle(tmp_path):
    s, p, d = ready(tmp_path); s.approve(d['id'], 1, True)
    due = (now() + timedelta(minutes=15)).isoformat()
    with wp_server() as (wp, remote):
        s.wordpress_draft(d['id'], 1, True, wp)
        publication = s.publish_schedule(d['id'], 1, due, True, wp)
        assert publication['state'] == 'future' and publication['remote_id'] == 42
        assert remote.post['status'] == 'future' and remote.post['date_gmt']
        assert s.detail(d['id'])['publication_lock'] is True
        with pytest.raises(ValueError): s.edit_draft(d['id'], 1, '새 제목', '본문')
        with pytest.raises(ValueError): s.update_product(p['id'], dict(p, name='수정'))
        with pytest.raises(ValueError): s.wordpress_draft(d['id'], 1, True, wp)
        count = len(remote.requests)
        assert s.publish_schedule(d['id'], 1, due, True, wp)['id'] == publication['id']
        assert len(remote.requests) == count
        assert s.refresh_publication(publication['id'], wp)['state'] == 'future'
        remote.post['status'] = 'draft'  # Explicit administrator action, never an app POST.
        count = len(remote.requests)
        cancelled = s.refresh_publication(publication['id'], wp)
        assert all(r[0] == 'GET' for r in remote.requests[count:])
        assert cancelled['state'] == 'cancelled' and remote.post['status'] == 'draft'
        assert not s.detail(d['id'])['publication_lock']
        assert s.refresh_publication(publication['id'], wp)['state'] == 'cancelled'
        assert s.state()['publications'][0]['state'] == 'cancelled'
        count = len(remote.requests)
        assert s.publish_schedule(d['id'], 1, due, True, wp)['id'] == publication['id']
        assert len(remote.requests) == count
        assert s.edit_draft(d['id'], 1, '새 제목', '본문')['version'] == 2


def test_cancel_fails_closed_without_post_even_if_wordpress_publishes(tmp_path):
    s, p, d = ready(tmp_path); s.approve(d['id'], 1, True)
    with wp_server() as (wp, remote):
        s.wordpress_draft(d['id'], 1, True, wp)
        pub = s.publish_schedule(d['id'], 1, (now() + timedelta(minutes=15)).isoformat(), True, wp)
        # Old implementation: future GET, then publish immediately before draft POST.
        remote.hook = lambda: remote.post.update(status='publish')
        for status in ('future', 'publish'):
            remote.post['status'] = status
            for confirmed in (True, False, None):
                count = len(remote.requests)
                posts = sum(r[0] == 'POST' for r in remote.requests)
                with pytest.raises(ValueError, match='WordPress 관리자.*원격 상태 조회'):
                    s.cancel_publication(pub['id'], confirmed, wp)
                assert len(remote.requests) == count
                assert sum(r[0] == 'POST' for r in remote.requests) == posts
                assert remote.post['status'] == status
        assert s.refresh_publication(pub['id'], wp)['state'] == 'publish'
        assert remote.requests[-1][0] == 'GET'
