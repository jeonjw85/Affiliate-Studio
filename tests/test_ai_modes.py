"""Exercise mode selection through HTTP; external transport uses a fixture only."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from studio.app import create_app
from test_core import product


@pytest.fixture
def remote(monkeypatch):
    calls = []
    settings = {'status': 200}
    def respond(request):
        calls.append(request)
        if settings['status'] != 200:
            return httpx.Response(settings['status'], headers={'Location': 'https://other.example/v1'},
                                  json={'error': 'fixture-key-123'})
        if request.url.path.endswith('/models'):
            return httpx.Response(200, json={'data': [{'id': 'fixture-model', 'description': settings.get('catalog_metadata', '')}]})
        return httpx.Response(200, json={'metadata': settings.get('generation_metadata', ''), 'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({
            'title': '확인한 조건 안내', 'body': 'USB 연결 조건을 확인하세요.', 'evidence_refs': [1]})}}]})
    original = httpx.Client
    monkeypatch.setattr('studio.ai.httpx.Client', lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    return calls, settings


def configure(monkeypatch, mode):
    monkeypatch.setenv('AI_MODE', mode)
    monkeypatch.setenv('AI_BASE_URL', 'https://api.example.test/v1' if mode == 'api' else 'http://127.0.0.1:11434/v1')
    monkeypatch.setenv('AI_MODEL', 'fixture-model')
    monkeypatch.setenv('AI_API_KEY', 'fixture-key-123')


@pytest.mark.parametrize('mode', ['local', 'api'])
def test_selected_mode_checks_and_generates_without_leaking_credentials(tmp_path, monkeypatch, remote, mode):
    configure(monkeypatch, mode)
    calls, _ = remote
    with TestClient(create_app(tmp_path), base_url='http://localhost') as client:
        response = client.get('/api/state')
        state = response.json()
        assert state['connections'].get('ai_mode') == mode
        assert state['connections']['ai'] is True
        assert not calls, 'Starting the app must not invoke an AI service'
        assert 'fixture-key-123' not in response.text
        headers = {'X-CSRF-Token': state['csrf']}
        checked = client.post('/api/ai/check', json={}, headers=headers)
        assert checked.json()['available'] is True
        p = client.post('/api/products', json=product(), headers=headers).json()
        d = client.post('/api/drafts', json={'product_id': p['id']}, headers=headers).json()
        path = '/api/drafts/' + d['id']
        denied = client.post(path + '/ai', json={'version': 1, 'style': 'guide'}, headers=headers)
        assert denied.status_code == 400 and len(calls) == 1
        generated = client.post(path + '/ai', json={'version': 1, 'style': 'guide', 'confirmed_send': True}, headers=headers)
        assert generated.status_code == 200, generated.text
        assert generated.json()['title'] == '확인한 조건 안내'
        assert client.get(path).json()['draft']['body'] == d['body']
        assert len(calls) == 2
        for request in calls:
            assert request.headers.get('Authorization') == ('Bearer fixture-key-123' if mode == 'api' else None)
        payload = json.loads(calls[-1].content)
        assert payload['model'] == 'fixture-model'
        assert payload['response_format']['type'] == 'json_schema'
        assert p['affiliate_url'] not in calls[-1].content.decode()
        assert 'fixture-key-123' not in calls[-1].content.decode()
        assert ('/no_think' in payload['messages'][0]['content']) is (mode == 'local')


@pytest.mark.parametrize('status', [401, 429, 302])
def test_api_failures_do_not_retry_redirect_or_expose_keys(tmp_path, monkeypatch, remote, status):
    configure(monkeypatch, 'api')
    calls, settings = remote
    settings['status'] = status
    with TestClient(create_app(tmp_path), base_url='http://localhost') as client:
        state = client.get('/api/state').json()
        assert state['connections']['ai'] is True
        result = client.post('/api/ai/check', json={}, headers={'X-CSRF-Token': state['csrf']})
        assert result.json()['available'] is False
        assert len(calls) == 1
        assert 'fixture-key-123' not in result.text


@pytest.mark.parametrize('changes', [
    {'AI_API_KEY': ''}, {'AI_API_KEY': 'bad\nkey'}, {'AI_MODE': 'unknown'},
    {'AI_BASE_URL': 'http://api.example.test/v1'},
    {'AI_BASE_URL': 'https://user:password@api.example.test/v1'},
    {'AI_BASE_URL': 'https://api.example.test/v1?key=fixture-key-123'},
])
def test_invalid_api_settings_never_fall_back_to_local(tmp_path, monkeypatch, remote, changes):
    configure(monkeypatch, 'api')
    for key, value in changes.items():
        monkeypatch.setenv(key, value)
    calls, _ = remote
    with TestClient(create_app(tmp_path), base_url='http://localhost') as client:
        response = client.get('/api/state')
        connections = response.json()['connections']
        assert connections['ai'] is False
        assert connections['ai_error']
        assert 'fixture-key-123' not in response.text and 'bad\\nkey' not in response.text
        assert not calls


def test_local_mode_allows_only_explicit_extra_model_hosts(tmp_path, monkeypatch, remote):
    configure(monkeypatch, 'local')
    monkeypatch.setenv('AI_BASE_URL', 'http://ollama:11434/v1')
    monkeypatch.setenv('AI_LOCAL_HOSTS', 'ollama,host.docker.internal')
    calls, _ = remote
    with TestClient(create_app(tmp_path), base_url='http://localhost') as client:
        state = client.get('/api/state').json()
        assert state['connections']['ai'] is True
        result = client.post('/api/ai/check', json={}, headers={'X-CSRF-Token': state['csrf']})
        assert result.json()['available'] is True
        assert calls[0].url.host == 'ollama'
        assert 'Authorization' not in calls[0].headers
    monkeypatch.setenv('AI_BASE_URL', 'http://other-server:11434/v1')
    with TestClient(create_app(tmp_path), base_url='http://localhost') as client:
        assert client.get('/api/state').json()['connections']['ai'] is False


@pytest.mark.parametrize('metadata_size, available', [(105600, True), (5 * 1024 * 1024, False)])
def test_api_catalog_has_its_own_bounded_response_size(tmp_path, monkeypatch, remote, metadata_size, available):
    configure(monkeypatch, 'api')
    calls, settings = remote
    settings['catalog_metadata'] = 'x' * metadata_size
    with TestClient(create_app(tmp_path), base_url='http://localhost') as client:
        token = client.get('/api/state').json()['csrf']
        result = client.post('/api/ai/check', json={}, headers={'X-CSRF-Token': token})
        assert result.json()['available'] is available
        assert len(calls) == 1


def test_generation_response_keeps_the_smaller_size_limit(remote):
    from studio.ai import OpenAIClient
    calls, settings = remote
    settings['generation_metadata'] = 'x' * 70000
    ai = OpenAIClient('https://api.example.test/v1', 'fixture-model', mode='api', api_key='fixture-key-123')
    with pytest.raises(ValueError, match='응답 크기 초과'):
        ai.request('POST', '/chat/completions', {})
    assert len(calls) == 1


def test_local_model_ipv6_host_is_normalized(tmp_path, monkeypatch, remote):
    configure(monkeypatch, 'local')
    monkeypatch.setenv('AI_BASE_URL', 'http://[fd00:0000:0000:0000:0000:0000:0000:0001]:11434/v1')
    monkeypatch.setenv('AI_LOCAL_HOSTS', 'fd00::1')
    with TestClient(create_app(tmp_path), base_url='http://localhost') as client:
        state = client.get('/api/state').json()
        assert state['connections']['ai'] is True
        result = client.post('/api/ai/check', json={}, headers={'X-CSRF-Token': state['csrf']})
        assert result.json()['available'] is True
