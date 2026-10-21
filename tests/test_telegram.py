"""Telegram send/poll wire contract, error privacy and durable notices."""
from datetime import datetime, timedelta, timezone
import json
import httpx
import pytest

from studio.core import Store

AT = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
ENV = {'TELEGRAM_BOT_TOKEN': '123456789:fixture_bot_token_value_abc', 'TELEGRAM_CHAT_ID': '1234',
       'TELEGRAM_ALLOWED_USER_ID': '5678'}


@pytest.fixture
def remote(monkeypatch):
    requests = []
    replies = []
    def handle(request):
        requests.append(request)
        if not replies:
            raise AssertionError('unexpected request ' + str(request.url))
        return replies.pop(0)
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request',
                        lambda self, request: transport.handle_request(request))
    return requests, replies


def test_missing_partial_and_valid_configuration():
    from studio.telegram import Telegram
    assert Telegram.from_env({}) is None
    for env in ({'TELEGRAM_BOT_TOKEN': 'x'}, dict(ENV, TELEGRAM_CHAT_ID='abc'),
                dict(ENV, TELEGRAM_ALLOWED_USER_ID='0'), dict(ENV, TELEGRAM_BOT_TOKEN='bad token')):
        with pytest.raises(ValueError) as error:
            Telegram.from_env(env)
        assert '123456789:fixture_bot_token_value_abc' not in str(error.value)
    client = Telegram.from_env(ENV)
    assert (client.chat_id, client.user_id) == (1234, 5678)
    assert '123456789:fixture_bot_token_value_abc' not in repr(client)


def test_poll_send_and_connect_wire_contract(remote):
    requests, replies = remote
    from studio.telegram import Telegram
    client = Telegram.from_env(ENV)
    replies.append(httpx.Response(200, json={'ok': True, 'result': {
        'id': 99, 'is_bot': True, 'username': 'fixture_bot'}}))
    replies.append(httpx.Response(200, json={'ok': True, 'result': {
        'url': '', 'has_custom_certificate': False, 'pending_update_count': 0}}))
    assert client.connect() == 99
    replies.append(httpx.Response(200, json={'ok': True, 'result': [
        {'update_id': 10, 'message': {'message_id': 1, 'text': 'hi'}}]}))
    assert client.updates(11) == [{'update_id': 10, 'message': {'message_id': 1, 'text': 'hi'}}]
    replies.append(httpx.Response(200, json={'ok': True, 'result': {'message_id': 501}}))
    assert client.send('접수', reply_to_message_id=7, force_reply=True) == 501
    urls = [str(request.url) for request in requests]
    assert urls[0].endswith('/bot123456789:fixture_bot_token_value_abc/getMe')
    assert urls[1].endswith('/getWebhookInfo')
    assert urls[2].endswith('/getUpdates')
    conversation = json.loads(requests[2].content)
    assert conversation == {'offset': 11, 'timeout': 25, 'allowed_updates': ['message']}
    sent = json.loads(requests[3].content)
    assert sent == {'chat_id': 1234, 'text': '접수', 'reply_to_message_id': 7,
                    'reply_markup': {'force_reply': True, 'selective': True}}
    assert all(request.method == 'POST' for request in requests[1:])


@pytest.mark.parametrize('reply,code,retryable', [
    (httpx.Response(302, headers={'location': 'https://evil.test/'}), 'redirect', False),
    (httpx.Response(401, json={'ok': False, 'description': 'Unauthorized'}), 'auth', False),
    (httpx.Response(403, json={'ok': False, 'description': 'Forbidden'}), 'auth', False),
    (httpx.Response(409, json={'ok': False, 'description': 'Conflict'}), 'conflict', False),
    (httpx.Response(429, json={'ok': False, 'parameters': {'retry_after': 7}}), 'rate', True),
    (httpx.Response(500, json={'ok': False}), 'server', True),
    (httpx.Response(200, json={'ok': False, 'description': '123456789:fixture_bot_token_value_abc'}), 'api', False),
    (httpx.Response(200, text='<html>fixture-bot-token</html>'), 'malformed', False),
])
def test_errors_do_not_expose_token_or_follow_redirects(remote, reply, code, retryable):
    requests, replies = remote
    from studio.telegram import Telegram, TelegramError
    client = Telegram.from_env(ENV)
    replies.append(reply)
    with pytest.raises(TelegramError) as error:
        client.send('본문')
    assert error.value.code == code
    assert error.value.retryable is retryable
    assert len(requests) == 1
    assert '123456789:fixture_bot_token_value_abc' not in str(error.value)
    assert '123456789:fixture_bot_token_value_abc' not in repr(error.value)
    if code == 'rate':
        assert error.value.retry_after == 7


