"""Automatic article policy: style choice, AI prompts, checks and rendering."""
from datetime import datetime, timedelta, timezone
import json
import pytest

from automation_fixtures import make_evidence, make_settings

AT = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
LINK = 'https://link.coupang.com/a/fixture'


def facts(count, source='page'):
    return [{'text': f'확인한 사실 {index}', 'source': source, 'observed_at': AT.isoformat()}
            for index in range(1, count + 1)]


class FakeAI:
    model = 'fixture-model'
    daily_limit = 10

    def __init__(self, answer=None):
        self.answer = answer or {'title': '근거 기반 안내', 'body': '## 확인한 내용\n확인한 사실 1을 살펴보세요.',
                                 'evidence_refs': [1]}
        self.requests = []

    def request(self, method, path, payload=None):
        self.requests.append((method, path, payload))
        return {'choices': [{'message': {'content': json.dumps(self.answer)}, 'finish_reason': 'stop'}]}


def test_style_selection_and_prompt_composition():
    from studio.writing_policy import choose_style
    settings = make_settings()
    assert choose_style(make_evidence(facts=facts(1)), settings) == 'concise'
    assert choose_style(make_evidence(facts=facts(2)), settings) == 'intro'
    assert choose_style(make_evidence(facts=facts(3)), settings) == 'intro'
    assert choose_style(make_evidence(facts=facts(3)), make_settings(writing={'purpose': '첫 구매'})) == 'guide'

    from studio.writing_policy import compose
    ai = FakeAI()
    result = compose(ai, make_evidence(facts=facts(3), category='생활용품'), make_settings(
        writing={'extra_instructions': ['짧은 문단 사용'], 'category_instructions': {'생활용품': '구성과 사용법 중심'}}))
    assert result['style'] == 'intro'
    assert result['body_format'] == 'markdown-v1'
    method, path, payload = ai.requests[0]
    assert (method, path) == ('POST', '/chat/completions')
    system = payload['messages'][0]['content']
    assert '자료' in system and 'JSON' in payload['messages'][0]['content']
    assert '추가 지침' in system and '짧은 문단 사용' in system
    assert '카테고리 지침' in system and '구성과 사용법 중심' in system
    data = json.loads(payload['messages'][1]['content'])
    assert 'affiliate_url' not in json.dumps(data)
    assert 'price' not in json.dumps(data)
    assert payload['response_format']['json_schema']['strict'] is True
    assert set(payload['response_format']['json_schema']['schema']['properties']) == {
        'title', 'body', 'evidence_refs'}


def test_checks_reject_fabricated_claims_and_refs():
    from studio.writing_policy import check_article
    evidence = make_evidence(facts=facts(2))
    good = {'title': '확인한 사실 1 안내', 'body': '## 구성\n확인한 사실 1과 확인한 사실 2를 확인하세요.',
            'evidence_refs': [1, 2], 'style': 'intro'}
    assert check_article(good, evidence) == []
    bad_cases = [
        {'title': '안내', 'body': '무게는 999kg입니다.', 'evidence_refs': [1], 'style': 'intro'},
        {'title': '안내', 'body': '확인한 사실 1', 'evidence_refs': [3], 'style': 'intro'},
        {'title': '안내', 'body': '<script>alert(1)</script>', 'evidence_refs': [1], 'style': 'intro'},
        {'title': '안내', 'body': '[링크](https://evil.test)', 'evidence_refs': [1], 'style': 'intro'},
        {'title': '안내', 'body': '써보니 정말 좋았습니다.', 'evidence_refs': [1], 'style': 'intro'},
        {'title': '안내', 'body': '확인한 사실 1\n' * 400, 'evidence_refs': [1], 'style': 'intro'},
    ]
    for case in bad_cases:
        assert check_article(case, evidence), case
    concise = {'title': '짧은 안내', 'body': '확인한 사실 1만 있는 짧은 글입니다.', 'evidence_refs': [1], 'style': 'concise'}
    assert check_article(concise, make_evidence(facts=facts(1))) == []
    long_concise = {'title': '짧은 안내', 'body': '확인한 사실 1 ' * 120, 'evidence_refs': [1], 'style': 'concise'}
    assert check_article(long_concise, make_evidence(facts=facts(1)))


def test_render_preserves_original_link_and_source_type():
    from studio.writing_policy import render_article
    evidence = make_evidence(facts=facts(2, source='user'), name='합성 <상품>')
    article = {'title': '합성 <제목>', 'body': '## 구성\n**확인한 사실 1**\n\n확인한 사실 2', 'evidence_refs': [1, 2],
               'style': 'intro'}
    rendered = render_article(article, evidence, 'coupang', LINK,
                              settings=make_settings(writing={'topic': '생활용품'}))
    assert 'href="https://link.coupang.com/a/fixture"' in rendered['html']
    assert 'rel="sponsored nofollow noreferrer"' in rendered['html']
    assert '&lt;제목&gt;' in rendered['html'] and '<제목>' not in rendered['html']
    assert '사용자 제공' in rendered['html']
    assert rendered['html'].index('쿠팡 파트너스') < rendered['html'].index('확인한 사실 1')
    assert '합성 <상품>' in rendered['text']
    assert rendered['text'].rstrip().endswith(LINK)
    blocked = render_article(dict(article, body='<script>alert(1)</script> 확인한 사실 1'),
                             evidence, 'coupang', LINK, settings=make_settings())
    assert '<script>' not in blocked['html']


def test_expired_price_is_removed_for_the_publish_instant():
    from studio.writing_policy import prepare_evidence
    evidence = make_evidence(facts=facts(2), price={'amount': 19900, 'currency': 'KRW',
                                                    'source': 'page', 'observed_at': AT.isoformat()})
    assert prepare_evidence(evidence, AT + timedelta(hours=24))['price'] is not None
    assert prepare_evidence(evidence, AT + timedelta(hours=24, seconds=1))['price'] is None
    assert evidence['price'] is not None  # source evidence stays intact
    user_priced = make_evidence(facts=facts(2), price={'amount': 100, 'currency': 'KRW',
                                                       'source': 'user', 'observed_at': AT.isoformat()})
    assert prepare_evidence(user_priced, AT + timedelta(days=3))['price'] is None
