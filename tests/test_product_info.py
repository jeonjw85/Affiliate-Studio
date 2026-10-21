"""Bounded public-page inspection: pinned DNS, byte limits, evidence extraction."""
from datetime import datetime, timezone
import gzip
import ipaddress
import json
import pytest

AT = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
COUPANG_LINK = 'https://link.coupang.com/a/fixture?subId=case%2B1'
COUPANG_FINAL = 'https://www.coupang.com/vp/products/123?itemId=456&vendorItemId=789'
TOSS_LINK = 'https://toss.im/_m/fixture'
TOSS_FINAL = 'https://toss.shopping/t/9876?k=fixture&referrer=affiliate'


class Reply:
    def __init__(self, status=200, body=b'', headers=None, url=None):
        self.status = status
        self.body = body
        self.headers = headers or {}
        self.url = url


def page_html(**meta):
    tags = ''.join(f'<meta property="{key}" content="{value}">' for key, value in meta.items())
    return f'<html><head>{tags}</head><body>fixture</body></html>'.encode()


def jsonld(product):
    return (f'<script type="application/ld+json">{json.dumps(product)}</script>'
            '<html><head></head><body></body></html>').encode()


def test_blocked_landing_retains_original_link_and_partial_identity():
    from studio.product_info import inspect_link
    def opener(url, deadline, limit):
        if url == COUPANG_LINK:
            return Reply(302, headers={'location': COUPANG_FINAL})
        assert url == COUPANG_FINAL
        return Reply(403, b'<html>Access Denied</html>')
    result = inspect_link(COUPANG_LINK, AT, opener=opener)
    assert result['outcome'] == 'needs_info'
    assert result['affiliate_url'] == COUPANG_LINK
    assert result['evidence']['product_key'] == 'coupang:123:456'
    assert result['evidence']['name'] == ''
    assert result['evidence']['price'] is None
    assert result['evidence']['facts'] == []


def test_jsonld_and_og_use_only_matching_product():
    from studio.product_info import parse_product
    product = {
        '@context': 'https://schema.org', '@type': 'Product',
        'name': '합성 물티슈', 'description': '120매 구성입니다.',
        'offers': {'@type': 'Offer', 'price': '11020', 'priceCurrency': 'KRW'},
    }
    page = Reply(200, jsonld(product), url=COUPANG_FINAL)
    evidence = parse_product(page, AT)
    assert evidence['name'] == '합성 물티슈'
    assert evidence['product_key'] == 'coupang:123:456'
    assert evidence['price'] == {'amount': 11020, 'currency': 'KRW', 'source': 'page',
                                'observed_at': AT.isoformat()}
    assert [fact['text'] for fact in evidence['facts']] == ['120매 구성입니다.']

    other = Reply(200, jsonld(dict(product, name='다른 상품', sku='999')), url=COUPANG_FINAL)
    assert parse_product(other, AT)['name'] == ''

    og = Reply(200, page_html(**{'og:title': 'OG 이름만'}), url=COUPANG_FINAL)
    assert parse_product(og, AT)['name'] == 'OG 이름만'
    assert parse_product(og, AT)['facts'] == []

    priced = Reply(200, jsonld(dict(product, offers={'@type': 'AggregateOffer',
                                                     'lowPrice': '1', 'highPrice': '2'})), url=COUPANG_FINAL)
    assert parse_product(priced, AT)['price'] is None
    foreign = Reply(200, jsonld(dict(product, offers={'@type': 'Offer', 'price': '9',
                                                      'priceCurrency': 'USD'})), url=COUPANG_FINAL)
    assert parse_product(foreign, AT)['price'] is None


def test_pinned_connection_blocks_private_and_rebinding(monkeypatch):
    from studio import product_info
    connections = []
    def private_resolver(host, port):
        return [ipaddress.ip_address('10.0.0.5')]
    with pytest.raises(ValueError, match='공개'):
        product_info._https_get(COUPANG_FINAL, deadline=1e9, limit=1024, resolver=private_resolver,
                                connector=lambda *args: connections.append(args))
    assert connections == []
    for address in ('127.0.0.1', '169.254.169.254', '::1', '::ffff:10.0.0.1', '0.0.0.0'):
        with pytest.raises(ValueError):
            product_info._https_get(COUPANG_FINAL, deadline=1e9, limit=1024,
                                    resolver=lambda host, port, a=address: [ipaddress.ip_address(a)],
                                    connector=lambda *args: connections.append(args))
    assert connections == []

    calls = []
    def public_resolver(host, port):
        calls.append(host)
        return [ipaddress.ip_address('93.184.216.34')]
    def connector(host, ip, port, timeout):
        calls.append((host, str(ip)))
        return Reply(200, b'ok')
    result = product_info._https_get(COUPANG_FINAL, deadline=1e9, limit=1024,
                                     resolver=public_resolver, connector=connector)
    assert result.body == b'ok'
    assert calls == ['www.coupang.com', ('www.coupang.com', '93.184.216.34')]

    def nat64_resolver(host, port):
        return [ipaddress.ip_address('64:ff9b::5db8:d822')]
    def nat64_connector(host, ip, port, timeout):
        return Reply(200, b'ok')
    assert product_info._https_get(COUPANG_FINAL, deadline=1e9, limit=1024,
                                   resolver=nat64_resolver, connector=nat64_connector).body == b'ok'
    def nat64_private(host, port):
        return [ipaddress.ip_address('64:ff9b::a00:5')]
    with pytest.raises(ValueError, match='공개'):
        product_info._https_get(COUPANG_FINAL, deadline=1e9, limit=1024,
                                resolver=nat64_private, connector=nat64_connector)


