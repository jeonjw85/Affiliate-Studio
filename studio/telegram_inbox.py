"""Authorized Telegram inbox: exact link extraction, description replies, cursor.

An update is only accepted from the configured private chat and user. Each
accepted change (request, notice, cursor) is committed together, so a crash
never acknowledges an update whose work was lost.
"""
from datetime import datetime, timezone
import re
from urllib.parse import urlsplit

from .automation_store import enqueue_notice, get_optional, public, record_id
from .core import safe_url
from .product_info import classify_link, inspect_link, merge_evidence, parse_description

CURSOR_ID = 'telegram_current'
URL_TEXT = re.compile(r'https://[^\s<>"\']+')


def _slice(text, offset, length):
    """Convert Telegram UTF-16 offsets to a Python string slice."""
    encoded = text.encode('utf-16-le')
    start, end = offset * 2, (offset + length) * 2
    if offset < 0 or length <= 0 or end > len(encoded):
        return ''
    return encoded[start:end].decode('utf-16-le', 'strict')


def extract_links(message):
    """Return explicit URL entities (or plain https URLs), deduplicated."""
    text = message.get('text') if isinstance(message.get('text'), str) else message.get('caption')
    if not isinstance(text, str):
        return []
    found = []
    for entity in message.get('entities') or message.get('caption_entities') or []:
        if not isinstance(entity, dict):
            continue
        kind = entity.get('type')
        offset, length = entity.get('offset'), entity.get('length')
        if type(offset) is not int or type(length) is not int:
            continue
        if kind == 'text_link':
            candidate = entity.get('url')
        elif kind == 'url':
            try:
                candidate = _slice(text, offset, length)
            except UnicodeDecodeError:
                continue
        else:
            continue
        if not isinstance(candidate, str) or not candidate.startswith('https://'):
            continue
        try:
            candidate = safe_url(candidate)
            classify_link(candidate)
        except ValueError:
            continue
        if candidate not in found:
            found.append(candidate)
    if not found:
        for match in URL_TEXT.findall(text):
            candidate = match.rstrip('.,;:!?)]}\'')
            try:
                candidate = safe_url(candidate)
                classify_link(candidate)
            except ValueError:
                continue
            if candidate not in found:
                found.append(candidate)
    return found


def _next_offset(store, c, at):
    row = get_optional(store, c, 'telegram_cursor', CURSOR_ID)
    return row or {'id': CURSOR_ID, 'next_offset': 0, 'updated_at': at.isoformat()}


def _authorized(message, client):
    chat, sender = message.get('chat') or {}, message.get('from') or {}
    return (chat.get('type') == 'private' and chat.get('id') == client.chat_id
            and sender.get('id') == client.user_id and sender.get('is_bot') is not True)


def _is_bot_reply(reply, client):
    return (isinstance(reply, dict) and (reply.get('from') or {}).get('is_bot') is True
            and (reply.get('from') or {}).get('id') == client.bot_id
            and (reply.get('chat') or {}).get('id') == client.chat_id)


def question_text(request):
    """Ask only for the missing description; request ID ties the reply."""
    return ('상품 정보를 읽지 못해 설명이 필요합니다.\n'
            '이 메시지에 답장으로 아래 형식을 보내주세요.\n'
            '상품명: 상품 이름과 선택한 옵션\n'
            '특징: 확인한 특징이나 구성\n'
            '특징: 추가로 확인한 내용\n'
            '가격: 선택 입력\n'
            '대상: 선택 입력\n'
            f'요청 {request["id"]}')


def _status_text(store, c):
    rows = store.rows(c, 'automation_request')
    counts = {}
    for row in rows:
        counts[row.get('state', 'unknown')] = counts.get(row.get('state', 'unknown'), 0) + 1
    if not counts:
        return '자동 운영 현황: 접수된 요청 없음'
    labels = {'awaiting_info': '설명 대기', 'ready': '발행 대기', 'composing': '작성 중',
              'checking': '검사 중', 'scheduled': '예약 확인', 'published': '발행 확인',
              'failed': '실패', 'uncertain': '결과 불명'}
    summary = ' · '.join(f'{labels.get(key, key)} {value}' for key, value in sorted(counts.items()))
    return f'자동 운영 현황: {summary}'


