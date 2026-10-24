"""Authorized Telegram updates: exact URLs, reverse-order replies, atomic cursor."""
from datetime import datetime, timezone
import json
import pytest

from automation_fixtures import message_update

AT = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
COUPANG = 'https://link.coupang.com/a/fixture?subId=case%2B1'
TOSS = 'https://toss.im/_m/fixture'


class FakeTelegram:
    chat_id = 10
    user_id = 10
    bot_id = 900

    def __init__(self):
        self.sent = []
        self.fail = False

    def send(self, text, *, reply_to_message_id=None, force_reply=False):
        if self.fail:
            raise AssertionError('unexpected send')
        self.sent.append({'text': text, 'reply_to': reply_to_message_id, 'force': force_reply})
        return 500 + len(self.sent)


def test_unicode_entities_preserve_exact_url():
    from studio.telegram_inbox import extract_links
    text = '😀 링크 https://link.coupang.com/a/fixture 확인'
    start = text.index('https://')
    length = len('https://link.coupang.com/a/fixture')
    update = message_update(1, text, entities=[{'type': 'url', 'offset': start, 'length': length}])
    assert extract_links(update['message']) == ['https://link.coupang.com/a/fixture']
    text_link = message_update(2, '상품 보기', entities=[
        {'type': 'text_link', 'offset': 0, 'length': 5, 'url': COUPANG}])
    assert extract_links(text_link['message']) == [COUPANG]
    bad = message_update(3, text, entities=[{'type': 'url', 'offset': start, 'length': 200}])
    # Corrupt entity metadata does not hide a valid plain URL in the text.
    assert extract_links(bad['message']) == ['https://link.coupang.com/a/fixture']
    for entity in ({'type': 'url', 'offset': -1, 'length': 5},
                   {'type': 'url', 'offset': 0, 'length': 0},
                   {'type': 'text_link', 'offset': 0, 'length': 5, 'url': 'javascript:alert(1)'}):
        # Malformed entity metadata is ignored; the visible https URL is
        # still validated by the same allowlist rather than trusted blindly.
        assert extract_links(message_update(4, text, entities=[entity])['message']) == [
            'https://link.coupang.com/a/fixture']
    plain = message_update(5, f'<{COUPANG}>')['message']
    assert extract_links(plain) == [COUPANG]


def test_authorization_precedes_lookup_and_advances_cursor(tmp_path):
    from studio.core import Store
    from studio.telegram_inbox import receive_update
    store = Store(tmp_path / 'data.db')
    client = FakeTelegram()

    def opener(*args):
        raise AssertionError('lookup must not run for foreign input')
    for update in (message_update(1, COUPANG, chat_id=99),
                   message_update(2, COUPANG, user_id=99),
                   message_update(3, COUPANG, chat_id=-100)):
        assert receive_update(store, client, update, AT, opener=opener) is None
    bot = message_update(4, COUPANG)
    bot['message']['from']['is_bot'] = True
    assert receive_update(store, client, bot, AT, opener=opener) is None
    with store.connect() as c:
        assert store.rows(c, 'automation_request') == []
    client.fail = True
    assert client.sent == []


def test_replay_and_duplicate_link_reuse_one_request(tmp_path):
    from studio.core import Store
    from studio.telegram_inbox import receive_update
    from studio.product_info import inspect_link

    store = Store(tmp_path / 'data.db')
    client = FakeTelegram()
    calls = []
    def opener(url, at):
        calls.append(url)
        return {'provider': 'coupang', 'affiliate_url': url, 'outcome': 'needs_info',
                'evidence': {'name': '', 'facts': [], 'price': None, 'category': '', 'audience': '',
                             'source_url': None, 'product_key': None, 'conflicts': []},
                'reason': '상품 페이지 접근 제한'}
    first = receive_update(store, client, message_update(1, COUPANG), AT, opener=opener)
    again = receive_update(store, FakeTelegram(), message_update(1, COUPANG), AT, opener=opener)
    other = receive_update(store, FakeTelegram(), message_update(2, COUPANG, message_id=77), AT, opener=opener)
    assert again is None  # update_id below the saved cursor is already acknowledged
    assert other == first
    assert len(calls) == 1
    with store.connect() as c:
        requests = store.rows(c, 'automation_request')
        cursor = store.rows(c, 'telegram_cursor')
    assert len(requests) == 1 and requests[0]['state'] == 'awaiting_info'
    assert requests[0]['affiliate_url'] == COUPANG
    assert requests[0]['provider'] == 'coupang'
    assert [row['next_offset'] for row in cursor] == [3]


