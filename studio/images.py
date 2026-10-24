"""Product-page image candidates, bounded downloads and AI illustration generation.

The affiliate URL is never rewritten. Only explicit image metadata from the
already-fetched product page is considered, and every request reuses the
same public-IP pinning and size limits as the product page client.
"""
import base64
import hashlib
import json
import re
import struct
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from datetime import timezone

from .core import safe_url, text

IMAGE_HOST_SUFFIXES = ('coupangcdn.com', 'toss.im', 'toss.shopping')
MAX_BYTES = 8 * 1024 * 1024
MIN_SIDE = 200
BODY_MIN_SIDE = 512
MAX_REDIRECTS = 3
IMAGE_TYPES = ('image/jpeg', 'image/png', 'image/webp')


def allowed_image_url(url):
    """Return the URL only when it is a plain HTTPS image host we accept."""
    value = safe_url(url)
    host = (urlsplit(value).hostname or '').lower()
    if not any(host == suffix or host.endswith('.' + suffix) for suffix in IMAGE_HOST_SUFFIXES):
        raise ValueError('허용된 상품 이미지 주소 아님')
    if urlsplit(value).query or urlsplit(value).fragment:
        raise ValueError('쿼리 없는 이미지 주소 필요')
    return value


def _candidate(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value:
        return _candidate(value[0])
    if isinstance(value, dict):
        return value.get('url') or value.get('@id')
    return None


def image_url(page):
    """Pick one representative image from JSON-LD, then OG, then Twitter."""
    import json
    from .product_info import _Meta, _products
    body = page['body'] if isinstance(page, dict) else page.body
    parser = _Meta()
    try:
        parser.feed(body.decode('utf-8', 'replace'))
    except Exception:
        return None
    for script in parser.scripts:
        try:
            data = json.loads(script)
        except (ValueError, RecursionError):
            continue
        for product in _products(data):
            candidate = _candidate(product.get('image'))
            if candidate:
                try:
                    return allowed_image_url(candidate)
                except ValueError:
                    continue
    for key in ('og:image', 'twitter:image'):
        candidate = parser.meta.get(key)
        if candidate:
            try:
                return allowed_image_url(candidate)
            except ValueError:
                continue
    return None


def _dimensions(body, content_type):
    if body[:8] == b'\x89PNG\r\n\x1a\n':
        if len(body) < 24 or body[12:16] != b'IHDR':
            raise ValueError('PNG 크기 확인 불가')
        return 'image/png', struct.unpack('>II', body[16:24])[0], struct.unpack('>II', body[16:24])[1]
    if body[:2] == b'\xff\xd8':
        index = 2
        while index + 9 < len(body):
            if body[index] != 0xFF:
                index += 1
                continue
            marker = body[index + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                height, width = struct.unpack('>HH', body[index + 5:index + 9])
                return 'image/jpeg', width, height
            if marker in (0xD8, 0xD9):
                index += 2
                continue
            length = struct.unpack('>H', body[index + 2:index + 4])[0]
            index += 2 + length
        raise ValueError('JPEG 크기 확인 불가')
    if body[:4] == b'RIFF' and body[8:12] == b'WEBP':
        chunk = body[12:16]
        if chunk == b'VP8X':
            width = int.from_bytes(body[24:27], 'little') + 1
            height = int.from_bytes(body[27:30], 'little') + 1
            return 'image/webp', width, height
        if chunk == b'VP8 ':
            width, height = struct.unpack('<HH', body[26:30])
            return 'image/webp', width & 0x3FFF, height & 0x3FFF
        if chunk == b'VP8L':
            bits = int.from_bytes(body[21:25], 'little')
            return 'image/webp', (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        raise ValueError('WebP 크기 확인 불가')
    raise ValueError('지원하지 않는 이미지 형식')


def _fetch(url, opener):
    current = allowed_image_url(url)
    for _ in range(MAX_REDIRECTS + 1):
        response = opener(current)
        if response.status in (301, 302, 303, 307, 308):
            location = response.headers.get('location')
            if not location:
                raise ValueError('이미지 이동 주소 없음')
            current = allowed_image_url(urljoin(current, location))
            continue
        return current, response
    raise ValueError('이미지 이동 횟수 초과')


def download_image(url, *, opener=None):
    """Fetch one bounded image and read its dimensions from the header only."""
    from .product_info import _https_get
    opener = opener or (lambda target: _https_get(target, 1e12, MAX_BYTES))
    final_url, response = _fetch(url, opener)
    if response.status != 200:
        raise ValueError('이미지 응답 확인 불가')
    declared = response.headers.get('content-type', '').split(';')[0].strip().lower()
    if declared and declared not in ('image/jpeg', 'image/png', 'image/webp'):
        raise ValueError('지원하지 않는 이미지 형식')
    body = response.body
    if len(body) > MAX_BYTES:
        raise ValueError('이미지 크기 초과')
    content_type, width, height = _dimensions(body, declared)
    if width < MIN_SIDE or height < MIN_SIDE:
        raise ValueError('이미지 최소 크기 미달')
    return {'url': final_url, 'body': body, 'content_type': content_type,
            'width': width, 'height': height, 'sha256': hashlib.sha256(body).hexdigest()}


def _env(environ, key, default=''):
    return (environ.get(key) or default).strip()


class ImageClient:
    """Minimal OpenAI-compatible image client; base64 responses only."""

    def __init__(self, url, model, api_key, daily_limit=3, *, timeout=120):
        u = urlsplit(url)
        if u.scheme != 'https' or not u.hostname or u.username or u.password \
                or '?' in url or '#' in url or any(c.isspace() for c in url):
            raise ValueError('IMAGE_BASE_URL은 로그인 정보·쿼리 없는 HTTPS 기본 주소 필요')
        if type(daily_limit) is not int or not 1 <= daily_limit <= 20:
            raise ValueError('IMAGE_DAILY_LIMIT은 1~20 정수 필요')
        if not isinstance(api_key, str) or not 1 <= len(api_key) <= 4096 \
                or any(not 33 <= ord(c) <= 126 for c in api_key):
            raise ValueError('IMAGE_API_KEY 설정 필요')
        self.url = url.rstrip('/')
        self.model = text(model, 200)
        self._api_key = api_key
        self.daily_limit = daily_limit
        self.timeout = min(max(float(timeout), 1), 180)

    def __repr__(self):
        return f'ImageClient(model={self.model!r}, configured=True)'

    @classmethod
    def from_env(cls, environ):
        mode = _env(environ, 'IMAGE_MODE').lower()
        if not mode:
            return None
        if mode not in ('api', 'local'):
            raise ValueError('IMAGE_MODE는 api 또는 local 필요')
        values = {key: _env(environ, key) for key in
                  ('IMAGE_BASE_URL', 'IMAGE_MODEL', 'IMAGE_API_KEY')}
        if not all(values.values()):
            raise ValueError('IMAGE_BASE_URL·IMAGE_MODEL·IMAGE_API_KEY 모두 설정 필요')
        try:
            limit = int(_env(environ, 'IMAGE_DAILY_LIMIT', '3'))
        except ValueError:
            raise ValueError('IMAGE_DAILY_LIMIT은 정수 필요') from None
        if mode != 'api':
            raise ValueError('로컬 이미지 생성은 지원하지 않음: IMAGE_MODE=api 필요')
        return cls(values['IMAGE_BASE_URL'], values['IMAGE_MODEL'], values['IMAGE_API_KEY'],
                   limit, timeout=_env(environ, 'IMAGE_TIMEOUT_SECONDS', '120'))

    def _post(self, payload):
        headers = {'Authorization': 'Bearer ' + self._api_key}
        with httpx.Client(timeout=self.timeout, follow_redirects=False, trust_env=False,
                          headers=headers) as client:
            with client.stream('POST', self.url + '/images/generations', json=payload) as response:
                if response.status_code != 200:
                    raise ValueError('이미지 생성 응답 상태 확인 필요')
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 16 * 1024 * 1024:
                        raise ValueError('이미지 생성 응답 크기 초과')
        try:
            return json.loads(raw)
        except ValueError:
            raise ValueError('이미지 생성 응답 형식 확인 불가') from None

    def generate(self, prompt, at):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError('이미지 프롬프트 필요')
        payload = {'model': self.model, 'prompt': prompt, 'size': '1024x1024',
                   'n': 1, 'response_format': 'b64_json'}
        try:
            data = self._post(payload)
        except ValueError:
            raise
        except httpx.TimeoutException:
            raise ValueError('이미지 생성 시간 초과') from None
        except httpx.HTTPError:
            raise ValueError('이미지 생성 연결 실패') from None
        if not isinstance(data, dict) or not isinstance(data.get('data'), list) or not data['data']:
            raise ValueError('이미지 생성 결과 확인 불가')
        item = data['data'][0]
        if not isinstance(item, dict) or not isinstance(item.get('b64_json'), str) \
                or 'url' in item:
            raise ValueError('이미지 생성은 b64_json 결과만 사용')
        try:
            body = base64.b64decode(item['b64_json'], validate=True)
        except Exception:
            raise ValueError('이미지 생성 결과 디코딩 실패') from None
        if len(body) > MAX_BYTES:
            raise ValueError('이미지 생성 결과 크기 초과')
        content_type, width, height = _dimensions(body, '')
        if width < BODY_MIN_SIDE or height < BODY_MIN_SIDE:
            raise ValueError('이미지 생성 결과 최소 크기 미달')
        return {'body': body, 'content_type': content_type, 'width': width, 'height': height,
                'sha256': hashlib.sha256(body).hexdigest()}


def image_prompt(products, settings):
    """Illustration prompt from names and audience only; no URLs or prices."""
    names = []
    categories = []
    for product in products:
        evidence = product.get('evidence') or {}
        if evidence.get('name'):
            names.append(evidence['name'])
        if evidence.get('category'):
            categories.append(evidence['category'])
    audience = settings.get('writing', {}).get('audience', '')
    topic = settings.get('writing', {}).get('topic', '')
    parts = ['설명용 삽화, 제품 실물 사진 아님',
             '밝고 단순한 배경의 편집 일러스트, 글 상단용 가로 구도']
    if names:
        parts.append('소개할 상품: ' + ', '.join(names[:3]))
    if categories:
        parts.append('분야: ' + ', '.join(sorted(set(categories))[:3]))
    if topic:
        parts.append('블로그 주제: ' + topic)
    if audience:
        parts.append('대상 독자: ' + audience)
    parts.append('얼굴·사람·로고·상표·글자·텍스트를 넣지 말 것')
    parts.append('상품의 성능·효능·할인을 암시하는 장면을 만들지 말 것')
    return '\n'.join(parts)


def reserve_image_operation(store, c, subject_id, limit, at):
    """Reserve one image credit; separate from the text AI budget."""
    from .publication import process_owner
    import uuid
    subject_id = text(subject_id, 64)
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError('IMAGE_DAILY_LIMIT은 1~20 정수 필요')
    day = at.astimezone(timezone.utc).date().isoformat()
    rows = [row for row in store.rows(c, 'operation') if row.get('type') == 'image']
    if sum(row['created_at'][:10] == day for row in rows) >= limit:
        raise ValueError('오늘의 이미지 생성 한도 소진')
    operation = dict(id=uuid.uuid4().hex, type='image', scope='automation',
                     subject_id=subject_id, state='running',
                     created_at=at.isoformat(), **process_owner())
    store.put(c, 'operation', operation)
    return operation


def save_image(data_dir, request_id, kind, image):
    """Write bytes under data/media with a server-chosen hash filename."""
    directory = Path(data_dir) / 'media'
    directory.mkdir(parents=True, exist_ok=True)
    suffix = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp'}.get(
        image['content_type'])
    if not suffix:
        raise ValueError('지원하지 않는 이미지 형식')
    name = f'{request_id}-{kind}-{image["sha256"][:16]}{suffix}'
    path = directory / name
    if not path.exists():
        temp = path.with_suffix(path.suffix + '.tmp')
        temp.write_bytes(image['body'])
        temp.replace(path)
    return {'path': path, 'local_path': name, 'bytes': len(image['body']),
            'sha256': image['sha256']}
