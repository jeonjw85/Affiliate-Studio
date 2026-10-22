"""Official affiliate wire contracts, with only external HTTP replaced."""
import hashlib
import hmac
import json
import re
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from studio.app import create_app
from test_core import product


ENV = {
    'COUPANG_ACCESS_KEY': 'fixture-coupang-access',
    'COUPANG_SECRET_KEY': 'fixture-coupang-secret',
    'TOSS_ACCESS_KEY': 'fixture-toss-access',
    'TOSS_SECRET_KEY': 'fixture-toss-secret',
    'TOSS_PUBLISHER_ID': '550e8400-e29b-41d4-a716-446655440000',
}
SOURCES = {'coupang': 'https://www.coupang.com/vp/products/184614775?itemId=123&vendorItemId=456',
           'toss': 'https://toss.shopping/t/9876'}
LINKS = {'coupang': 'https://link.coupang.com/a/fixture', 'toss': 'https://toss.im/_m/fixture'}
COUPANG_API = 'https://api-gateway.coupang.com/v2/providers/affiliate_open_api/apis/openapi/v1/deeplink'
TOKEN_API = 'https://oauth2.cert.toss.im/token'
TOSS_API = 'https://sharelink.toss.im/openapi/links'


@pytest.fixture
def remote(monkeypatch):
    requests = []
    def handle(request):
        requests.append(request)
        if str(request.url) == TOKEN_API:
            return httpx.Response(200, json={'access_token': 'fixture-token', 'token_type': 'Bearer',
                                            'expires_in': 3600, 'scope': 'sharelink:read sharelink:write'})
        if str(request.url) == COUPANG_API:
            return httpx.Response(200, json={'rCode': '0', 'rMessage': '', 'data': [{
                'originalUrl': json.loads(request.content)['coupangUrls'][0],
                'shortenUrl': LINKS['coupang'], 'landingUrl': 'https://link.coupang.com/re/AFFSDP?lptag=fixture'}]})
        assert str(request.url) == TOSS_API
        return httpx.Response(200, json={'resultType': 'SUCCESS', 'success': {
            'tacaItemId': 12345, 'publisherId': ENV['TOSS_PUBLISHER_ID'],
            'shortUrl': LINKS['toss'], 'originUrl': 'https://toss.shopping/t/9876?k=fixture&referrer=affiliate'}})
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', lambda self, request: transport.handle_request(request))
    return requests, transport


@pytest.fixture
def client(tmp_path, monkeypatch, remote):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    with TestClient(create_app(tmp_path), base_url='http://127.0.0.1:8787') as c:
        c.headers['X-CSRF-Token'] = c.get('/api/state').json()['csrf']
        yield c


def issue(client, provider='coupang', source=None):
    return client.post('/api/affiliate-links', json={'provider': provider, 'source_url': source or SOURCES[provider]})


@pytest.mark.parametrize('provider', ['coupang', 'toss'])
def test_issued_link_reaches_saved_product_and_content(client, remote, provider):
    response = issue(client, provider)
    assert response.status_code == 200, response.text
    assert response.json() == {'provider': provider, 'source_url': SOURCES[provider], 'affiliate_url': LINKS[provider]}
    assert client.get('/api/state').json()['products'] == []  # Form fill, then explicit save.
    p = client.post('/api/products', json=product(provider=provider, source_url=SOURCES[provider],
                                                  affiliate_url=response.json()['affiliate_url'])).json()
    draft = client.post('/api/drafts', json={'product_id': p['id']}).json()
    detail = client.get(f'/api/drafts/{draft["id"]}').json()
    assert LINKS[provider] in detail['preview']['html']
    assert LINKS[provider] in detail['preview']['text']
    assert all(r.method == 'POST' for r in remote[0])


def test_coupang_hmac_and_original_query_match_official_contract(client, remote):
    assert issue(client).status_code == 200
    request, = remote[0]
    assert str(request.url) == COUPANG_API
    assert json.loads(request.content) == {'coupangUrls': [SOURCES['coupang']]}
    auth = request.headers['Authorization']
    match = re.fullmatch(r'CEA algorithm=HmacSHA256, access-key=fixture-coupang-access, signed-date=(\d{6}T\d{6}Z), signature=([0-9a-f]{64})', auth)
    assert match
    # Independent protocol oracle: the product URL/body is NOT part of the signed path.
    message = match[1] + 'POST/v2/providers/affiliate_open_api/apis/openapi/v1/deeplink'
    assert match[2] == hmac.new(b'fixture-coupang-secret', message.encode(), hashlib.sha256).hexdigest()