def test_multiple_links_make_one_comparison_request(tmp_path):
    from studio.core import Store
    from studio.telegram_inbox import receive_update
    store = Store(tmp_path / 'data.db')
    client = FakeTelegram()
    text = f'{COUPANG} {TOSS}'
    update = message_update(1, text, entities=[
        {'type': 'url', 'offset': 0, 'length': len(COUPANG)},
        {'type': 'url', 'offset': len(COUPANG) + 1, 'length': len(TOSS)}])
    request_id = receive_update(store, client, update, AT, opener=lambda url, at: {
        'provider': 'coupang' if 'coupang' in url else 'toss', 'affiliate_url': url,
        'outcome': 'ready',
        'evidence': {'name': '합성 상품', 'facts': [{'text': '사실', 'source': 'page',
                                                     'observed_at': AT.isoformat()}],
                     'price': None, 'category': '', 'audience': '', 'source_url': None,
                     'product_key': None, 'conflicts': []}, 'reason': ''})
    assert request_id
    with store.connect() as c:
        requests = store.rows(c, 'automation_request')
        notices = store.rows(c, 'notice')
    assert len(requests) == 1 and len(requests[0]['products']) == 2
    assert requests[0]['state'] == 'ready'
    assert any('접수 완료' in notice['text'] for notice in notices)
    assert client.sent == []  # queued only; a separate sender owns delivery


def test_reverse_order_replies_and_lost_question_ack(tmp_path):
    from studio.core import Store
    from studio.telegram_inbox import receive_update
    store = Store(tmp_path / 'data.db')
    client = FakeTelegram()

    def needs_info(url, at):
        return {'provider': 'coupang', 'affiliate_url': url, 'outcome': 'needs_info',
                'evidence': {'name': '', 'facts': [], 'price': None, 'category': '', 'audience': '',
                             'source_url': None, 'product_key': None, 'conflicts': []},
                'reason': '상품 페이지 접근 제한'}
    a = receive_update(store, client, message_update(1, COUPANG), AT, opener=needs_info)
    b = receive_update(store, client, message_update(2, TOSS), AT, opener=needs_info)
    # The bot's question for B is answered first; its ack was lost, so only
    # the request ID text and the bot reply identity are available.
    reply_b = {'message_id': 700, 'from': {'id': 900, 'is_bot': True}, 'chat': {'id': 10}}
    update_b = message_update(10, f'상품명: 합성 상품 B\n특징: B 특징\n요청 {b}',
                              reply_to=reply_b)
    assert receive_update(store, client, update_b, AT, opener=needs_info) == b
    reply_a = {'message_id': 999, 'from': {'id': 900, 'is_bot': True}, 'chat': {'id': 10}}
    update_a = message_update(11, f'상품명: 합성 상품 A\n특징: A 특징\n요청 {a}', reply_to=reply_a)
    assert receive_update(store, client, update_a, AT, opener=needs_info) == a
    with store.connect() as c:
        rows = {row['id']: row for row in store.rows(c, 'automation_request')}
        cursor = store.rows(c, 'telegram_cursor')[0]
    assert rows[a]['evidence']['name'] == '합성 상품 A'
    assert rows[b]['evidence']['name'] == '합성 상품 B'
    assert rows[a]['state'] == rows[b]['state'] == 'ready'
    assert cursor['next_offset'] == 12


def test_question_message_id_binds_reply_without_request_text(tmp_path):
    from studio.core import Store
    from studio.telegram_inbox import receive_update
    store = Store(tmp_path / 'data.db')
    client = FakeTelegram()

    def needs_info(url, at):
        return {'provider': 'coupang', 'affiliate_url': url, 'outcome': 'needs_info',
                'evidence': {'name': '', 'facts': [], 'price': None, 'category': '', 'audience': '',
                             'source_url': None, 'product_key': None, 'conflicts': []}, 'reason': 'x'}
    request_id = receive_update(store, client, message_update(1, COUPANG), AT, opener=needs_info)
    with store.connect() as c:
        request = store.rows(c, 'automation_request')[0]
        notice = next(row for row in store.rows(c, 'notice') if row['force_reply'])
    from studio.telegram import deliver_notice
    deliver_notice(store, client, notice['id'], AT)
    with store.connect() as c:
        request = store.rows(c, 'automation_request')[0]
    assert request['products'][0]['question_message_id'] == 501
    reply = {'message_id': 501, 'from': {'id': 900, 'is_bot': True}, 'chat': {'id': 10}}
    update = message_update(2, '상품명: 답장 연결\n특징: 메시지 ID만 사용', reply_to=reply)
    assert receive_update(store, client, update, AT, opener=needs_info) == request_id
    with store.connect() as c:
        assert store.rows(c, 'automation_request')[0]['state'] == 'ready'


