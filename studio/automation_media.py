"""Durable media records: one per request+kind, reused across retries.

A media record owns the whole image lifecycle. Upload success is persisted so a
restart, an article rewrite, or a late description can never upload the same
image twice.
"""
from datetime import timezone
from pathlib import Path

from .automation_store import get_optional, public, record_id
from .images import (allowed_image_url, download_image, image_prompt,
                     reserve_image_operation, save_image)


def media_id(request_id, kind):
    return record_id('media', request_id, kind)


def media_record(store, request_id, kind):
    with store.connect() as c:
        row = get_optional(store, c, 'automation_media', media_id(request_id, kind))
    return public(row) if row else None


def save_media(store, record):
    with store.connect() as c:
        store.put(c, 'automation_media', dict(record))
    return record


def _record(request_id, kind, **values):
    return dict({'id': media_id(request_id, kind), 'request_id': request_id, 'kind': kind,
                 'state': 'unavailable', 'source_url': '', 'host': '', 'prompt': '',
                 'content_type': '', 'bytes': 0, 'sha256': '', 'width': 0, 'height': 0,
                 'local_path': '', 'remote_id': None, 'remote_url': '', 'error': ''}, **values)


def prepare_media(store, request_id, kind, image, client, at):
    """Upload one saved image once; failures are recorded, never raised."""
    at = at.astimezone(timezone.utc)
    existing = media_record(store, request_id, kind)
    if existing and existing['state'] in ('uploaded', 'unavailable', 'upload_uncertain'):
        return existing
    path = Path(image.get('path') or '')
    if not path.is_file():
        return save_media(store, _record(request_id, kind, state='unavailable',
                                         error='저장된 이미지 파일 없음'))
    record = _record(request_id, kind, state='fetched',
                     source_url=image.get('source_url') or image.get('url') or '',
                     host=image.get('host', ''), content_type=image['content_type'],
                     bytes=image.get('bytes', len(image.get('body', b''))),
                     sha256=image['sha256'], width=image.get('width', 0),
                     height=image.get('height', 0), local_path=image['local_path'])
    if image.get('prompt'):
        record['prompt'] = image['prompt']
    if client is None:
        return save_media(store, dict(record, state='unavailable', error='WordPress 연결 없음'))
    try:
        uploaded = client.upload_media(image['local_path'], path.read_bytes(),
                                       image['content_type'])
        if not isinstance(uploaded, dict) or type(uploaded.get('id')) is not int \
                or uploaded['id'] <= 0:
            raise ValueError('WordPress 미디어 ID 확인 불가')
        record.update(state='uploaded', remote_id=uploaded['id'],
                      remote_url=uploaded.get('source_url', ''))
    except Exception:
        # Recover from a lost response without uploading a second copy.
        try:
            matches = client.find_media(image['local_path'])
        except Exception:
            matches = []
        if len(matches) == 1 and type(matches[0].get('id')) is int:
            record.update(state='uploaded', remote_id=matches[0]['id'],
                          remote_url=matches[0].get('source_url', ''))
        else:
            record.update(state='unavailable', error='미디어 업로드 결과 확인 불가')
    return save_media(store, record)


def prepare_featured_image(store, request_id, evidence, client, data_dir, at):
    """Download the product-page image, save it, then upload once."""
    existing = media_record(store, request_id, 'featured')
    if existing and existing['state'] in ('uploaded', 'unavailable', 'upload_uncertain'):
        return existing
    url = evidence.get('image_url')
    if not url:
        return save_media(store, _record(request_id, 'featured', state='unavailable',
                                         error='상품 페이지 대표 이미지 없음'))
    try:
        allowed_image_url(url)
        downloaded = download_image(url)
    except ValueError as error:
        return save_media(store, _record(request_id, 'featured', state='unavailable',
                                         error=str(error)))
    saved = save_image(data_dir, request_id, 'featured', downloaded)
    return prepare_media(store, request_id, 'featured',
                         {**downloaded, **saved, 'source_url': url}, client, at)


def prepare_body_image(store, request_id, image_client, wordpress, products, settings,
                       data_dir, at):
    """Generate one illustration, save it, and upload it once."""
    existing = media_record(store, request_id, 'body')
    if existing and existing['state'] in ('uploaded', 'unavailable', 'upload_uncertain'):
        return existing
    if image_client is None:
        return save_media(store, _record(request_id, 'body', state='unavailable',
                                         error='이미지 생성 설정 없음'))
    prompt = image_prompt(products, settings)
    try:
        with store.connect() as c:
            reserve_image_operation(store, c, request_id, image_client.daily_limit, at)
        generated = image_client.generate(prompt, at)
    except ValueError as error:
        return save_media(store, _record(request_id, 'body', state='unavailable',
                                         prompt=prompt, error=str(error)))
    saved = save_image(data_dir, request_id, 'body', generated)
    return prepare_media(store, request_id, 'body',
                         {**generated, **saved, 'prompt': prompt}, wordpress, at)
