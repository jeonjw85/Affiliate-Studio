"""Telegram Bot API adapter: fixed endpoint, no redirects, sanitized errors.

The token is embedded in the request path by Telegram's design, so it must
never appear in exceptions, logs, or returned messages.
"""
from datetime import datetime, timedelta, timezone
import json
import re

import httpx

from .automation_store import claim, finish, get_optional
from .core import now

API = 'https://api.telegram.org'
TOKEN = re.compile(r'[0-9]{5,20}:[A-Za-z0-9_-]{20,}')
MAX_TEXT = 4096


class TelegramError(ValueError):
    def __init__(self, code, *, retryable=False, retry_after=None):
        super().__init__(MESSAGES.get(code, 'Telegram 요청 실패: 연결 설정 확인 필요'))
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after

    def __repr__(self):
        return f'TelegramError({self.code!r})'


MESSAGES = {
    'timeout': 'Telegram 응답 시간 초과: 네트워크 상태 확인 필요',
    'connection': 'Telegram 연결 실패: 네트워크 상태 확인 필요',
    'redirect': 'Telegram 주소 변경 응답 거절: 설정과 서비스 상태 확인 필요',
    'auth': 'Telegram 인증 오류: 봇 토큰 확인 필요',
    'conflict': 'Telegram 수신 충돌: 다른 webhook 또는 같은 토큰의 수신기 확인 필요',
    'rate': 'Telegram 호출 한도 초과: 잠시 후 자동 재시도',
    'server': 'Telegram 서버 오류: 잠시 후 자동 재시도',
    'api': 'Telegram 요청 거절: 봇 대화 권한 확인 필요',
    'malformed': 'Telegram 응답 형식 확인 불가',
}


