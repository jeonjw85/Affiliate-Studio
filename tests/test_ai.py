"""Loopback-only real HTTP tests; no actual model or paid service."""
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from test_jobs import ready


@contextmanager
def ai_server():
    class Handler(BaseHTTPRequestHandler):
        requests = []
        answer = {'title': '근거 기반 안내', 'body': 'USB 연결 조건을 확인하세요.', 'evidence_refs': [1]}
        hook = None
        def log_message(self, *args): pass
        def send(self, data):
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps(data).encode())
        def do_GET(self):
            self.requests.append((self.path, None))
            self.send({'data': [{'id': 'local-test'}]})
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.requests.append((self.path, payload))
            if type(self).hook: type(self).hook()
            self.send({'choices': [{'message': {'content': json.dumps(self.answer)}}]})
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try: yield f'http://127.0.0.1:{server.server_port}/v1', Handler
    finally: server.shutdown(); server.server_close(); thread.join()


def test_local_candidate_is_separate_then_apply_invalidates_approval(tmp_path):
    from studio.ai import LocalAI
    s, p, d = ready(tmp_path); s.approve(d['id'], 1, True)
    before = s.detail(d['id'])['draft']
    with ai_server() as (url, remote):
        ai = LocalAI(url, 'local-test')
        assert ai.check() == {'available': True, 'model': 'local-test', 'models': ['local-test'], 'error': ''}
        candidate = s.generate_ai(d['id'], 1, 'guide', True, ai)
        assert candidate['source_version'] == candidate['product_version'] == 1
        assert candidate['model'] == 'local-test' and candidate['evidence_refs'] == [1]
        assert s.detail(d['id'])['draft'] == before
        assert s.detail(d['id'])['candidates'] == [candidate]
        payload = remote.requests[-1][1]
        assert remote.requests[-1][0] == '/v1/chat/completions'
        assert payload['max_tokens'] <= 900 and payload['response_format']['type'] == 'json_schema'
        assert '/no_think' in json.dumps(payload)
        assert p['affiliate_url'] not in json.dumps(payload)
        applied = s.apply_candidate(d['id'], candidate['id'], 1)
        assert applied['version'] == 2 and applied['approved_at'] is None and applied['status'] == 'review'
        assert applied['title'] == candidate['title']
        with pytest.raises(ValueError): s.apply_candidate(d['id'], candidate['id'], 2)
