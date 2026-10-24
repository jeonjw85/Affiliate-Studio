"""Read public product pages without provider APIs.

The supplied affiliate URL is never rewritten. Redirect resolution is limited
to the short-link hosts, every connection is pinned to a validated public IP,
and only small, bounded HTML responses are parsed for explicit evidence.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from html import unescape
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit
import zlib

from .core import safe_url

TIMEOUT = 20.0
LIMIT = 2 * 1024 * 1024
MAX_REDIRECTS = 5
PRODUCT_HOSTS = {'www.coupang.com', 'm.coupang.com'}
SHORT_HOSTS = {'link.coupang.com', 'coupa.ng', 'toss.im'}
ENTRY_HOSTS = PRODUCT_HOSTS | SHORT_HOSTS | {'toss.shopping'}
COUPANG_ID = re.compile(r'/vp/products/([1-9][0-9]{0,18})/?')
TOSS_ID = re.compile(r'/t/([1-9][0-9]{0,18})/?')
NAT64_PREFIX = ipaddress.ip_network('64:ff9b::/96')
RESOLVER = ThreadPoolExecutor(max_workers=2, thread_name_prefix='product-dns')


def provider_for(url):
    u = urlsplit(url)
    host = (u.hostname or '').lower()
    if host in ENTRY_HOSTS or host.endswith('.coupang.com'):
        if host.startswith(('link.coupang', 'coupa.')) or host in PRODUCT_HOSTS:
            return 'coupang'
    if host == 'toss.im' or host == 'toss.shopping':
        return 'toss'
    raise ValueError('쿠팡 또는 토스 제휴 링크 필요')


def product_key(url, provider):
    u = urlsplit(url)
    host = (u.hostname or '').lower()
    if provider == 'coupang':
        match = COUPANG_ID.fullmatch(u.path)
        if not match or host not in PRODUCT_HOSTS:
            return None
        from urllib.parse import parse_qs
        item = parse_qs(u.query).get('itemId', [''])[0]
        return f'coupang:{match[1]}' + (f':{item}' if item.isdigit() and item != '0' else '')
    match = TOSS_ID.fullmatch(u.path)
    return f'toss:{match[1]}' if match and host == 'toss.shopping' else None


def classify_link(url):
    value = safe_url(url)
    provider = provider_for(value)
    u = urlsplit(value)
    if u.query and u.hostname == 'toss.shopping':
        from urllib.parse import parse_qs
        query = parse_qs(u.query)
        if not (query.get('k') or query.get('referrer')):
            raise ValueError('토스 제휴 링크의 추적 정보 확인 필요')
    if provider == 'coupang' and u.hostname in PRODUCT_HOSTS and not product_key(value, provider):
        raise ValueError('쿠팡 상품 URL 형식 확인 필요')
    if provider == 'toss' and u.hostname == 'toss.shopping' and not product_key(value, provider):
        raise ValueError('토스 상품 URL 형식 확인 필요')
    return provider


def _resolve(host, resolver, deadline):
    future = RESOLVER.submit(resolver, host, 443)
    try:
        return future.result(timeout=max(0.001, deadline - time.monotonic()))
    except Exception:
        future.cancel()
        raise ValueError('상품 페이지 조회 시간 초과') from None


def _public_ip(value):
    try:
        address = ipaddress.ip_address(str(value))
    except ValueError:
        raise ValueError('공개 주소 확인 불가') from None
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    elif address in NAT64_PREFIX:
        # NAT64's well-known prefix is globally routed; judge the embedded
        # IPv4 destination rather than the reserved-by-Python wrapper.
        address = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    if not address.is_global or address.is_multicast or address.is_reserved:
        raise ValueError('공개 인터넷 주소만 조회 가능')
    return address


def _default_resolver(host, port):
    return [ipaddress.ip_address(item[4][0])
            for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, ip, timeout):
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((str(self._ip), 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


class Reply:
    def __init__(self, status, body=b'', headers=None, url=None):
        self.status = status
        self.body = body
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}
        self.url = url


def _https_get(url, deadline, limit, *, resolver=None, connector=None):
    """Fetch one HTTPS response through validated, pinned public DNS."""
    u = urlsplit(url)
    if u.scheme != 'https' or not u.hostname or (u.port or 443) != 443:
        raise ValueError('HTTPS 공개 주소 필요')
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError('상품 페이지 조회 시간 초과')
    addresses = _resolve(u.hostname, resolver or _default_resolver, deadline)
    public = [_public_ip(address) for address in addresses]
    if not public:
        raise ValueError('공개 주소 확인 불가')
    # Prefer IPv4 so hosts that also publish NAT64 addresses still work on
    # networks without IPv6 egress; fall through the rest on connect failure.
    public.sort(key=lambda address: (address.version != 4, str(address)))
    if connector is not None:
        return connector(u.hostname, public[0], 443, remaining)
    path = (u.path or '/') + ('?' + u.query if u.query else '')
    error = None
    for ip in public:
        conn = _PinnedHTTPSConnection(u.hostname, ip, max(0.001, deadline - time.monotonic()))
        try:
            conn.request('GET', path, headers={
                'Host': u.hostname, 'Accept-Encoding': 'gzip',
                'User-Agent': 'AffiliateStudio/0.4', 'Connection': 'close'})
            response = conn.getresponse()
            body = response.read(limit + 1)
            return Reply(response.status, body, dict(response.getheaders()), url)
        except (OSError, http.client.HTTPException) as exc:
            error = exc
        finally:
            conn.close()
        if time.monotonic() >= deadline:
            break
    raise ValueError('상품 페이지 연결 실패') from error


def _decode(response, limit):
    body = response.body
    if len(body) > limit:
        raise ValueError('상품 페이지 응답 크기 초과')
    encoding = response.headers.get('content-encoding', '').lower()
    if encoding == 'gzip':
        try:
            decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
            body = decompressor.decompress(body, limit + 1)
        except zlib.error:
            raise ValueError('상품 페이지 압축 해제 실패') from None
    if len(body) > limit:
        raise ValueError('상품 페이지 응답 크기 초과')
    return body


def fetch_page(url, *, opener=None, clock=time.monotonic):
    """Follow short links to one product page within a shared deadline."""
    deadline = clock() + TIMEOUT
    fetch = opener or (lambda target, deadline, limit: _https_get(target, deadline, limit))
    current = safe_url(url)
    provider = provider_for(current)
    final_url, status, headers, body = current, None, {}, b''
    for hop in range(MAX_REDIRECTS + 1):
        if clock() > deadline:
            raise ValueError('상품 페이지 조회 시간 초과')
        try:
            response = fetch(current, deadline, LIMIT)
        except ValueError:
            raise
        except (TimeoutError, OSError, http.client.HTTPException):
            raise ValueError('상품 페이지 조회 시간 초과') from None
        status = response.status
        headers = {key.lower(): value for key, value in (response.headers or {}).items()}
        body = _decode(response, LIMIT)
        final_url = response.url or current
        location = headers.get('location')
        if status not in (301, 302, 303, 307, 308) or not location:
            break
        target = urljoin(current, location)
        try:
            same_service = provider_for(target) == provider
        except ValueError:
            same_service = False
        if urlsplit(target).scheme != 'https' or not same_service:
            raise ValueError('허용되지 않은 상품 페이지 이동')
        current = safe_url(target)
    else:
        raise ValueError('상품 페이지 이동 횟수 초과')
    return {'final_url': final_url, 'status': status, 'headers': headers, 'body': body}


class _Meta(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.scripts, self._script = {}, [], None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'meta':
            key = values.get('property') or values.get('name')
            if key and 'content' in values:
                self.meta[key.lower()] = values['content']
        elif tag == 'script' and (values.get('type') or '').lower() == 'application/ld+json':
            self._script = []

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self._script is not None:
            self.scripts.append(''.join(self._script))
            self._script = None


def _products(node):
    if isinstance(node, list):
        for item in node:
            yield from _products(item)
    elif isinstance(node, dict):
        types = node.get('@type')
        types = types if isinstance(types, list) else [types]
        if 'Product' in types or 'ProductGroup' in types:
            yield node
        for key in ('@graph', 'itemListElement', 'mainEntity'):
            if key in node:
                yield from _products(node[key])


def _amount(value):
    if isinstance(value, str) and re.fullmatch(r'[0-9]{1,9}(?:\.[0-9]{1,2})?', value.strip()):
        value = float(value)
    if type(value) not in (int, float) or not 0 < value <= 100000000 or int(value) != value:
        return None
    return int(value)


def _clean(value):
    return re.sub(r'https?://\S+', '', str(value)).strip()


def parse_product(page, at):
    """Extract only explicit product fields; conflicting values are dropped."""
    parser = _Meta()
    try:
        parser.feed(page.body.decode('utf-8', 'replace'))
    except Exception:
        raise ValueError('상품 페이지 형식 확인 불가') from None
    candidates = []
    for script in parser.scripts:
        try:
            candidates.extend(_products(json.loads(script)))
        except (ValueError, RecursionError):
            continue
    key = product_key(page.url, provider_for(page.url)) if page.url else None
    selected = None
    for candidate in candidates:
        identifiers = {str(candidate.get(name)) for name in ('sku', 'productID', 'productId', 'gtin')
                       if candidate.get(name) is not None}
        if key:
            tail = key.split(':')
            if identifiers and not any(item in tail[1:] for item in identifiers):
                continue
        selected = candidate
        break
    name = _clean(selected.get('name', '')) if selected else _clean(parser.meta.get('og:title', ''))
    facts = []
    price = None
    if selected:
        description = _clean(selected.get('description', ''))
        if description:
            facts.append(description)
        offer = selected.get('offers')
        if isinstance(offer, list):
            offer = offer[0] if len(offer) == 1 else {}
        if isinstance(offer, dict) and offer.get('@type') == 'Offer' \
                and offer.get('priceCurrency') == 'KRW' and _amount(offer.get('price')) is not None:
            price = {'amount': _amount(offer['price']), 'currency': 'KRW',
                     'source': 'page', 'observed_at': at.isoformat()}
    category = _clean(selected.get('category', '')) if selected else ''
    from .images import image_url
    try:
        image = image_url(page)
    except Exception:
        image = None
    return {'name': name[:160], 'facts': [
        {'text': fact[:500], 'source': 'page', 'observed_at': at.isoformat()} for fact in facts[:20]],
        'price': price, 'category': category[:100], 'audience': '', 'source_url': page.url,
        'product_key': key, 'conflicts': [], 'image_url': image}


def parse_description(text, at):
    """Parse the user's reply into evidence; URLs never become facts."""
    if not isinstance(text, str):
        raise ValueError('상품 설명 텍스트 필요')
    result = {'name': '', 'facts': [], 'price': None, 'category': '', 'audience': '',
              'source_url': None, 'product_key': None, 'conflicts': []}
    seen = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(('요청 ', '요청:')):
            continue
        if line.startswith(('상품명:', '상품명：')):
            result['name'] = _clean(line.split(':', 1)[-1] if ':' in line else line.split('：', 1)[-1])[:160]
        elif line.startswith(('대상:', '대상：')):
            result['audience'] = _clean(line.split(':', 1)[-1] if ':' in line else line.split('：', 1)[-1])[:500]
        elif line.startswith(('가격:', '가격：')):
            match = re.search(r'([0-9][0-9,]{0,12})\s*원?', line)
            if match and _amount(match[1].replace(',', '')) is not None:
                result['price'] = {'amount': _amount(match[1].replace(',', '')), 'currency': 'KRW',
                                   'source': 'user', 'observed_at': at.isoformat()}
        else:
            value = line
            for prefix in ('특징:', '특징：', '- ', '* '):
                if value.startswith(prefix):
                    value = value[len(prefix):]
                    break
            value = _clean(value)[:500]
            if value and value not in seen:
                seen.add(value)
                result['facts'].append({'text': value, 'source': 'user', 'observed_at': at.isoformat()})
    return result