def test_unlinked_text_and_wrong_bot_reply_are_ignored(tmp_path):
    from studio.core import Store
    from studio.telegram_inbox import receive_update
    store = Store(tmp_path / 'data.db')
    client = FakeTelegram()

    def needs_info(url, at):
        return {'provider': 'coupang', 'affiliate_url': url, 'outcome': 'needs_info',
                'evidence': {'name': '', 'facts': [], 'price': None, 'category': '', 'audience': '',
                             'source_url': None, 'product_key': None, 'conflicts': []},
                'reason': 'x'}
    request_id = receive_update(store, client, message_update(1, COUPANG), AT, opener=needs_info)
    with store.connect() as c:
        before = store.rows(c, 'automation_request')[0]
    assert receive_update(store, client, message_update(2, '상품명: 임의', reply_to=None), AT,
                          opener=needs_info) is None
    foreign = {'message_id': 1, 'from': {'id': 10, 'is_bot': False}, 'chat': {'id': 10}}
    assert receive_update(store, client, message_update(
        3, f'상품명: 다른 봇\n요청 {request_id}', reply_to=foreign), AT, opener=needs_info) is None
    with store.connect() as c:
        after = store.rows(c, 'automation_request')[0]
    assert after == before


def test_crash_rolls_back_request_and_cursor(tmp_path, monkeypatch):
    from studio.core import Store
    from studio import telegram_inbox
    store = Store(tmp_path / 'data.db')
    client = FakeTelegram()
    original = telegram_inbox.enqueue_notice
    def explode(*args, **kwargs):
        raise RuntimeError('fixture failure after request insert')
    monkeypatch.setattr(telegram_inbox, 'enqueue_notice', explode)

    def needs_info(url, at):
        return {'provider': 'coupang', 'affiliate_url': url, 'outcome': 'needs_info',
                'evidence': {'name': '', 'facts': [], 'price': None, 'category': '', 'audience': '',
                             'source_url': None, 'product_key': None, 'conflicts': []},
                'reason': 'x'}
    with pytest.raises(RuntimeError):
        telegram_inbox.receive_update(store, client, message_update(1, COUPANG), AT, opener=needs_info)
    with store.connect() as c:
        assert store.rows(c, 'automation_request') == []
        assert store.rows(c, 'telegram_cursor') == []
    monkeypatch.setattr(telegram_inbox, 'enqueue_notice', original)
    assert telegram_inbox.receive_update(store, client, message_update(1, COUPANG), AT,
                                         opener=needs_info) is not None


def test_start_status_and_photo_caption(tmp_path):
    from studio.core import Store
    from studio.telegram_inbox import receive_update
    store = Store(tmp_path / 'data.db')
    client = FakeTelegram()
    assert receive_update(store, client, message_update(1, '/start'), AT, opener=lambda *a: None) is None
    assert receive_update(store, client, message_update(2, '/status'), AT, opener=lambda *a: None) is None
    with store.connect() as c:
        texts = [row['text'] for row in store.rows(c, 'notice')]
    assert len(texts) == 2
    assert any('링크' in text for text in texts) and any('현황' in text for text in texts)
    photo = {'update_id': 3, 'message': {'message_id': 3, 'date': 1, 'chat': {'id': 10, 'type': 'private'},
                                          'from': {'id': 10, 'is_bot': False}, 'photo': [{'file_id': 'x'}]}}
    assert receive_update(store, client, photo, AT, opener=lambda *a: None) is None
    with store.connect() as c:
        notices = store.rows(c, 'notice')
    assert '텍스트' in notices[0]['text']


def test_blocked_link_asks_for_description_and_ready_link_skips_question(tmp_path):
    from studio.core import Store
    from studio.telegram_inbox import receive_update, question_text
    store = Store(tmp_path / 'data.db')
    client = FakeTelegram()

    def blocked(url, at):
        return {'provider': 'coupang', 'affiliate_url': url, 'outcome': 'needs_info',
                'evidence': {'name': '', 'facts': [], 'price': None, 'category': '', 'audience': '',
                             'source_url': None, 'product_key': 'coupang:123:456', 'conflicts': []},
                'reason': '상품 페이지 접근 제한'}

    def ready(url, at):
        return {'provider': 'coupang', 'affiliate_url': url, 'outcome': 'ready',
                'evidence': {'name': '합성 상품', 'facts': [{'text': '사실', 'source': 'page',
                                                             'observed_at': AT.isoformat()}],
                             'price': None, 'category': '', 'audience': '', 'source_url': None,
                             'product_key': 'coupang:1', 'conflicts': []}, 'reason': ''}
    blocked_id = receive_update(store, client, message_update(1, COUPANG), AT, opener=blocked)
    ready_id = receive_update(store, client, message_update(2, 'https://coupa.ng/b/fixture'), AT, opener=ready)
    with store.connect() as c:
        rows = {row['id']: row for row in store.rows(c, 'automation_request')}
        notices = store.rows(c, 'notice')
    assert rows[blocked_id]['state'] == 'awaiting_info'
    assert rows[ready_id]['state'] == 'ready'
    assert len([row for row in notices if '상품명' in row['text']]) == 1
    text = question_text(rows[blocked_id])
    assert blocked_id in text and '상품명' in text and '특징' in text
