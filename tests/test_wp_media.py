"""One media upload per request+kind, with lost-response recovery."""
from datetime import datetime, timezone
import pytest

from studio.core import Store

AT = datetime(2026, 10, 24, 0, 0, tzinfo=timezone.utc)
SITE = 'https://wp.example.test'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 40


class FakeWordPress:
    url = SITE
    def __init__(self):
        self.media = {}
        self.uploads = []
        self.next_id = 7
        self.lose_next = False
        self.reject = False

    def upload_media(self, filename, content, content_type):
        self.uploads.append((filename, content_type, len(content)))
        if self.reject:
            raise ValueError('WordPress 미디어 업로드 거절')
        media_id = self.next_id
        self.next_id += 1
        self.media[media_id] = {'id': media_id, 'source_url': SITE + f'/upload/{filename}',
                                'title': {'raw': filename}}
        if self.lose_next:
            self.lose_next = False
            raise ValueError('WordPress 응답 확인 불가')
        return {'id': media_id, 'source_url': self.media[media_id]['source_url']}

    def find_media(self, filename):
        return [item for item in self.media.values() if item['title']['raw'] == filename]


class FakeImageClient:
    daily_limit = 3
    def __init__(self, body=PNG):
        self.body = body
        self.calls = 0
        def generate(prompt, at):
            self.calls += 1
            return {'body': self.body, 'content_type': 'image/png',
                    'width': 600, 'height': 600, 'sha256': 'b' * 64}
        self.generate = generate


def image(kind='featured'):
    return {'body': PNG, 'content_type': 'image/png', 'width': 300, 'height': 300,
            'sha256': 'a' * 64, 'source_url': 'https://image9.coupangcdn.com/a.png'}


def test_media_upload_uses_media_endpoint_and_records_id(tmp_path):
    from studio.images import save_image
    from studio.automation_media import prepare_media
    store = Store(tmp_path / 'data.db')
    saved = save_image(tmp_path, 'req', 'featured', image())
    wp = FakeWordPress()
    record = prepare_media(store, 'req', 'featured', {**image(), **saved}, wp, AT)
    assert record['state'] == 'uploaded' and record['remote_id'] == 7
    assert wp.uploads == [(saved['local_path'], 'image/png', len(PNG))]


def test_upload_happens_once_across_restarts(tmp_path):
    from studio.images import save_image
    from studio.automation_media import prepare_media
    store = Store(tmp_path / 'data.db')
    saved = save_image(tmp_path, 'req', 'featured', image())
    wp = FakeWordPress()
    first = prepare_media(store, 'req', 'featured', {**image(), **saved}, wp, AT)
    second = prepare_media(Store(tmp_path / 'data.db'), 'req', 'featured',
                           {**image(), **saved}, wp, AT)
    assert second['remote_id'] == first['remote_id']
    assert len(wp.uploads) == 1


def test_lost_upload_response_reuses_matching_media(tmp_path):
    from studio.images import save_image
    from studio.automation_media import prepare_media
    store = Store(tmp_path / 'data.db')
    saved = save_image(tmp_path, 'req', 'featured', image())
    wp = FakeWordPress()
    wp.lose_next = True
    first = prepare_media(store, 'req', 'featured', {**image(), **saved}, wp, AT)
    assert first['state'] == 'uploaded'
    assert first['remote_id'] == 7
    assert len(wp.uploads) == 1


def test_rejected_upload_is_unavailable_and_not_retried(tmp_path):
    from studio.images import save_image
    from studio.automation_media import prepare_media
    store = Store(tmp_path / 'data.db')
    saved = save_image(tmp_path, 'req', 'featured', image())
    wp = FakeWordPress()
    wp.reject = True
    first = prepare_media(store, 'req', 'featured', {**image(), **saved}, wp, AT)
    assert first['state'] == 'unavailable' and first['error']
    wp.reject = False
    second = prepare_media(store, 'req', 'featured', {**image(), **saved}, wp, AT)
    assert second['state'] == 'unavailable'
    assert wp.uploads == [(saved['local_path'], 'image/png', len(PNG))]


def test_missing_local_file_is_unavailable(tmp_path):
    from studio.images import save_image
    from studio.automation_media import prepare_media
    store = Store(tmp_path / 'data.db')
    saved = save_image(tmp_path, 'req', 'featured', image())
    saved['path'].unlink()
    record = prepare_media(store, 'req', 'featured', {**image(), **saved}, FakeWordPress(), AT)
    assert record['state'] == 'unavailable'


def test_generated_body_image_is_saved_and_uploaded(tmp_path):
    from studio.automation_media import prepare_body_image
    store = Store(tmp_path / 'data.db')
    wp = FakeWordPress()
    client = FakeImageClient()
    record = prepare_body_image(store, 'req', client, wp, [{'index': 1, 'evidence': {'name': '합성'}}],
                                {'writing': {}}, tmp_path, AT)
    assert record['kind'] == 'body'
    assert record['state'] == 'uploaded'
    assert client.calls == 1
    again = prepare_body_image(store, 'req', client, wp, [{'index': 1, 'evidence': {'name': '합성'}}],
                               {'writing': {}}, tmp_path, AT)
    assert again['remote_id'] == record['remote_id']
    assert client.calls == 1


def test_existing_media_record_roundtrip(tmp_path):
    from studio.automation_media import media_record, save_media
    store = Store(tmp_path / 'data.db')
    from studio.automation_media import media_id
    record = {'id': media_id('req', 'featured'), 'request_id': 'req', 'kind': 'featured',
              'state': 'unavailable'}
    save_media(store, record)
    assert media_record(store, 'req', 'featured')['state'] == 'unavailable'
    assert media_record(store, 'req', 'body') is None
