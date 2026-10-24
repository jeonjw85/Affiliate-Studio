"""Multi-product articles: comparison table, bundle fallback, mixed disclosures."""
from datetime import datetime, timedelta, timezone
import json
import pytest

from automation_fixtures import make_evidence, make_settings

AT = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
SETTINGS = make_settings()


def product(index, provider='coupang', facts=None, name=None, price=None):
    evidence = make_evidence(
        facts=[{'text': text, 'source': 'page', 'observed_at': AT.isoformat()} for text in facts or []],
        name=name or f'합성 상품 {index}', price=price)
    return {'index': index, 'provider': provider,
            'affiliate_url': f'https://link.coupang.com/a/fixture-{index}' if provider == 'coupang'
            else f'https://toss.im/_m/fixture-{index}',
            'evidence': evidence, 'state': 'ready', 'revision': 0,
            'question_message_id': None, 'reason': ''}


def article(**overrides):
    value = {'title': '합성 비교 안내', 'body': '## 확인한 내용\n확인한 사실 하나',
             'body_format': 'markdown-v1', 'evidence_refs': [1], 'style': 'comparison',
             'policy_version': 1}
    value.update(overrides)
    return value


def test_comparison_table_uses_only_common_labelled_fields():
    from studio.writing_policy import comparison_rows
    a = product(1, facts=['용량: 120매', '색상: 흰색'])
    b = product(2, facts=['용량: 100매', '구성: 10개'])
    rows = comparison_rows([a, b])
    assert rows['headers'][0] == '항목'
    assert rows['headers'][1:] == ['합성 상품 1', '합성 상품 2']
    assert [row[0] for row in rows['rows']] == ['용량']
    assert rows['rows'][0][1:] == ['120매', '100매']


def test_comparison_table_includes_matching_prices():
    from studio.writing_policy import comparison_rows
    price = lambda amount: {'amount': amount, 'currency': 'KRW', 'source': 'page', 'observed_at': AT.isoformat()}
    rows = comparison_rows([product(1, facts=['용량: 120매'], price=price(19900)),
                            product(2, facts=['용량: 100매'], price=price(9900))])
    assert ['가격', '19,900원', '9,900원'] in rows['rows']


def test_no_common_fields_renders_bundle_without_table():
    from studio.writing_policy import choose_style, comparison_rows, render_article
    a = product(1, facts=['색상: 흰색'])
    b = product(2, facts=['구성: 10개'])
    assert comparison_rows([a, b]) is None
    assert choose_style([a, b], SETTINGS) == 'bundle'
    rendered = render_article(article(style='bundle'), [a, b], settings=SETTINGS)
    assert '비교표' not in rendered['html']


def test_common_fields_choose_comparison():
    from studio.writing_policy import choose_style
    a = product(1, facts=['용량: 120매'])
    b = product(2, facts=['용량: 100매'])
    assert choose_style([a, b], SETTINGS) == 'comparison'
    assert choose_style([a], SETTINGS) == 'concise'
    assert choose_style([product(1, facts=['용량: 120매', '색상: 흰색'])], SETTINGS) == 'intro'
    assert choose_style([a, b, product(3, facts=['용량: 80매'])], SETTINGS) == 'comparison'


def test_mixed_disclosures_and_links_are_all_present():
    from studio.writing_policy import render_article
    a, b = product(1, provider='coupang'), product(2, provider='toss')
    rendered = render_article(article(), [a, b], settings=SETTINGS)
    first_name = a['evidence']['name']
    assert rendered['html'].index('쿠팡 파트너스') < rendered['html'].index(first_name)
    assert '토스쇼핑' in rendered['html']
    assert a['affiliate_url'] in rendered['html'] and b['affiliate_url'] in rendered['html']
    assert a['affiliate_url'] in rendered['text'] and b['affiliate_url'] in rendered['text']
    assert rendered['html'].count('rel="sponsored nofollow noreferrer"') == 2


def test_single_product_render_keeps_existing_layout():
    from studio.writing_policy import render_article
    one = product(1, facts=['확인한 사실 하나'])
    rendered = render_article(article(style='intro'), [one], settings=SETTINGS)
    assert rendered['text'].rstrip().endswith(one['affiliate_url'])
    assert '정보 출처' in rendered['text']
    assert '비교표' not in rendered['html']


def test_comparison_length_and_ungrounded_numbers():
    from studio.writing_policy import check_article
    two = [product(1, facts=['무게: 999kg']), product(2, facts=['무게: 1kg'])]
    assert check_article(article(body='확인 ' * 1600), two)
    assert check_article(article(body='무게 777kg'), two)
    assert check_article(article(body='무게 999kg'), two) == []


def test_evidence_refs_must_belong_to_a_real_product():
    from studio.writing_policy import check_article
    two = [product(1, facts=['사실 A']), product(2, facts=['사실 B'])]
    assert check_article(article(body='사실 A', evidence_refs=[1]), two) == []
    assert check_article(article(body='사실 A', evidence_refs=[9]), two)


def test_prepare_products_drops_only_expired_prices():
    from studio.writing_policy import prepare_products
    old = {'amount': 1000, 'currency': 'KRW', 'source': 'page', 'observed_at': AT.isoformat()}
    fresh = {'amount': 2000, 'currency': 'KRW', 'source': 'page',
             'observed_at': (AT + timedelta(hours=23)).isoformat()}
    products = [product(1, price=old), product(2, price=fresh)]
    later = AT + timedelta(hours=24, minutes=1)
    prepared = prepare_products(products, later)
    assert prepared[0]['evidence']['price'] is None
    assert prepared[1]['evidence']['price'] == fresh
    assert products[0]['evidence']['price'] == old


def test_ai_material_has_no_urls_and_uses_product_indexes():
    from studio.writing_policy import compose
    class AI:
        mode = 'local'
        model = 'fixture'
        def request(self, method, path, payload=None):
            self.payload = payload
            answer = {'title': '비교 안내', 'body': '## 확인\n확인한 사실', 'evidence_refs': [1, 2]}
            return {'choices': [{'message': {'content': json.dumps(answer)}, 'finish_reason': 'stop'}]}
    ai = AI()
    compose(ai, [product(1, facts=['용량: 120매']), product(2, facts=['용량: 100매'])], SETTINGS)
    material = json.loads(ai.payload['messages'][1]['content'])
    assert [item['index'] for item in material['products']] == [1, 2]
    assert 'affiliate_url' not in json.dumps(material)
    assert 'https://' not in json.dumps(material)
