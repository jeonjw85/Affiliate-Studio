"""Product list inside one automation request.

Old single-product rows only have top-level provider/affiliate_url/evidence.
Everything that reads or writes a request goes through this module so both
shapes behave the same and no caller has to guess which one it has.
"""
PRODUCT_LIMIT = 3
PRODUCT_STATES = ('ready', 'awaiting_info', 'failed')


def _defaults(index, product):
    return {
        'index': index,
        'provider': product.get('provider', ''),
        'affiliate_url': product.get('affiliate_url', ''),
        'evidence': product.get('evidence') or {},
        'state': product.get('state', 'awaiting_info'),
        'revision': product.get('revision', 0),
        'question_message_id': product.get('question_message_id'),
        'reason': product.get('reason', ''),
    }


def products_of(request):
    """Return the request's products, converting a legacy row to one item."""
    products = request.get('products')
    if products:
        return [_defaults(index, product) for index, product in enumerate(products, 1)]
    if not request.get('affiliate_url') and not request.get('evidence'):
        return []
    legacy = _defaults(1, {
        'provider': request.get('provider', ''),
        'affiliate_url': request.get('affiliate_url', ''),
        'evidence': request.get('evidence') or {},
        'state': request.get('state', 'awaiting_info'),
        'revision': request.get('revision', 0),
        'question_message_id': request.get('question_message_id'),
        'reason': request.get('reason', ''),
    })
    if legacy['state'] not in PRODUCT_STATES:
        legacy['state'] = 'awaiting_info' if legacy['state'] == 'awaiting_info' else 'ready'
    return [legacy]


def with_products(request, products):
    """Copy of the request with the product list and compatibility fields."""
    normalized = []
    for position, product in enumerate(products, 1):
        if 'index' in product and product['index'] != position:
            raise ValueError('상품 순번은 1부터 연속이어야 함')
        normalized.append(_defaults(position, product))
    products = normalized
    if not products:
        raise ValueError('상품 하나 이상 필요')
    if len(products) > PRODUCT_LIMIT:
        raise ValueError(f'한 요청에는 상품 최대 {PRODUCT_LIMIT}개까지 가능')
    first = products[0]
    return dict(request, products=products, provider=first['provider'],
                affiliate_url=first['affiliate_url'], evidence=first['evidence'])


def ready_products(products):
    return [product for product in products if product.get('state') == 'ready']


def derive_state(products):
    if not products:
        return 'failed'
    states = {product.get('state') for product in products}
    if 'ready' in states:
        return 'ready'
    if 'awaiting_info' in states:
        return 'awaiting_info'
    return 'failed'


def distinct_providers(products):
    ordered = []
    for product in products:
        provider = product.get('provider')
        if provider and provider not in ordered:
            ordered.append(provider)
    return ordered


def next_question(products):
    return next((product for product in products
                 if product.get('state') == 'awaiting_info'
                 and not product.get('question_message_id')), None)


def request_providers(request):
    return distinct_providers(products_of(request))
