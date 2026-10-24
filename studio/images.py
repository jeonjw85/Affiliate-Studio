"""Product-page image candidates and bounded image downloads.

The affiliate URL is never rewritten. Only explicit image metadata from the
already-fetched product page is considered, and every request reuses the
same public-IP pinning and size limits as the product page client.
"""
import hashlib
import struct
from urllib.parse import urljoin, urlsplit

from .core import safe_url

IMAGE_HOST_SUFFIXES = ('coupangcdn.com', 'toss.im', 'toss.shopping')
MAX_BYTES = 8 * 1024 * 1024
MIN_SIDE = 200
MAX_REDIRECTS = 3


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