def test_toss_oauth_form_and_publisher_use_cached_token(client, remote):
    assert issue(client, 'toss').status_code == 200
    assert issue(client, 'toss').status_code == 200
    token, first, second = remote[0]
    assert str(token.url) == TOKEN_API
    assert token.headers['Content-Type'] == 'application/x-www-form-urlencoded'
    assert parse_qs(token.content.decode()) == {
        'grant_type': ['client_credentials'], 'client_id': ['fixture-toss-access'],
        'client_secret': ['fixture-toss-secret'], 'scope': ['sharelink:read sharelink:write']}
    for request in (first, second):
        assert str(request.url) == TOSS_API
        assert request.headers['Authorization'] == 'Bearer fixture-token'
        assert json.loads(request.content) == {'landingType': 'PRODUCT', 'tacaId': 9876,
                                              'publisherId': ENV['TOSS_PUBLISHER_ID']}


@pytest.mark.parametrize('provider,url', [
    ('coupang', 'https://www.coupang.com.evil.test/vp/products/123'),
    ('coupang', 'https://evil.test/?url=https://www.coupang.com/vp/products/123'),
    ('coupang', 'https://www.coupang.com@evil.test/vp/products/123'),
    ('coupang', 'http://www.coupang.com/vp/products/123'),
    ('coupang', 'https://www.coupang.com:8443/vp/products/123'),
    ('coupang', 'https://www.coupang.com/np/search?q=test'),
    ('coupang', 'https://link.coupang.com/a/existing'),
    ('toss', 'https://toss.im/_m/unknown'),
    ('toss', 'https://toss.shopping/t/0'),
    ('toss', 'https://toss.shopping/t/１２３'),
    ('toss', 'https://toss.shopping/t/9876/another'),
    ('toss', 'https://toss.shopping.evil.test/t/9876'),
    ('toss', 'https://127.0.0.1/t/9876'),
    ('toss', SOURCES['coupang']),
])
def test_unsupported_urls_never_send_credentials(client, remote, provider, url):
    response = issue(client, provider, url)
    assert response.status_code == 400
    assert response.json()['error']
    assert remote[0] == []


@pytest.mark.parametrize('provider,payload', [
    ('coupang', {'rCode': '400', 'rMessage': 'fixture-coupang-secret', 'data': []}),
    ('coupang', {'rCode': '0', 'data': []}),
    ('coupang', {'rCode': '0', 'data': [{'originalUrl': 'https://www.coupang.com/vp/products/999', 'shortenUrl': LINKS['coupang']}]}),
    ('coupang', {'rCode': '0', 'data': [{'originalUrl': SOURCES['coupang'], 'shortenUrl': 'https://evil.test/a'}]}),
    ('toss', {'resultType': 'FAIL', 'error': {'reason': 'fixture-toss-secret'}}),
    ('toss', {'resultType': 'FAIL', 'error': {'errorCode': 'SHARELINK_OPENAPI_ACCESS_DENIED'}}),
    ('toss', {'resultType': 'FAIL', 'error': {'errorCode': 'SHARELINK_OPENAPI_QUOTA_EXCEEDED'}}),
    ('toss', {'resultType': 'SUCCESS', 'success': {'shortUrl': 'javascript:alert(1)'}}),
    ('toss', {'resultType': 'SUCCESS', 'success': {'shortUrl': LINKS['toss'], 'tacaItemId': 12345, 'publisherId': 'other-account'}}),
    ('toss', {'resultType': 'SUCCESS', 'success': None}),
    ('coupang', []),
])
def test_remote_business_errors_and_invalid_results_do_not_issue_links(client, remote, provider, payload):
    transport = remote[1]
    original = transport.handler
    def handle(request):
        if str(request.url) == TOKEN_API:
            return original(request)
        remote[0].append(request)
        return httpx.Response(200, json=payload)
    transport.handler = handle
    response = issue(client, provider)
    assert response.status_code == 400
    assert response.json()['error']
    assert 'affiliate_url' not in response.json()
    assert not any(secret in response.text for secret in ENV.values())
    assert len(remote[0]) == (2 if provider == 'toss' else 1)


@pytest.mark.parametrize('status', [302, 400, 401, 403, 429, 500])
def test_http_errors_do_not_follow_redirects_retry_or_expose_secrets(client, remote, status):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, headers={'Location': 'https://evil.test/steal'}, text='fixture-coupang-secret')
    remote[1].handler = handle
    response = issue(client)
    assert response.status_code == 400
    assert response.json()['error']
    assert 'fixture-coupang-secret' not in response.text
    assert len(calls) == 1