def test_invalid_retry_after_is_not_trusted(remote):
    requests, replies = remote
    from studio.telegram import Telegram, TelegramError
    client = Telegram.from_env(ENV)
    for value in (0, -3, 'soon', True, 10**9):
        replies.append(httpx.Response(429, json={'ok': False, 'parameters': {'retry_after': value}}))
        with pytest.raises(TelegramError) as error:
            client.send('본문')
        assert error.value.retry_after is None
    replies.append(httpx.Response(200, json={'ok': True, 'result': {'message_id': 5}}))
    assert client.send('본문') == 5


def test_timeout_is_retryable_and_private(remote, monkeypatch):
    from studio.telegram import Telegram, TelegramError
    client = Telegram.from_env(ENV)
    def boom(request):
        raise httpx.ReadTimeout('123456789:fixture_bot_token_value_abc', request=request)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', lambda self, request: boom(request))
    with pytest.raises(TelegramError) as error:
        client.send('본문')
    assert error.value.retryable is True and error.value.code == 'timeout'
    assert '123456789:fixture_bot_token_value_abc' not in str(error.value)


def test_notice_delivery_is_idempotent_and_never_touches_publication(tmp_path, remote):
    requests, replies = remote
    from studio.automation_store import enqueue_notice
    from studio.telegram import Telegram, deliver_notice
    store = Store(tmp_path / 'data.db')
    with store.connect() as c:
        notice = enqueue_notice(store, c, 'req1', 'accepted', '접수', chat_id=1234)
    client = Telegram.from_env(ENV)
    replies.append(httpx.Response(200, json={'ok': True, 'result': {'message_id': 501}}))
    first = deliver_notice(store, client, notice['id'], AT)
    assert first['state'] == 'sent' and first['telegram_message_id'] == 501
    second = deliver_notice(store, client, notice['id'], AT)
    assert second['state'] == 'sent'
    assert len(requests) == 1
    with store.connect() as c:
        assert store.rows(c, 'automation_publication') == []


def test_notice_failure_schedules_retry_and_survives_restart(tmp_path, remote):
    requests, replies = remote
    from studio.automation_store import enqueue_notice
    from studio.telegram import Telegram, deliver_notice
    path = tmp_path / 'data.db'
    store = Store(path)
    with store.connect() as c:
        notice = enqueue_notice(store, c, 'req1', 'accepted', '접수', chat_id=1234)
    client = Telegram.from_env(ENV)
    replies.append(httpx.Response(500, json={'ok': False}))
    failed = deliver_notice(store, client, notice['id'], AT)
    assert failed['state'] == 'pending' and failed['attempts'] == 1
    assert failed['next_attempt_at'] == (AT + timedelta(seconds=60)).isoformat()
    assert deliver_notice(store, client, notice['id'], AT + timedelta(seconds=30))['attempts'] == 1
    replies.append(httpx.Response(200, json={'ok': True, 'result': {'message_id': 502}}))
    done = deliver_notice(Store(path), client, notice['id'], AT + timedelta(seconds=61))
    assert done['state'] == 'sent' and done['telegram_message_id'] == 502
    assert len(requests) == 2


def test_notice_requires_a_registered_chat_and_plain_length(remote):
    requests, replies = remote
    from studio.telegram import Telegram, TelegramError
    client = Telegram.from_env(ENV)
    for text in ('', 'x' * 4097):
        with pytest.raises(ValueError):
            client.send(text)
    assert requests == []
