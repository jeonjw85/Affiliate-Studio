from datetime import timedelta
from fastapi.testclient import TestClient
from studio.app import create_app
from studio.core import now
from test_core import product
from test_ai import ai_server
from test_publication import wp_server


def test_ai_to_wordpress_contract_end_to_end(tmp_path, monkeypatch):
    with ai_server() as (url, ai), wp_server() as (wp, remote):
        monkeypatch.setenv('AI_BASE_URL', url); monkeypatch.setenv('AI_MODEL', 'local-test')
        monkeypatch.setenv('WP_URL', 'https://fixture.example'); monkeypatch.setenv('WP_USERNAME', 'fixture')
        monkeypatch.setenv('WP_APP_PASSWORD', 'fixture-only')
        monkeypatch.setattr('studio.app.WordPress', lambda *args: wp)
        with TestClient(create_app(tmp_path), base_url='http://127.0.0.1:8787') as client:
            state = client.get('/api/state').json()
            assert state['connections']['ai'] is True and state['connections']['ai_model'] == 'local-test'
            assert not ai.requests and not remote.requests, 'startup must not perform external requests'
            headers = {'X-CSRF-Token': state['csrf']}
            def post(path, data):
                response = client.post(path, json=data, headers=headers)
                assert response.status_code == 200, response.text
                return response.json()
            assert post('/api/ai/check', {})['available'] is True
            p = post('/api/products', product())
            d = post('/api/drafts', {'product_id': p['id']}); path = '/api/drafts/' + d['id']
            denied = client.post(path + '/ai', json={'version': 1, 'style': 'guide'}, headers=headers)
            assert denied.status_code == 400
            candidate = post(path + '/ai', {'version': 1, 'style': 'concise', 'confirmed_send': True})
            d = post(path + '/candidates/' + candidate['id'] + '/apply', {'version': 1})
            assert d['version'] == 2 and d['status'] == 'review'
            post(path + '/approve', {'version': 2, 'confirmed': True})
            post(path + '/wordpress', {'version': 2, 'confirmed': True})
            pub = post(path + '/publish-schedule', {'version': 2, 'run_at': (now() + timedelta(minutes=15)).isoformat(), 'confirmed_publish': True})
            assert pub['state'] == 'future'
            assert post('/api/publications/' + pub['id'] + '/refresh', {})['state'] == 'future'
            count = len(remote.requests)
            denied = client.post('/api/publications/' + pub['id'] + '/cancel', json={'confirmed_cancel': True}, headers=headers)
            assert denied.status_code == 400 and 'WordPress 관리자' in denied.json()['error']
            assert len(remote.requests) == count and remote.post['status'] == 'future'
            remote.post['status'] = 'draft'  # Explicit administrator action in the fixture.
            assert post('/api/publications/' + pub['id'] + '/refresh', {})['state'] == 'cancelled'
            assert all(r[0] == 'GET' for r in remote.requests[count:])
            assert client.get('/api/state').json()['publications'][0]['state'] == 'cancelled'
