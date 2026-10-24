"""Several links in one message become one request with per-product questions."""
from datetime import datetime, timezone
import pytest

from automation_fixtures import make_evidence, message_update
from studio.core import Store

AT = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
A = 'https://link.coupang.com/a/fixture-a'
B = 'https://toss.im/_m/fixture-b'
C = 'https://coupa.ng/b/fixture-c'
D = 'https://link.coupang.com/a/fixture-d'


class FakeTelegram:
    chat_id = 10
    user_id = 10
    bot_id = 900


def needs_info(url, at, name=''):
    return {'provider': 'coupang' if 'coupang' in url or 'coupa' in url else 'toss',
            'affiliate_url': url, 'outcome': 'needs_info',
            'evidence': make_evidence(name=name) | {'facts': []},
            'reason': '상품 페이지 접근 제한'}


def ready(url, at):
    return {'provider': 'coupang' if 'coupang' in url or 'coupa' in url else 'toss',
            'affiliate_url': url, 'outcome': 'ready',
            'evidence': make_evidence(name=f'{url[-1].upper()} 상품'),
            'reason': ''}


def receive(store, update, opener=ready):
    from studio.telegram_inbox import receive_update
    return receive_update(store, FakeTelegram(), update, AT, opener=opener)


def rows(store, kind):
    with store.connect() as c:
        return store.rows(c, kind)


def test_two_links_make_one_request_with_two_products(tmp_path):
    store = Store(tmp_path / 'data.db')
    request_id = receive(store, message_update(1, f'{A} {B}'))
    requests = rows(store, 'automation_request')
    assert len(requests) == 1
    row = requests[0]
    assert row['id'] == request_id
    assert len(row['products']) == 2
    assert [p['index'] for p in row['products']] == [1, 2]
    assert row['state'] == 'ready'
    assert len(rows(store, 'notice')) == 1  # accepted notice, no question


def test_one_blocked_product_publishes_ready_others_and_asks_only_blocked(tmp_path):
    store = Store(tmp_path / 'data.db')
    def opener(url, at):
        return needs_info(url, at) if url == B else ready(url, at)
    request_id = receive(store, message_update(1, f'{A} {B}'), opener=opener)
    row = rows(store, 'automation_request')[0]
    assert row['state'] == 'ready'
    assert [p['state'] for p in row['products']] == ['ready', 'awaiting_info']
    questions = [n for n in rows(store, 'notice') if n.get('force_reply')]
    assert len(questions) == 1
    assert '2번 상품' in questions[0]['text']
    assert request_id in questions[0]['text']


def test_reply_updates_only_its_product_by_message_id(tmp_path):
    store = Store(tmp_path / 'data.db')
    def opener(url, at):
        return needs_info(url, at) if url == B else ready(url, at)
    request_id = receive(store, message_update(1, f'{A} {B}'), opener=opener)
    question = next(n for n in rows(store, 'notice') if n.get('force_reply'))
    reply = message_update(2, '상품명: B 상품\n특징: B 사실 하나', reply_to={
        'message_id': question.get('telegram_message_id') or 501,
        'from': {'id': 900, 'is_bot': True}, 'chat': {'id': 10}})
    with store.connect() as c:
        row = store.get(c, request_id, 'automation_request')
        row['products'][1]['question_message_id'] = 501
        store.put(c, 'automation_request', row)
    receive(store, reply, opener=opener)
    row = rows(store, 'automation_request')[0]
    assert row['products'][1]['evidence']['name'] == 'B 상품'
    assert row['products'][1]['state'] == 'ready'
    assert row['products'][0]['revision'] == 0
    assert row['products'][1]['revision'] == 1


def test_four_links_are_rejected_without_a_request(tmp_path):
    store = Store(tmp_path / 'data.db')
    receive(store, message_update(1, f'{A} {B} {C} {D}'))
    assert rows(store, 'automation_request') == []
    notices = rows(store, 'notice')
    assert any('2~3개' in n['text'] for n in notices)


def test_duplicate_links_are_counted_once(tmp_path):
    store = Store(tmp_path / 'data.db')
    receive(store, message_update(1, f'{A} {A}'))
    row = rows(store, 'automation_request')[0]
    assert len(row['products']) == 1


def test_all_blocked_is_awaiting_info_with_one_question_each(tmp_path):
    store = Store(tmp_path / 'data.db')
    receive(store, message_update(1, f'{A} {B}'), opener=needs_info)
    row = rows(store, 'automation_request')[0]
    assert row['state'] == 'awaiting_info'
    questions = [n for n in rows(store, 'notice') if n.get('force_reply')]
    assert len(questions) == 2
    assert all('요청 ' in n['text'] for n in questions)


def test_late_reply_marks_same_request_for_update(tmp_path):
    store = Store(tmp_path / 'data.db')
    def opener(url, at):
        return needs_info(url, at) if url == B else ready(url, at)
    request_id = receive(store, message_update(1, f'{A} {B}'), opener=opener)
    with store.connect() as c:
        row = store.get(c, request_id, 'automation_request')
        row.update(state='published', update_state='')
        row['products'][1]['question_message_id'] = 501
        store.put(c, 'automation_request', row)
    reply = message_update(2, '상품명: 늦은 B\n특징: 늦은 사실', reply_to={
        'message_id': 501, 'from': {'id': 900, 'is_bot': True}, 'chat': {'id': 10}})
    receive(store, reply, opener=opener)
    row = rows(store, 'automation_request')[0]
    assert len(rows(store, 'automation_request')) == 1
    assert row['update_state'] == 'updating'
    assert row['state'] == 'published'
    assert row['products'][1]['state'] == 'ready'
