import importlib, json, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from test_jobs import ready
import pytest

class WPFixture(BaseHTTPRequestHandler):
    payload=None; requests=[]; fail=False
    def log_message(self,*args): pass
    def do_POST(self):
        type(self).requests.append(self.path)
        type(self).payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.fail:
            self.send_response(500); self.end_headers(); return
        self.send_response(201); self.send_header('Content-Type','application/json'); self.end_headers()
        self.wfile.write(json.dumps({'id':42,'status':'draft'}).encode())
    def do_GET(self):
        type(self).requests.append(self.path)
        self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers()
        self.wfile.write(json.dumps({'id':42,'status':'draft','title':{'raw':self.payload['title']},'content':{'raw':self.payload['content']}}).encode())

def test_real_local_wordpress_draft_transport(tmp_path):
    assert importlib.util.find_spec('studio.wordpress') is not None, 'WordPress 어댑터 미구현'
    from studio.wordpress import WordPress
    server=ThreadingHTTPServer(('127.0.0.1',0),WPFixture)
    WPFixture.requests=[]; WPFixture.fail=False
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        wp=WordPress(f'http://127.0.0.1:{server.server_port}','fixture-user','fixture-app-password',allow_local_test=True)
        s,p,d=ready(tmp_path); s.approve(d['id'],1,True)
        assert hasattr(s,'wordpress_draft'), '명시 승인 전송 미구현'
        result=s.wordpress_draft(d['id'],1,True,wp)
        assert result['state']=='completed' and result['remote_id']==42
        assert WPFixture.payload['status']=='draft'
        assert '쿠팡 파트너스' in WPFixture.payload['content']
        assert len(WPFixture.requests)==2
        again=s.wordpress_draft(d['id'],1,True,wp)
        assert again['id']==result['id'] and len(WPFixture.requests)==2
    finally:
        server.shutdown(); server.server_close()

def test_remote_failure_not_automatically_retried(tmp_path):
    assert importlib.util.find_spec('studio.wordpress') is not None
    from studio.wordpress import WordPress
    server=ThreadingHTTPServer(('127.0.0.1',0),WPFixture)
    WPFixture.requests=[]; WPFixture.fail=True
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        wp=WordPress(f'http://127.0.0.1:{server.server_port}','fixture-user','fixture-app-password',allow_local_test=True)
        s,p,d=ready(tmp_path); s.approve(d['id'],1,True)
        result=s.wordpress_draft(d['id'],1,True,wp)
        assert result['state']=='uncertain'
        assert 'fixture-app-password' not in json.dumps(result)
        assert s.wordpress_draft(d['id'],1,True,wp)['state']=='uncertain'
        assert len(WPFixture.requests)==1
    finally:
        WPFixture.fail=False; server.shutdown(); server.server_close()
