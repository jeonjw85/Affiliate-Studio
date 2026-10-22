"""Shared synthetic data for automation tests. No real links or accounts."""
from datetime import datetime, timezone


def make_settings(**overrides):
    schedule = {
        'timezone': 'Asia/Seoul', 'weekdays': [0, 1, 2, 3, 4, 5, 6],
        'start': '09:00', 'end': '21:00', 'daily_limits': {'coupang': 1, 'toss': 1},
    }
    schedule.update(overrides.pop('schedule', {}))
    for key in ('timezone', 'start', 'end', 'weekdays', 'daily_limits'):
        if key in overrides:
            schedule[key] = overrides.pop(key)
    writing = {'topic': '생활용품', 'audience': '정리 습관을 만드는 독자', 'tone': '차분한 존댓말',
               'purpose': '', 'extra_instructions': [], 'category_instructions': {}}
    writing.update(overrides.pop('writing', {}))
    body = {'enabled': overrides.pop('enabled', False), 'writing': writing, 'schedule': schedule}
    assert not overrides, overrides
    from studio.automation_schedule import validate_settings
    return {'version': 0, **validate_settings(body)}


def make_evidence(facts=None, **overrides):
    now = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc).isoformat()
    evidence = {
        'name': '합성 상품',
        'facts': [{'text': '합성 사실 하나', 'source': 'page', 'observed_at': now}]
        if facts is None else facts,
        'price': None,
        'category': '',
        'audience': '',
        'source_url': None,
        'product_key': None,
        'conflicts': [],
    }
    evidence.update(overrides)
    return evidence


def message_update(update_id, text, *, message_id=None, chat_id=10, user_id=10,
                   entities=None, reply_to=None):
    message = {
        'message_id': update_id if message_id is None else message_id,
        'date': 1792000000,
        'chat': {'id': chat_id, 'type': 'private'},
        'from': {'id': user_id, 'is_bot': False, 'first_name': 'fixture'},
        'text': text,
    }
    if entities is not None:
        message['entities'] = entities
    if reply_to is not None:
        message['reply_to_message'] = reply_to
    return {'update_id': update_id, 'message': message}