@pytest.mark.parametrize('mode', ['timeout', 'not-json'])
def test_transport_failures_are_readable_errors(client, remote, mode):
    def handle(request):
        if mode == 'timeout':
            raise httpx.ReadTimeout('fixture-coupang-secret', request=request)
        return httpx.Response(200, text='<html>fixture-coupang-secret</html>')
    remote[1].handler = handle
    response = issue(client)
    assert response.status_code == 400
    assert response.json()['error']
    assert 'fixture-coupang-secret' not in response.text


@pytest.mark.parametrize('missing', [list(ENV), ['TOSS_SECRET_KEY'], ['COUPANG_ACCESS_KEY']])
def test_missing_configuration_is_reported_without_network(tmp_path, monkeypatch, remote, missing):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    for key in missing:
        monkeypatch.delenv(key)
    with TestClient(create_app(tmp_path), base_url='http://localhost') as c:
        state = c.get('/api/state').json()
        c.headers['X-CSRF-Token'] = state['csrf']
        assert 'affiliate' in state['connections']
        for provider in ('coupang', 'toss'):
            configured = not any(key.startswith(provider.upper()) for key in missing)
            connection = state['connections']['affiliate'][provider]
            assert connection['configured'] is configured
            if not configured:
                assert connection['error']
                assert issue(c, provider).status_code == 400
        assert remote[0] == []
        assert not any(value in json.dumps(state) for value in ENV.values())


def test_issue_requires_csrf_and_valid_provider(client, remote):
    assert client.post('/api/affiliate-links', json={}, headers={'X-CSRF-Token': ''}).status_code == 403
    for provider in ('other', None, [], {}):
        response = client.post('/api/affiliate-links', json={'provider': provider, 'source_url': SOURCES['coupang']})
        assert response.status_code == 400
    assert remote[0] == []


def test_toss_expired_token_is_refreshed_before_issuing(client, remote, monkeypatch):
    clock = [100]
    monkeypatch.setattr('studio.affiliate.monotonic', lambda: clock[0])
    assert issue(client, 'toss').status_code == 200
    clock[0] += 3601
    assert issue(client, 'toss').status_code == 200
    assert [str(r.url) for r in remote[0]] == [TOKEN_API, TOSS_API, TOKEN_API, TOSS_API]


def test_toss_unauthorized_token_is_refreshed_only_on_next_explicit_request(client, remote):
    original = remote[1].handler
    attempts = []
    def handle(request):
        if str(request.url) == TOSS_API:
            attempts.append(request)
            if len(attempts) == 1:
                return httpx.Response(401, text='fixture-token')
        return original(request)
    remote[1].handler = handle
    failed = issue(client, 'toss')
    assert failed.status_code == 400 and 'fixture-token' not in failed.text
    assert len(attempts) == 1
    assert issue(client, 'toss').status_code == 200
    assert len(attempts) == 2
    assert sum(str(r.url) == TOKEN_API for r in remote[0]) == 2


@pytest.mark.parametrize('change', [
    {'access_token': None}, {'access_token': 'token\nheader'},
    {'token_type': None}, {'token_type': {}}, {'token_type': 'Basic'},
    {'expires_in': '3600'}, {'expires_in': 0}, {'expires_in': True},
])
def test_bad_oauth_reply_never_attempts_link_issuance(client, remote, change):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={'access_token': 'fixture-token', 'token_type': 'Bearer',
                                        'expires_in': 3600, **change})
    remote[1].handler = handle
    response = issue(client, 'toss')
    assert response.status_code == 400
    assert 'fixture-token' not in response.text
    assert [str(r.url) for r in calls] == [TOKEN_API]


@pytest.mark.parametrize('key,value,provider', [
    ('COUPANG_ACCESS_KEY', 'bad,header', 'coupang'),
    ('COUPANG_SECRET_KEY', 'bad\nsecret', 'coupang'),
    ('TOSS_ACCESS_KEY', 'bad key', 'toss'),
    ('TOSS_PUBLISHER_ID', 'not-a-uuid', 'toss'),
])
def test_invalid_configuration_does_not_break_startup_or_expose_values(tmp_path, monkeypatch, remote, key, value, provider):
    for name, credential in ENV.items():
        monkeypatch.setenv(name, credential)
    monkeypatch.setenv(key, value)
    with TestClient(create_app(tmp_path), base_url='http://localhost') as c:
        state = c.get('/api/state')
        assert state.status_code == 200
        assert not state.json()['connections']['affiliate'][provider]['configured']
        assert value not in state.text
        c.headers['X-CSRF-Token'] = state.json()['csrf']
        assert issue(c, provider).status_code == 400
        assert remote[0] == []
