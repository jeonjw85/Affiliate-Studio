"""Opt-in Docker integration check; disposable volumes and a synthetic AI server."""
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import uuid
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = '''import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def reply(self, value):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(value).encode())
    def do_GET(self):
        assert self.path == '/v1/models'
        assert 'Authorization' not in self.headers
        self.reply({'data': [{'id': 'fixture-model'}]})
    def do_POST(self):
        assert self.path == '/v1/chat/completions'
        assert 'Authorization' not in self.headers
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert body['model'] == 'fixture-model'
        assert body['response_format']['type'] == 'json_schema'
        content = {'title': '확인한 연결 조건', 'body': 'USB 연결 조건을 확인하세요.', 'evidence_refs': [1]}
        self.reply({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(content)}}]})
ThreadingHTTPServer(('0.0.0.0', 11434), Handler).serve_forever()
'''


def main():
    project_name = 'affiliate-smoke-' + uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix=project_name + '-') as temp:
        project = Path(temp)
        for name in ('Dockerfile', 'compose.yaml', '.dockerignore', 'requirements.lock', 'run.py'):
            shutil.copy2(ROOT / name, project / name)
        shutil.copytree(ROOT / 'studio', project / 'studio', ignore=shutil.ignore_patterns('__pycache__'))
        (project / 'fixture.py').write_text(FIXTURE)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        (project / '.env').write_text(
            f'STUDIO_ALLOWED_HOSTS=nas.fixture\nSTUDIO_PORT={port}\n'
            "AI_MODE=local\nAI_MODEL=fixture-model\nAI_BASE_URL=http://ollama:11434/v1\n"
            "AI_LOCAL_HOSTS=ollama\nAI_API_KEY='fixture-${DO_NOT_EXPAND}#key'\n"
            'WP_URL=\nWP_USERNAME=\nWP_APP_PASSWORD=\n')
        (project / 'fixture-compose.json').write_text(json.dumps({'services': {'ollama': {
            'image': 'python:3.12-slim', 'entrypoint': ['python', '/fixture.py'],
            'volumes': [f'{project / "fixture.py"}:/fixture.py:ro'],
        }}}))
        env = {k: v for k, v in os.environ.items() if not k.startswith(('AI_', 'WP_', 'STUDIO_', 'COMPOSE_'))}
        compose = ['docker', 'compose', '-p', project_name, '-f', str(project / 'compose.yaml'),
                   '-f', str(project / 'fixture-compose.json'), '--profile', 'local-ai']

        def run(*args, check=True):
            result = subprocess.run([*compose, *args], cwd=project, env=env, text=True,
                                    capture_output=True, timeout=240)
            if check and result.returncode:
                raise AssertionError(result.stdout + result.stderr)
            return result.stdout

        def fetch(path='/api/state', body=None, token='', host='nas.fixture'):
            request = urllib.request.Request(f'http://127.0.0.1:{port}' + path,
                data=json.dumps(body).encode() if body is not None else None,
                headers={'Host': f'{host}:{port}', 'Origin': f'http://{host}:{port}',
                         'Content-Type': 'application/json', 'X-CSRF-Token': token})
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read()
                return json.loads(raw) if response.headers.get_content_type() == 'application/json' else raw

        try:
            run('up', '-d', '--build', '--wait', '--wait-timeout', '90')
            state = fetch()
            assert state['worker_running'] and state['connections']['ai_mode'] == 'local'
            assert state['connections']['ai_base_url'] == 'http://ollama:11434/v1'
            assert b'Affiliate Studio' in fetch('/')
            try:
                fetch(host='unlisted.fixture')
                raise AssertionError('Unlisted host was accepted')
            except urllib.error.HTTPError as error:
                assert error.code == 403
            token = state['csrf']
            assert fetch('/api/ai/check', {}, token)['available']
            product = fetch('/api/products', dict(provider='coupang', name='Docker 검증용 상품',
                source_url='https://example.com/product', affiliate_url='https://example.com/affiliate',
                price=None, observed_at=datetime.now(timezone.utc).isoformat(), facts=['USB 연결'],
                audience='테스트 독자', caveats='합성 테스트 자료', ownership_confirmed=True), token)
            draft = fetch('/api/drafts', {'product_id': product['id']}, token)
            path = '/api/drafts/' + draft['id']
            candidate = fetch(path + '/ai', {'version': 1, 'style': 'guide', 'confirmed_send': True}, token)
            assert candidate['title'] == '확인한 연결 조건'
            fetch(path + '/approve', {'version': 1, 'confirmed': True}, token)
            with zipfile.ZipFile(io.BytesIO(fetch(path + '/download?version=1'))) as archive:
                assert archive.testzip() is None
            run('exec', '-T', 'studio', 'python', '-c',
                "import os; from pathlib import Path; assert os.getuid() != 0; "
                "assert not Path('/app/.env').exists(); "
                "assert os.environ['AI_API_KEY'] == 'fixture-${DO_NOT_EXPAND}#key'")
            # Persist an interrupted operation with the real app's owner/PID.
            run('exec', '-T', 'studio', 'python', '-c',
                "from studio.core import Store; s=Store('/app/data/studio.db'); "
                "\nwith s.connect() as c:\n r=s.rows(c, 'operation')[0]; r['state']='running'; s.put(c, 'operation', r)")
            run('up', '-d', '--no-deps', '--force-recreate', '--wait', '--wait-timeout', '90', 'studio')
            restarted = fetch()
            assert restarted['products'][0]['id'] == product['id']
            assert restarted['drafts'][0]['status'] == 'approved'
            assert fetch(path)['candidates'][0]['id'] == candidate['id']
            run('exec', '-T', 'studio', 'python', '-c',
                "from studio.core import Store; s=Store('/app/data/studio.db'); "
                "\nwith s.connect() as c:\n assert s.rows(c, 'operation')[0]['state'] == 'failed'")
            print(json.dumps(dict(docker_build=True, nas_http=True, unlisted_host_blocked=True,
                local_ai_network_fixture=True, zip_download=True, nonroot=True, image_excludes_dotenv=True,
                literal_env_values=True, recreated_container_retains_data=True, interrupted_claim_recovered=True), indent=2))
        except Exception:
            print(run('logs', '--tail=60', check=False))
            raise
        finally:
            run('down', '-v', '--remove-orphans', '--rmi', 'local', check=False)


if __name__ == '__main__':
    main()
