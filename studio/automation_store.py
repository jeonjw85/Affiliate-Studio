"""Durable automation settings, exclusive work claims and the notice outbox."""
from datetime import datetime, time, timedelta, timezone
from hashlib import sha256
import uuid
from zoneinfo import ZoneInfo

from .automation_schedule import validate_settings
from .core import VersionConflict, now
from .publication import owner_alive, process_owner, public

SETTINGS_ID = 'settings_current'


def record_id(prefix, *parts):
    digest = sha256('\x00'.join(str(part) for part in parts).encode()).hexdigest()[:40]
    return f'{prefix}_{digest}'


def get_optional(store, c, kind, id):
    from .core import text
    id = text(id, 64)
    row = c.execute('SELECT data FROM records WHERE id=? AND kind=?', (id, kind)).fetchone()
    return None if not row else __import__('json').loads(row[0])


def default_settings():
    return {
        'id': SETTINGS_ID, 'version': 1, 'enabled': False, 'pending_schedule': None,
        'writing': {'topic': '', 'audience': '', 'tone': '', 'purpose': '',
                    'extra_instructions': [], 'category_instructions': {}},
        'schedule': {'timezone': 'Asia/Seoul', 'weekdays': [0, 1, 2, 3, 4, 5, 6],
                     'start': '09:00', 'end': '21:00',
                     'daily_limits': {'coupang': 0, 'toss': 0}},
    }


def _next_midnight(at, zone_name):
    local = at.astimezone(ZoneInfo(zone_name))
    return datetime.combine(local.date() + timedelta(days=1), time(0, 0),
                            tzinfo=ZoneInfo(zone_name)).astimezone(timezone.utc)


def _settings(store, c, at):
    row = get_optional(store, c, 'automation_settings', SETTINGS_ID) or default_settings()
    pending = row.get('pending_schedule')
    if pending and at >= datetime.fromisoformat(pending['effective_at']):
        row['schedule'] = pending['schedule']
        row['pending_schedule'] = None
        store.put(c, 'automation_settings', row)
    return row


def get_settings(store, at):
    """Return current settings, promoting a pending schedule at local midnight."""
    at = at.astimezone(timezone.utc)
    with store.connect() as c:
        return public(_settings(store, c, at))


def save_settings(store, body, expected_version, at):
    """Persist settings; schedule edits wait for midnight in the old time zone."""
    at = at.astimezone(timezone.utc)
    valid = validate_settings(body)
    with store.connect() as c:
        existing = get_optional(store, c, 'automation_settings', SETTINGS_ID)
        current = existing or default_settings()
        if type(expected_version) is not int or current['version'] != expected_version:
            raise VersionConflict('자동 운영 설정 변경됨: 새로고침 후 다시 시도 필요')
        pending = current.get('pending_schedule')
        if pending and at >= datetime.fromisoformat(pending['effective_at']):
            current['schedule'] = pending['schedule']
            pending = None
        current['pending_schedule'] = None
        # The first save establishes the starting schedule immediately; later
        # edits wait so a day's already-created slots are never duplicated.
        if existing is None:
            current['schedule'] = valid['schedule']
        elif valid['schedule'] != current['schedule']:
            current['pending_schedule'] = {'effective_at': _next_midnight(at, current['schedule']['timezone']).isoformat(),
                                           'schedule': valid['schedule']}
        current.update(enabled=valid['enabled'], writing=valid['writing'],
                       version=current['version'] + 1, updated_at=now().isoformat() if at is None else at.isoformat())
        store.put(c, 'automation_settings', current)
        return public(current)


def claim(store, c, kind, id, states, at, *, steal_after=None):
    """Return a claimed copy only when no other live owner holds the record."""
    row = get_optional(store, c, kind, id)
    if row is None or row.get('state') not in states:
        return None
    if row.get('_claim') and owner_alive(row):
        return None
    token = uuid.uuid4().hex
    row.update(state=row['state'], _claim=token, **process_owner())
    store.put(c, kind, row)
    return row


def finish(store, c, kind, id, claim_token, changes):
    """Apply changes only for the current claimant; drops ownership fields."""
    row = get_optional(store, c, kind, id)
    if row is None or not claim_token or row.get('_claim') != claim_token:
        raise ValueError('작업 소유권이 변경됨: 결과를 덮어쓰지 않음')
    row.pop('_claim', None)
    row.pop('_pid', None)
    row.pop('_owner', None)
    row.update(changes)
    store.put(c, kind, row)
    return public(row)


def enqueue_notice(store, c, request_id, event_key, text, *, chat_id,
                   reply_to_message_id=None, force_reply=False):
    """Insert one durable outbox event; repeated keys return the same row."""
    notice_id = record_id('notice', request_id or '', event_key)
    existing = get_optional(store, c, 'notice', notice_id)
    if existing:
        return public(existing)
    notice = {'id': notice_id, 'request_id': request_id, 'event_key': event_key,
              'chat_id': chat_id, 'text': text, 'reply_to_message_id': reply_to_message_id,
              'force_reply': bool(force_reply), 'state': 'pending', 'attempts': 0,
              'next_attempt_at': None, 'telegram_message_id': None, 'error': '',
              'created_at': now().isoformat()}
    store.put(c, 'notice', notice)
    return public(notice)


def public_state(store, at, *, target=None):
    """Read-only view for the HTTP screen; never touches the network."""
    from .automation_schedule import slots_for_day
    at = at.astimezone(timezone.utc)
    with store.connect() as c:
        settings = public(_settings(store, c, at))
        requests = [public(row) for row in store.rows(c, 'automation_request')]
        notices = [public(row) for row in store.rows(c, 'notice')]
        publications = [public(row) for row in store.rows(c, 'automation_publication')]
    from .automation_products import request_providers
    counts = {provider: {} for provider in ('coupang', 'toss')}
    for row in requests:
        # A comparison post counts for every service it includes.
        for provider in request_providers(row):
            if provider in counts:
                counts[provider][row['state']] = counts[provider].get(row['state'], 0) + 1
    next_slots = slots_for_day(settings, at.astimezone(ZoneInfo(settings['schedule']['timezone'])).date(),
                               target) if target and settings['enabled'] else []
    next_slots.sort(key=lambda slot: slot['run_at'])
    return dict(settings=settings, counts=counts,
                requests=sorted(requests, key=lambda row: row.get('created_at', ''), reverse=True),
                notifications=sorted(notices, key=lambda row: row.get('created_at', ''), reverse=True)[:100],
                publications=publications,
                next_slots=[slot for slot in next_slots if datetime.fromisoformat(slot['run_at']) >= at])
