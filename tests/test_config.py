"""Real launcher and NAS request-boundary checks with disposable configuration."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

import pytest
from fastapi.testclient import TestClient
from studio.app import create_app
from test_core import product


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('override, expected', [(None, 'from-file-${HOME}#literal'), ('from-shell', 'from-shell')])
def test_launcher_loads_its_own_dotenv_and_respects_environment(tmp_path, override, expected):
    project = tmp_path / 'project'
    project.mkdir()
    shutil.copy2(ROOT / 'run.py', project / 'run.py')
    shutil.copytree(ROOT / 'studio', project / 'studio', ignore=shutil.ignore_patterns('__pycache__'))
    (project / '.env').write_text("AI_MODE=local\nAI_MODEL='from-file-${HOME}#literal'\nAI_BASE_URL=http://127.0.0.1:11434/v1\n")
    (tmp_path / '.env').write_text('AI_MODEL=wrong-directory\n')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('AI_', 'WP_', 'STUDIO_'))}
    env['STUDIO_DATA_DIR'] = str(tmp_path / 'data')
    if override is not None:
        env['AI_MODEL'] = override
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    with (tmp_path / 'server.log').open('w') as log:
        server = subprocess.Popen([sys.executable, str(project / 'run.py'), '--port', str(port)],
                                  cwd=tmp_path, env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 10
            state = None
            while time.monotonic() < deadline:
                assert server.poll() is None, (tmp_path / 'server.log').read_text()
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/state', timeout=1) as response:
                        state = json.load(response)
                    break
                except OSError:
                    time.sleep(.05)
            assert state is not None, 'Launcher did not become ready'
            assert state['connections']['ai_model'] == expected
            assert state['connections']['ai'] is True
        finally:
            server.terminate()
            server.wait(timeout=10)


def test_configured_nas_hosts_allow_same_origin_writes_only(tmp_path, monkeypatch):
    monkeypatch.setenv('STUDIO_ALLOWED_HOSTS', 'nas.local,192.168.1.20')
    with TestClient(create_app(tmp_path), base_url='http://nas.local:8787') as client:
        response = client.get('/api/state')
        assert response.status_code == 200
        token = response.json()['csrf']
        headers = {'X-CSRF-Token': token, 'Origin': 'http://nas.local:8787'}
        assert client.post('/api/products', json=product(), headers=headers).status_code == 200
        assert client.get('/api/state', headers={'Host': '192.168.1.20:8787'}).status_code == 200
        for host in ('other.local:8787', 'nas.local.evil.test', 'user@nas.local', 'nas.local/path'):
            assert client.get('/api/state', headers={'Host': host}).status_code == 403
        assert client.post('/api/products', json=product(), headers={**headers, 'Origin': 'http://other.local:8787'}).status_code == 403
        assert client.post('/api/products', json=product()).status_code == 403
        assert len(client.get('/api/state').json()['products']) == 1


@pytest.mark.parametrize('hosts', ['*', '*.local', 'http://nas.local', 'nas.local:8787'])
def test_invalid_host_configuration_fails_closed(tmp_path, monkeypatch, hosts):
    monkeypatch.setenv('STUDIO_ALLOWED_HOSTS', hosts)
    with pytest.raises(ValueError, match='STUDIO_ALLOWED_HOSTS'):
        create_app(tmp_path)


@pytest.mark.parametrize('configured, requested', [
    ('fd00:0000:0000:0000:0000:0000:0000:0001', '[fd00::1]:8787'),
    ('fd00::1', '[fd00:0000:0000:0000:0000:0000:0000:0001]:8787'),
])
def test_equivalent_ipv6_host_spellings_are_allowed(tmp_path, monkeypatch, configured, requested):
    monkeypatch.setenv('STUDIO_ALLOWED_HOSTS', configured)
    with TestClient(create_app(tmp_path), base_url='http://localhost') as client:
        assert client.get('/api/state', headers={'Host': requested}).status_code == 200
        assert client.get('/api/state', headers={'Host': '[fd00::2]:8787'}).status_code == 403