def receive_update(store, client, update, at, *, opener=None):
    """Persist one Telegram update. Network work runs outside the DB lock."""
    at = at.astimezone(timezone.utc)
    if not isinstance(update, dict) or type(update.get('update_id')) is not int:
        return None
    update_id = update['update_id']
    message = update.get('message')
    with store.connect() as c:
        cursor = _next_offset(store, c, at)
        if update_id < cursor['next_offset']:
            return None
        existing_requests = store.rows(c, 'automation_request')
    if not isinstance(message, dict) or not _authorized(message, client):
        _commit_cursor(store, update_id, at)
        return None

    text = message.get('text') if isinstance(message.get('text'), str) else message.get('caption')
    links = extract_links(message)
    request_id = None
    notices = []
    if isinstance(text, str) and text.strip().startswith('/start'):
        notices.append((None, f'reply:{update_id}', '사용 방법: 본인 제휴 링크를 보내면 상품 정보를 확인해 글을 작성합니다. 정보가 부족하면 이 대화로 설명을 요청합니다.', False))
        _commit(store, update_id, at, [], notices, client.chat_id)
        return None
    if isinstance(text, str) and text.strip().startswith('/status'):
        with store.connect() as c:
            status = _status_text(store, c)
        _commit(store, update_id, at, [], [(None, f'reply:{update_id}', status, False)])
        return None
    if len(links) > 1:
        _commit(store, update_id, at, [], [(None, f'reply:{update_id}',
                                           '한 메시지에는 링크 하나만 보내주세요. 상품별로 따로 전송이 필요합니다.', False)],
                client.chat_id)
        return None
    if links:
        affiliate = links[0]
        duplicate = next((row for row in existing_requests if row.get('affiliate_url') == affiliate), None)
        if duplicate:
            if duplicate.get('state') == 'failed':
                # A failed request can be retried by sending the link again;
                # this is the only recovery affordance in the bot.
                retry = {**duplicate, 'state': 'ready', 'error': '', 'attempts': 0}
                _commit(store, update_id, at, [retry], [(duplicate['id'], f'retry:{update_id}',
                                                         f'다시 접수했습니다. 요청 {duplicate["id"]}', False)],
                        client.chat_id)
                return duplicate['id']
            _commit(store, update_id, at, [], [(duplicate['id'], f'duplicate:{update_id}',
                                                f'이미 접수된 링크입니다. 요청 {duplicate["id"]}', False)],
                    client.chat_id)
            return duplicate['id']
        provider = classify_link(affiliate)
        request_id = record_id('req', client.bot_id, client.chat_id, message.get('message_id'))
        try:
            inspection = (opener or (lambda url, moment: inspect_link(url, moment)))(affiliate, at)
        except (ValueError, OSError):
            inspection = {'provider': provider, 'affiliate_url': affiliate, 'outcome': 'needs_info',
                          'evidence': {'name': '', 'facts': [], 'price': None, 'category': '',
                                       'audience': '', 'source_url': None, 'product_key': None,
                                       'conflicts': []},
                          'reason': '상품 페이지 조회 실패'}
        # The submitted affiliate URL is authoritative; never trust a rewrite.
        inspection = dict(inspection, provider=provider, affiliate_url=affiliate)
        request = {'id': request_id, 'bot_id': client.bot_id, 'chat_id': client.chat_id,
                   'user_id': client.user_id, 'message_id': message.get('message_id'),
                   'provider': provider, 'affiliate_url': affiliate,
                   'evidence': inspection['evidence'],
                   'state': 'ready' if inspection['outcome'] == 'ready' else 'awaiting_info',
                   'version': 1, 'created_at': at.isoformat(), 'updated_at': at.isoformat(),
                   'settings_version': None, 'slot_id': None, 'error': '', 'next_attempt_at': None,
                   'reason': inspection.get('reason', ''), 'revision': 0}
        if request['state'] == 'awaiting_info':
            notices.append((request_id, f'question:0', question_text(request), True))
        else:
            notices.append((request_id, 'accepted', f'접수 완료: {request["provider"]} 상품 글을 준비합니다. 요청 {request_id}', False))
        _commit(store, update_id, at, [request], notices, client.chat_id)
        return request_id

    reply = message.get('reply_to_message')
    if isinstance(text, str) and not _is_bot_reply(reply, client) and not links and not message.get('photo') \
            and not text.strip().startswith('/'):
        _commit(store, update_id, at, [], [(None, f'reply:{update_id}',
                                           '설명은 봇이 보낸 질문에 답장으로 보내주세요. '
                                           '새 상품은 제휴 링크를 그대로 전송하면 됩니다.', False)], client.chat_id)
        return None
    if isinstance(text, str) and _is_bot_reply(reply, client):
        request_id = next((row['id'] for row in existing_requests
                           if row['state'] == 'awaiting_info'
                           and row.get('question_message_id') == reply.get('message_id')), None)
        if request_id is None:
            for row in existing_requests:
                if row['id'] in text and row['state'] != 'awaiting_info':
                    _commit(store, update_id, at, [], [(row['id'], f'late:{update_id}',
                                                        f'이미 설명이 반영된 요청입니다. 현재 상태: {row["state"]}', False)],
                            client.chat_id)
                    return row['id']
        if request_id is None:
            request_id = next((row['id'] for row in existing_requests
                               if row['id'] in text and row['state'] == 'awaiting_info'), None)
        if request_id is None:
            _commit_cursor(store, update_id, at)
            return None
        supplied = parse_description(text, at)
        if not supplied['name'] or not supplied['facts']:
            _commit(store, update_id, at, [], [(request_id, f'more_info:{update_id}',
                                                '상품명과 특징을 한 줄씩 보내주세요.\n' + question_text({'id': request_id}), True)],
                        client.chat_id)
            return request_id
        with store.connect() as c:
            request = get_optional(store, c, 'automation_request', request_id)
        merged = merge_evidence(request['evidence'], supplied)
        state = 'ready' if merged['name'] and merged['facts'] else 'awaiting_info'
        updated = dict(request, evidence=merged, state=state, revision=request.get('revision', 0) + 1,
                       updated_at=at.isoformat())
        notices.append((request_id, f'info:{updated["revision"]}',
                        f'설명 반영 완료: {merged["name"]}' if state == 'ready' else question_text(updated), state == 'awaiting_info'))
        _commit(store, update_id, at, [updated], notices, client.chat_id)
        return request_id

    if message.get('photo'):
        _commit(store, update_id, at, [], [(None, f'reply:{update_id}',
                                           '사진만으로는 상품을 확인할 수 없습니다. 상품명과 특징을 텍스트로 보내주세요.', False)],
                client.chat_id)
        return None
    _commit_cursor(store, update_id, at)
    return None


def _commit_cursor(store, update_id, at):
    _commit(store, update_id, at, [], [])


def _commit(store, update_id, at, requests, notices, chat_id=None):
    with store.connect() as c:
        for request in requests:
            store.put(c, 'automation_request', request)
        for request_id, event_key, text, force in notices:
            enqueue_notice(store, c, request_id, f'{event_key}', text,
                           chat_id=chat_id or 10, force_reply=force)
        cursor = get_optional(store, c, 'telegram_cursor', CURSOR_ID) or {
            'id': CURSOR_ID, 'next_offset': 0}
        cursor.update(next_offset=update_id + 1, updated_at=at.isoformat())
        store.put(c, 'telegram_cursor', cursor)
