"""Images on published posts: featured media, body figure, failures don't block."""
from datetime import datetime, timedelta, timezone
import base64
import pytest

from automation_fixtures import make_settings, message_update
from studio.core import Store
from test_automation import AT, COUPANG, FakeAI, FakeTelegram, FakeWordPress, Remote, build, inspect, prepare
from test_images import png_bytes
from test_wp_media import FakeImageClient, FakeWordPress as MediaWordPress

SITE = 'https://wp.example.test'


class ImageRemote(Remote):
    def __init__(self):
        super().__init__()
        self.image_calls = 0


class Media(FakeWordPress):
    """Adds the media API to the shared WordPress fake."""
    def __init__(self, remote):
        super().__init__(remote)
        self.media = {}
        self.media_uploads = []
        self.next_media = 7
        self.image_fail = False

    def upload_media(self, filename, content, content_type):
        self.media_uploads.append((filename, content_type, len(content)))
        if self.image_fail:
            raise ValueError('WordPress 미디어 업로드 거절')
        media_id = self.next_media
        self.next_media += 1
        self.media[media_id] = {'id': media_id, 'title': {'raw': filename},
                                'source_url': SITE + '/upload/' + filename}
        return {'id': media_id, 'source_url': self.media[media_id]['source_url']}

    def find_media(self, filename):
        return [item for item in self.media.values() if item['title']['raw'] == filename]

    def request(self, method, suffix, payload=None, *, params=None):
        if method == 'POST' and suffix == '':
            payload = dict(payload or {})
            # emulate WordPress storing a featured image id
            result = super().request(method, suffix, payload, params=params)
            post = self.remote.posts[result['id']]
            post['featured_media'] = payload.get('featured_media')
            return result
        return super().request(method, suffix, payload, params=params)


def image_evidence(url='https://image9.coupangcdn.com/a.png'):
    result = inspect(Remote(), COUPANG)
    result['evidence']['image_url'] = url
    return result


def build_images(tmp_path, *, image=True):
    from studio.automation import Automation
    from studio.automation_store import get_settings, save_settings
    remote = ImageRemote()
    store = Store(tmp_path / 'data.db')
    wp = Media(remote)
    images = FakeImageClient() if image else None
    automation = Automation(store, FakeTelegram(remote), FakeAI(remote), wp,
                            clock=lambda: AT, images=images, data_dir=tmp_path)
    save_settings(store, make_settings(enabled=True, start='10:00'), get_settings(store, AT)['version'], AT)
    return automation, store, remote, image_evidence


def receive(store, automation, update, opener):
    from studio.telegram_inbox import receive_update
    return receive_update(store, automation.telegram, update, AT, opener=opener)


def at_opener(evidence):
    return lambda url, moment: evidence()


def test_published_post_has_featured_media_and_figure(tmp_path, monkeypatch):
    automation, store, remote, evidence = build_images(tmp_path)
    monkeypatch.setattr('studio.automation_media.download_image',
                        lambda url: {'url': url, 'body': png_bytes(300, 300),
                                     'content_type': 'image/png', 'width': 300, 'height': 300,
                                     'sha256': 'c' * 64})
    receive(store, automation, message_update(1, COUPANG), at_opener(evidence))
    due, _ = prepare(automation)
    automation.tick(due)
    post = next(iter(remote.posts.values()))
    assert post['featured_media'] == 7
    assert 'AI 생성 이미지 · 설명용 삽화' in post['content']['raw']
    assert post['content']['raw'].index('<figure>') < post['content']['raw'].index('<h2>')


def test_image_failures_do_not_block_publish(tmp_path, monkeypatch):
    automation, store, remote, evidence = build_images(tmp_path)
    automation.images = None
    monkeypatch.setattr('studio.automation_media.download_image',
                        lambda url: (_ for _ in ()).throw(ValueError('이미지 응답 확인 불가')))
    receive(store, automation, message_update(1, COUPANG), at_opener(evidence))
    due, _ = prepare(automation)
    automation.tick(due)
    request = automation.public(due)['requests'][0]
    assert request['state'] == 'scheduled'
    post = next(iter(remote.posts.values()))
    assert post.get('featured_media') is None
    assert '<figure>' not in post['content']['raw']
    automation.deliver(due)
    assert any('이미지' in note['text'] for note in remote.sent)


def test_update_keeps_existing_media(tmp_path, monkeypatch):
    automation, store, remote, evidence = build_images(tmp_path)
    monkeypatch.setattr('studio.automation_media.download_image',
                        lambda url: {'url': url, 'body': png_bytes(300, 300),
                                     'content_type': 'image/png', 'width': 300, 'height': 300,
                                     'sha256': 'c' * 64})
    receive(store, automation, message_update(1, COUPANG), at_opener(evidence))
    due, _ = prepare(automation)
    automation.tick(due)
    post = next(iter(remote.posts.values()))
    post['status'] = 'publish'
    with store.connect() as c:
        row = store.rows(c, 'automation_request')[0]
        row['update_state'] = 'updating'
        store.put(c, 'automation_request', row)
    uploads = len(automation.wordpress.media_uploads)
    automation.tick(due + timedelta(minutes=5))
    assert len(automation.wordpress.media_uploads) == uploads  # no new upload on update
    assert post['featured_media'] == 7


def test_legacy_snapshot_without_media_still_verifies(tmp_path):
    from test_automation_publish import FakeWordPress as PublishWordPress, SNAPSHOT
    wp = PublishWordPress()
    wp.posts[42] = {'id': 42, 'status': 'draft', 'slug': 'x',
                    'title': {'raw': SNAPSHOT['title']}, 'content': {'raw': SNAPSHOT['html']},
                    'date_gmt': ''}
    assert wp.verify_snapshot(wp.posts[42], SNAPSHOT)
