"""One request can carry up to three products; old single-product rows stay valid."""
from datetime import datetime, timezone
import pytest

from automation_fixtures import make_evidence

AT = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)


def make_product(index, state='ready', provider='coupang', name=None, **overrides):
    product = {
        'index': index,
        'provider': provider,
        'affiliate_url': f'https://link.coupang.com/a/fixture-{index}',
        'evidence': make_evidence(name=name or f'합성 상품 {index}'),
        'state': state,
        'revision': 0,
        'question_message_id': None,
        'reason': '',
    }
    product.update(overrides)
    return product


def test_legacy_single_request_reads_as_one_product():
    from studio.automation_products import products_of, with_products
    row = {'id': 'req', 'provider': 'coupang',
           'affiliate_url': 'https://link.coupang.com/a/fixture',
           'evidence': make_evidence(name='기존 상품'), 'state': 'ready'}
    products = products_of(row)
    assert len(products) == 1
    assert products[0]['index'] == 1 and products[0]['state'] == 'ready'
    assert products[0]['affiliate_url'] == row['affiliate_url']
    saved = with_products(row, products)
    assert saved['products'] == products
    assert saved['affiliate_url'] == row['affiliate_url']
    assert saved['provider'] == 'coupang'


def test_partial_ready_derives_ready_and_keeps_awaiting_product():
    from studio.automation_products import derive_state, next_question, ready_products
    products = [make_product(1, 'ready'), make_product(2, 'awaiting_info')]
    assert derive_state(products) == 'ready'
    assert [p['index'] for p in ready_products(products)] == [1]
    assert next_question(products)['index'] == 2
    assert next_question([make_product(1, 'ready')]) is None


def test_empty_and_failed_states():
    from studio.automation_products import derive_state
    assert derive_state([]) == 'failed'
    assert derive_state([make_product(1, 'awaiting_info'), make_product(2, 'awaiting_info')]) == 'awaiting_info'
    assert derive_state([make_product(1, 'failed')]) == 'failed'


def test_providers_are_distinct_and_ordered():
    from studio.automation_products import distinct_providers
    products = [make_product(1, provider='toss'),
                make_product(2, provider='coupang'),
                make_product(3, provider='toss')]
    assert distinct_providers(products) == ['toss', 'coupang']


def test_with_products_rejects_more_than_three():
    from studio.automation_products import with_products
    products = [make_product(index) for index in range(1, 5)]
    with pytest.raises(ValueError, match='3'):
        with_products({'id': 'req'}, products)


def test_with_products_requires_contiguous_indexes():
    from studio.automation_products import with_products
    with pytest.raises(ValueError):
        with_products({'id': 'req'}, [make_product(2)])


def test_products_of_fills_missing_fields_but_keeps_evidence():
    from studio.automation_products import products_of
    row = {'id': 'req', 'products': [
        {'index': 1, 'provider': 'coupang', 'affiliate_url': 'https://link.coupang.com/a/x',
         'evidence': make_evidence(name='A'), 'state': 'ready'},
        {'index': 2, 'provider': 'toss', 'affiliate_url': 'https://toss.im/_m/y',
         'evidence': make_evidence(name='B'), 'state': 'awaiting_info'},
    ]}
    products = products_of(row)
    assert products[0]['revision'] == 0
    assert products[1]['question_message_id'] is None
    assert products[1]['state'] == 'awaiting_info'


def test_question_text_names_the_product_and_request():
    from studio.telegram_inbox import question_text
    product = make_product(2, 'awaiting_info')
    text = question_text({'id': 'req_fixture'}, product)
    assert '2번 상품' in text
    assert 'req_fixture' in text
    assert '상품명' in text and '특징' in text
