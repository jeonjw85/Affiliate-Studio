"""OpenAI-compatible image generation: base64 only, bounded, key-safe."""
import base64
import json
from datetime import datetime, timezone
import pytest

from test_images import png_bytes

AT = datetime(2026, 10, 24, 0, 0, tzinfo=timezone.utc)
ENV = {'IMAGE_MODE': 'api', 'IMAGE_BASE_URL': 'https://image.example.test/v1',
       'IMAGE_MODEL': 'fixture-image', 'IMAGE_API_KEY': 'fixture-image-key',
       'IMAGE_DAILY_LIMIT': '3'}


class Remote:
    def __init__(self):
        self.requests = []
        self.replies = []

    def request(self, method, path, payload=None, headers=None):
        self.requests.append((method, path, payload, headers))
        if not self.replies:
            raise AssertionError('unexpected image request')
        return self.replies.pop(0)


def test_image_client_needs_complete_config():
    from studio.images import ImageClient
    assert ImageClient.from_env({}) is None
    for env in ({'IMAGE_MODE': 'api'},
                {'IMAGE_MODE': 'api', 'IMAGE_BASE_URL': 'http://x/v1',
                 'IMAGE_MODEL': 'm', 'IMAGE_API_KEY': 'k'},
                {'IMAGE_MODE': 'api', 'IMAGE_BASE_URL': 'https://x/v1',
                 'IMAGE_MODEL': 'm', 'IMAGE_API_KEY': 'k', 'IMAGE_DAILY_LIMIT': '0'},
                {'IMAGE_MODE': 'weird', 'IMAGE_BASE_URL': 'https://x/v1',
                 'IMAGE_MODEL': 'm', 'IMAGE_API_KEY': 'k'}):
        with pytest.raises(ValueError):
            ImageClient.from_env(env)
    client = ImageClient.from_env(ENV)
    assert client.model == 'fixture-image'
    assert 'fixture-image-key' not in repr(client)


def test_generation_sends_b64_only_request(monkeypatch):
    from studio.images import ImageClient
    remote = Remote()
    remote.replies.append({'data': [{'b64_json': base64.b64encode(png_bytes(600, 600)).decode()}]})
    monkeypatch.setattr(ImageClient, '_post', lambda self, payload: remote.request(
        'POST', '/images/generations', payload))
    result = ImageClient.from_env(ENV).generate('설명용 삽화', AT)
    assert (result['width'], result['height']) == (600, 600)
    assert result['content_type'] == 'image/png'
    method, path, payload, _ = remote.requests[0]
    assert (method, path) == ('POST', '/images/generations')
    assert payload == {'model': 'fixture-image', 'prompt': '설명용 삽화', 'size': '1024x1024',
                       'n': 1, 'response_format': 'b64_json'}


@pytest.mark.parametrize('reply', [
    {'data': [{'url': 'https://evil.test/a.png'}]},
    {'data': [{'b64_json': base64.b64encode(b'<html>').decode()}]},
    {'data': [{'b64_json': base64.b64encode(png_bytes(100, 100)).decode()}]},
    {'data': []},
    {'error': {'message': 'fixture-image-key'}},
    [],
])
def test_generation_rejects_bad_replies(monkeypatch, reply):
    from studio.images import ImageClient
    monkeypatch.setattr(ImageClient, '_post', lambda self, payload: reply)
    with pytest.raises(ValueError) as error:
        ImageClient.from_env(ENV).generate('prompt', AT)
    assert 'fixture-image-key' not in str(error.value)


def test_prompt_has_no_urls_prices_or_people():
    from studio.images import image_prompt
    products = [{'index': 1, 'evidence': {'name': '합성 상품', 'category': '생활용품',
                                          'facts': [{'text': '가격: 19,900원'}]}}]
    prompt = image_prompt(products, {'writing': {'audience': '정리하는 독자', 'topic': '생활용품'}})
    assert '합성 상품' in prompt and '설명용 삽화' in prompt
    assert '19,900' not in prompt and 'http' not in prompt
    assert '얼굴' in prompt and '로고' in prompt and '텍스트' in prompt


def test_daily_image_limit_is_separate(tmp_path):
    from studio.core import Store
    from studio.images import reserve_image_operation
    store = Store(tmp_path / 'data.db')
    next_day = datetime(2026, 10, 25, 0, 0, tzinfo=timezone.utc)
    with store.connect() as c:
        reserve_image_operation(store, c, 'req', 1, AT)
        with pytest.raises(ValueError):
            reserve_image_operation(store, c, 'req2', 1, AT)
        assert reserve_image_operation(store, c, 'req3', 1, next_day)['state'] == 'running'
    with store.connect() as c:
        assert all(row['type'] == 'image' for row in store.rows(c, 'operation'))


def test_save_image_uses_hash_name_and_is_idempotent(tmp_path):
    from studio.images import save_image
    image = {'body': png_bytes(300, 300), 'sha256': 'a' * 64, 'content_type': 'image/png'}
    first = save_image(tmp_path, 'req', 'featured', image)
    second = save_image(tmp_path, 'req', 'featured', image)
    assert first['path'] == second['path']
    assert first['path'].read_bytes() == image['body']
    assert 'req' in first['path'].name and 'a' * 16 in first['path'].name
    assert first['bytes'] == len(image['body'])
