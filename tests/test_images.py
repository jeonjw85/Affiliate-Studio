"""Safe product-image candidates and downloads. No arbitrary URL fetching."""
import hashlib
import struct
import pytest

HOST = 'https://image9.coupangcdn.com'


def png_bytes(width, height):
    def chunk(kind, payload):
        return (struct.pack('>I', len(payload)) + kind + payload
                + struct.pack('>I', 0))
    header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IEND', b'')


def jpeg_bytes(width, height):
    app0 = b'\xff\xe0' + struct.pack('>H', 16) + b'JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
    sof = b'\xff\xc0' + struct.pack('>H', 17) + b'\x08' + struct.pack('>HH', height, width) + b'\x03\x01\x11\x00\x02\x11\x01\x03\x11\x01'
    return b'\xff\xd8' + app0 + sof + b'\xff\xd9'


def webp_bytes(width, height):
    return b'RIFF' + struct.pack('<I', 30) + b'WEBPVP8X' + struct.pack('<I', 10) + b'\x00\x00\x00\x00' \
        + (width - 1).to_bytes(3, 'little') + (height - 1).to_bytes(3, 'little') + b'\x00' * 10


class Reply:
    def __init__(self, status=200, body=b'', headers=None, url=None):
        self.status = status
        self.body = body
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}
        self.url = url


def page_with(jsonld=None, og=None, twitter=None):
    import json
    tags = ''
    if jsonld is not None:
        tags += f'<script type="application/ld+json">{json.dumps(jsonld)}</script>'
    if og:
        tags += f'<meta property="og:image" content="{og}">'
    if twitter:
        tags += f'<meta name="twitter:image" content="{twitter}">'
    return {'final_url': f'{HOST}/p.png', 'status': 200,
            'headers': {'content-type': 'text/html'}, 'body': f'<html><head>{tags}</head><body></body></html>'.encode()}


def test_candidate_order_and_single_choice():
    from studio.images import image_url
    page = page_with(jsonld={'@type': 'Product', 'name': 'A', 'image': f'{HOST}/a.jpg'},
                     og=f'{HOST}/og.jpg')
    assert image_url(page) == f'{HOST}/a.jpg'
    assert image_url(page_with(og=f'{HOST}/og.jpg')) == f'{HOST}/og.jpg'
    assert image_url(page_with(twitter=f'{HOST}/tw.jpg')) == f'{HOST}/tw.jpg'
    assert image_url(page_with()) is None


def test_product_parse_includes_image_url():
    from studio.product_info import parse_product
    import json
    body = ('<html><head><script type="application/ld+json">'
            + json.dumps({'@type': 'Product', 'name': '합성', 'image': f'{HOST}/a.jpg'})
            + '</script></head></html>').encode()
    page = Reply(200, body, {'content-type': 'text/html'},
                 'https://www.coupang.com/vp/products/123')
    from datetime import datetime, timezone
    evidence = parse_product(page, datetime(2026, 10, 24, tzinfo=timezone.utc))
    assert evidence['image_url'] == f'{HOST}/a.jpg'


@pytest.mark.parametrize('url', [
    'http://image9.coupangcdn.com/a.jpg',
    'https://evil.test/a.jpg',
    'https://user:pass@image9.coupangcdn.com/a.jpg',
    'data:image/png;base64,AAAA',
    'javascript:alert(1)',
    '/relative/a.jpg',
    'https://image9.coupangcdn.com.evil.test/a.jpg',
    'https://127.0.0.1/a.jpg',
])
def test_rejects_unsafe_image_urls(url):
    from studio.images import allowed_image_url
    with pytest.raises(ValueError):
        allowed_image_url(url)


def test_download_checks_type_size_and_header_dimensions():
    from studio.images import download_image
    png = png_bytes(300, 300)
    result = download_image(f'{HOST}/a.png', opener=lambda url: Reply(
        200, png, {'content-type': 'image/png'}))
    assert (result['width'], result['height']) == (300, 300)
    assert result['content_type'] == 'image/png'
    assert result['sha256'] == hashlib.sha256(png).hexdigest()
    assert result['body'] == png
    jpeg = download_image(f'{HOST}/a.jpg', opener=lambda url: Reply(200, jpeg_bytes(400, 400),
                                                                    {'content-type': 'image/jpeg'}))
    assert (jpeg['width'], jpeg['height']) == (400, 400)
    webp = download_image(f'{HOST}/a.webp', opener=lambda url: Reply(200, webp_bytes(500, 500),
                                                                     {'content-type': 'image/webp'}))
    assert (webp['width'], webp['height']) == (500, 500)


@pytest.mark.parametrize('reply', [
    Reply(200, png_bytes(10, 10), {'content-type': 'image/png'}),
    Reply(200, b'x' * (8 * 1024 * 1024 + 1), {'content-type': 'image/png'}),
    Reply(200, png_bytes(300, 300), {'content-type': 'text/html'}),
    Reply(200, b'<html>nope</html>', {'content-type': 'image/png'}),
    Reply(302, headers={'location': 'https://evil.test/a.png'}),
    Reply(403, b'', {'content-type': 'text/html'}),
])
def test_download_rejects_bad_replies(reply):
    from studio.images import download_image
    with pytest.raises(ValueError):
        download_image(f'{HOST}/a.png', opener=lambda url, r=reply: r)


def test_redirect_must_stay_on_allowed_host():
    from studio.images import download_image
    seen = []
    def opener(url):
        seen.append(url)
        if len(seen) == 1:
            return Reply(302, headers={'location': f'{HOST}/final.png'}, url=url)
        return Reply(200, png_bytes(300, 300), {'content-type': 'image/png'}, url=url)
    result = download_image(f'{HOST}/a.png', opener=opener)
    assert result['url'] == f'{HOST}/final.png'
    assert seen == [f'{HOST}/a.png', f'{HOST}/final.png']
    with pytest.raises(ValueError):
        download_image(f'{HOST}/a.png', opener=lambda url: Reply(
            302, headers={'location': 'https://evil.test/a.png'}, url=url))