def test_total_deadline_redirect_limit_and_decoded_size():
    from studio.product_info import fetch_page
    clock = [0.0]
    def opener(url, deadline, limit):
        clock[0] += 11
        if clock[0] > 20:
            raise TimeoutError('deadline')
        return Reply(302, headers={'location': 'https://coupa.ng/b/fixture'}, url=url)
    with pytest.raises(ValueError, match='시간'):
        fetch_page(COUPANG_LINK, opener=opener, clock=lambda: clock[0])

    def redirect(url, deadline, limit):
        return Reply(302, headers={'location': COUPANG_LINK})
    with pytest.raises(ValueError, match='이동'):
        fetch_page(COUPANG_LINK, opener=redirect, clock=lambda: 0.0)

    payload = b'x' * (2 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match='크기'):
        fetch_page(COUPANG_LINK, opener=lambda url, deadline, limit: Reply(
            200, gzip.compress(payload), {'content-encoding': 'gzip'}), clock=lambda: 0.0)
    limit = 2 * 1024 * 1024
    exact = fetch_page(COUPANG_LINK, opener=lambda url, deadline, limit: Reply(
        200, gzip.compress(b'y' * limit), {'content-encoding': 'gzip'}), clock=lambda: 0.0)
    assert len(exact['body']) == limit


def test_redirects_stay_inside_the_same_service():
    from studio.product_info import fetch_page
    with pytest.raises(ValueError, match='이동'):
        fetch_page(COUPANG_LINK, opener=lambda url, deadline, limit: Reply(
            302, headers={'location': 'https://evil.test/a'}), clock=lambda: 0.0)
    with pytest.raises(ValueError, match='이동'):
        fetch_page(COUPANG_LINK, opener=lambda url, deadline, limit: Reply(
            302, headers={'location': 'http://www.coupang.com/vp/products/1'}), clock=lambda: 0.0)
    def redirected(url, deadline, limit):
        if url == COUPANG_LINK:
            return Reply(302, headers={'location': COUPANG_FINAL}, url=url)
        return Reply(200, page_html(**{'og:title': '상품'}), url=url)
    result = fetch_page(COUPANG_LINK, opener=redirected, clock=lambda: 0.0)
    assert result['final_url'] == COUPANG_FINAL
    assert result['status'] == 200


def test_description_merges_only_supplied_fields():
    from studio.product_info import merge_evidence, parse_description
    supplied = parse_description('상품명: 합성 상품\n특징: 120매 구성\n특징: 캡형\n- 120매 구성\n가격: 25,900원\n대상: 아이 키우는 집', AT)
    assert supplied['name'] == '합성 상품'
    assert [fact['text'] for fact in supplied['facts']] == ['120매 구성', '캡형']
    assert supplied['price']['amount'] == 25900
    assert supplied['audience'] == '아이 키우는 집'
    assert all(fact['source'] == 'user' for fact in supplied['facts'])
    page = {'name': '페이지 이름', 'facts': [{'text': '페이지 사실', 'source': 'page', 'observed_at': AT.isoformat()}],
            'price': None, 'category': '', 'audience': '', 'source_url': COUPANG_FINAL,
            'product_key': 'coupang:123:456', 'conflicts': []}
    merged = merge_evidence(page, supplied)
    assert merged['name'] == '페이지 이름'
    assert merged['conflicts'] and merged['conflicts'][0]['field'] == 'name'
    assert [fact['text'] for fact in merged['facts']] == ['페이지 사실', '120매 구성', '캡형']
    assert 'https://' not in ' '.join(fact['text'] for fact in parse_description(
        '특징: 링크 https://link.coupang.com/a/fixture 확인', AT)['facts'])


def test_unsupported_and_unreachable_links():
    from studio.product_info import classify_link, inspect_link
    assert classify_link(COUPANG_LINK) == 'coupang'
    assert classify_link(TOSS_LINK) == 'toss'
    assert classify_link('https://www.coupang.com/vp/products/1') == 'coupang'
    for url in ('https://evil.test/a', 'http://link.coupang.com/a/x', 'https://127.0.0.1/a',
                'https://link.coupang.com.evil.test/a', 'https://toss.shopping/t/0'):
        with pytest.raises(ValueError):
            classify_link(url)
    result = inspect_link(COUPANG_LINK, AT, opener=lambda *args: Reply(503))
    assert result['outcome'] == 'needs_info'
    assert result['affiliate_url'] == COUPANG_LINK

    def timeout(*args):
        raise TimeoutError()
    assert inspect_link(COUPANG_LINK, AT, opener=timeout)['outcome'] == 'needs_info'