class Telegram:
    def __init__(self, token, chat_id, user_id):
        self._token = token
        self.chat_id = chat_id
        self.user_id = user_id
        self.bot_id = None

    def __repr__(self):
        return f'Telegram(chat_id={self.chat_id}, user_id={self.user_id}, configured=True)'

    @classmethod
    def from_env(cls, environ):
        values = {key: (environ.get(key) or '').strip() for key in
                  ('TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'TELEGRAM_ALLOWED_USER_ID')}
        if not any(values.values()):
            return None
        if not all(values.values()):
            raise ValueError('Telegram 봇 토큰, 채팅 ID, 사용자 ID 세 개 모두 설정 필요')
        token = values['TELEGRAM_BOT_TOKEN']
        if not TOKEN.fullmatch(token):
            raise ValueError('TELEGRAM_BOT_TOKEN 형식 확인 필요')
        numbers = []
        for key in ('TELEGRAM_CHAT_ID', 'TELEGRAM_ALLOWED_USER_ID'):
            value = values[key]
            if not re.fullmatch(r'-?[1-9][0-9]{0,19}', value):
                raise ValueError('Telegram 채팅·사용자 ID는 정수 필요')
            numbers.append(int(value))
        if numbers[0] <= 0:
            raise ValueError('개인 대화 채팅 ID 필요')
        return cls(token, numbers[0], numbers[1])

    def call(self, method, payload=None, *, timeout=10):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
                with client.stream('POST', f'{API}/bot{self._token}/{method}', json=payload or {}) as response:
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 1024 * 1024:
                            raise TelegramError('malformed')
                    status = response.status_code
        except httpx.TimeoutException:
            raise TelegramError('timeout', retryable=True) from None
        except httpx.HTTPError:
            raise TelegramError('connection', retryable=True) from None
        if status in (301, 302, 303, 307, 308):
            raise TelegramError('redirect')
        if status == 401:
            raise TelegramError('auth')
        if status == 403:
            raise TelegramError('auth')
        if status == 409:
            raise TelegramError('conflict')
        if status == 429:
            retry_after = None
            try:
                value = json.loads(raw).get('parameters', {}).get('retry_after')
                if type(value) is int and 0 < value <= 86400:
                    retry_after = value
            except (ValueError, AttributeError):
                pass
            raise TelegramError('rate', retryable=True, retry_after=retry_after)
        if status >= 500:
            raise TelegramError('server', retryable=True)
        if status != 200:
            raise TelegramError('api')
        try:
            data = json.loads(raw)
        except ValueError:
            raise TelegramError('malformed') from None
        if not isinstance(data, dict) or data.get('ok') is not True:
            raise TelegramError('api')
        return data.get('result')

    def connect(self):
        me = self.call('getMe')
        if not isinstance(me, dict) or me.get('is_bot') is not True or type(me.get('id')) is not int or me['id'] <= 0:
            raise TelegramError('malformed')
        webhook = self.call('getWebhookInfo')
        if not isinstance(webhook, dict) or not isinstance(webhook.get('url'), str):
            raise TelegramError('malformed')
        if webhook['url']:
            raise TelegramError('conflict')
        self.bot_id = me['id']
        return self.bot_id

    def updates(self, offset):
        if type(offset) is not int or offset < 0:
            raise ValueError('Telegram 오프셋은 0 이상 정수 필요')
        result = self.call('getUpdates', {'offset': offset, 'timeout': 25,
                                          'allowed_updates': ['message']}, timeout=35)
        if not isinstance(result, list) or any(not isinstance(item, dict) or type(item.get('update_id')) is not int
                                               for item in result):
            raise TelegramError('malformed')
        return result

    def send(self, text, *, reply_to_message_id=None, force_reply=False):
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
            raise ValueError('Telegram 알림은 1~4,096자 텍스트 필요')
        payload = {'chat_id': self.chat_id, 'text': text}
        if reply_to_message_id is not None:
            if type(reply_to_message_id) is not int or reply_to_message_id <= 0:
                raise ValueError('답장 대상 메시지 ID 확인 필요')
            payload['reply_to_message_id'] = reply_to_message_id
        if force_reply:
            # Questions are meant to be answered by swiping that message; the
            # reply UI is independent of whether a reply target is set.
            payload['reply_markup'] = {'force_reply': True, 'selective': True}
        result = self.call('sendMessage', payload, timeout=20)
        if not isinstance(result, dict) or type(result.get('message_id')) is not int or result['message_id'] <= 0:
            raise TelegramError('malformed')
        return result['message_id']


def _backoff(attempts):
    return (60, 300, 900)[min(attempts - 1, 2)]


def deliver_notice(store, client, notice_id, at):
    """Send one stored outbox event; delivery never triggers publishing."""
    at = at.astimezone(timezone.utc)
    with store.connect() as c:
        row = get_optional(store, c, 'notice', notice_id)
        if row is None:
            raise ValueError('알림 기록 없음')
        if row['state'] in ('sent', 'cancelled'):
            row.pop('_claim', None)
            return row
        if row.get('next_attempt_at') and at < datetime.fromisoformat(row['next_attempt_at']):
            row.pop('_claim', None)
            return row
        if row.get('force_reply') and row.get('request_id'):
            request = get_optional(store, c, 'automation_request', row['request_id'])
            if request is not None and request.get('state') != 'awaiting_info':
                row.update(state='cancelled', error='질문이 이미 해결됨')
                store.put(c, 'notice', row)
                row.pop('_claim', None)
                return row
        claimed = claim(store, c, 'notice', notice_id, ('pending', 'sending'), at)
        if claimed is None:
            return {**row, '_claim': None}
    try:
        message_id = client.send(claimed['text'], reply_to_message_id=claimed.get('reply_to_message_id'),
                                 force_reply=claimed.get('force_reply', False))
    except TelegramError as error:
        with store.connect() as c:
            changes = {'attempts': claimed['attempts'] + 1, 'error': str(error)}
            if error.retryable and changes['attempts'] < 4:
                delay = error.retry_after or _backoff(changes['attempts'])
                changes['next_attempt_at'] = (at + timedelta(seconds=min(delay, 86400))).isoformat()
                changes['state'] = 'pending'
            else:
                changes['state'] = 'failed'
                changes['next_attempt_at'] = None
            return finish(store, c, 'notice', notice_id, claimed['_claim'], changes)
    with store.connect() as c:
        # A confirmed question message ID lets later replies bind to this
        # request even when several products are waiting for descriptions.
        request = (get_optional(store, c, 'automation_request', claimed['request_id'])
                   if claimed.get('request_id') and claimed.get('force_reply') else None)
        if request is not None:
            from .automation_products import products_of, with_products
            products = products_of(request)
            waiting = [product for product in products if product['state'] == 'awaiting_info']
            if waiting:
                # The question text names the product; bind the sent message to
                # that product, not just to the request.
                event = claimed.get('event_key', '')
                target = next((product for product in waiting
                               if event.startswith(f'question:{product["index"]}:')), waiting[0])
                for product in products:
                    if product['index'] == target['index']:
                        product['question_message_id'] = message_id
                updated = with_products(request, products)
                updated['question_message_id'] = message_id
                store.put(c, 'automation_request', updated)
        return finish(store, c, 'notice', notice_id, claimed['_claim'],
                      {'state': 'sent', 'attempts': claimed['attempts'] + 1,
                       'telegram_message_id': message_id, 'next_attempt_at': None, 'error': ''})