def merge_evidence(existing, supplied):
    """Keep page evidence, add supplied facts, and record disagreements."""
    merged = dict(existing)
    merged['facts'] = list(existing['facts'])
    seen = {fact['text'] for fact in merged['facts']}
    for fact in supplied['facts']:
        if fact['text'] not in seen:
            seen.add(fact['text'])
            merged['facts'].append(fact)
    merged['facts'] = merged['facts'][:20]
    conflicts = list(existing.get('conflicts', []))
    for field in ('name', 'audience', 'category'):
        if existing.get(field) and supplied.get(field) and existing[field] != supplied[field]:
            conflicts.append({'field': field, 'page': existing[field], 'user': supplied[field]})
        elif not merged.get(field) and supplied.get(field):
            merged[field] = supplied[field]
    if existing.get('price') and supplied.get('price') and existing['price']['amount'] != supplied['price']['amount']:
        conflicts.append({'field': 'price', 'page': existing['price']['amount'],
                          'user': supplied['price']['amount']})
    elif not merged.get('price') and supplied.get('price'):
        merged['price'] = supplied['price']
    merged['conflicts'] = conflicts
    return merged


def inspect_link(url, at, *, opener=None):
    """Classify a saved affiliate link and collect bounded evidence."""
    affiliate = safe_url(url)
    provider = classify_link(affiliate)
    evidence = {'name': '', 'facts': [], 'price': None, 'category': '', 'audience': '',
                'source_url': None, 'product_key': product_key(affiliate, provider),
                'conflicts': []}
    try:
        page = fetch_page(affiliate, opener=opener)
    except (ValueError, OSError, TimeoutError):
        return {'provider': provider, 'affiliate_url': affiliate, 'evidence': evidence,
                'outcome': 'needs_info', 'reason': '상품 페이지 조회 실패'}
    evidence['product_key'] = product_key(page['final_url'], provider) or evidence['product_key']
    if page['status'] >= 400:
        return {'provider': provider, 'affiliate_url': affiliate, 'evidence': evidence,
                'outcome': 'needs_info', 'reason': '상품 페이지 접근 제한'}
    try:
        parsed = parse_product(Reply(page['status'], page['body'], page['headers'], page['final_url']), at)
    except ValueError:
        return {'provider': provider, 'affiliate_url': affiliate, 'evidence': evidence,
                'outcome': 'needs_info', 'reason': '상품 정보 추출 실패'}
    evidence.update({key: parsed[key] for key in ('name', 'facts', 'price', 'category',
                                                  'source_url', 'product_key', 'conflicts')})
    outcome = 'ready' if evidence['name'] and evidence['facts'] else 'needs_info'
    return {'provider': provider, 'affiliate_url': affiliate, 'evidence': evidence,
            'outcome': outcome, 'reason': '' if outcome == 'ready' else '상품 정보 부족'}
