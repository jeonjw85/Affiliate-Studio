"""Service-independent daily slots with explicit time zones and DST rules."""
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PROVIDERS = ('coupang', 'toss')


def _clock(value, label):
    if not isinstance(value, str) or len(value) != 5 or value[2] != ':' \
            or not (value[:2] + value[3:]).isascii() or not (value[:2] + value[3:]).isdigit():
        raise ValueError(f'{label}는 HH:MM 형식 필요')
    hour, minute = int(value[:2]), int(value[3:])
    if hour > 23 or minute > 59:
        raise ValueError(f'{label}는 HH:MM 형식 필요')
    return hour, minute


def _wording(value, label):
    if not isinstance(value, str):
        raise ValueError(f'{label}는 문자열 필요')
    return value.strip()


def validate_settings(body):
    """Validate user-editable automation settings and return normalized values."""
    if not isinstance(body, dict):
        raise ValueError('자동 운영 설정 형식 확인 필요')
    enabled = body.get('enabled', False)
    if type(enabled) is not bool:
        raise ValueError('자동 운영 활성화 값은 참/거짓 필요')
    writing = body.get('writing')
    if not isinstance(writing, dict):
        raise ValueError('작성 지침 형식 확인 필요')
    result_writing = {}
    for field, limit in (('topic', 500), ('audience', 500), ('tone', 500), ('purpose', 500)):
        value = _wording(writing.get(field, ''), field)
        if len(value) > limit:
            raise ValueError(f'{field}는 {limit}자 이하 필요')
        result_writing[field] = value
    extra = writing.get('extra_instructions', [])
    if not isinstance(extra, list) or len(extra) > 20:
        raise ValueError('추가 지침은 최대 20개 목록 필요')
    result_writing['extra_instructions'] = [_wording(item, '추가 지침') for item in extra]
    if any(not item or len(item) > 2000 for item in result_writing['extra_instructions']):
        raise ValueError('추가 지침은 항목당 1~2,000자 필요')
    category = writing.get('category_instructions', {})
    if not isinstance(category, dict) or len(category) > 20:
        raise ValueError('카테고리 지침은 최대 20개 필요')
    result_writing['category_instructions'] = {}
    for key, value in category.items():
        key, value = _wording(key, '카테고리'), _wording(value, '카테고리 지침')
        if not key or len(key) > 100 or not value or len(value) > 2000:
            raise ValueError('카테고리 지침 형식 확인 필요')
        result_writing['category_instructions'][key] = value
    schedule = body.get('schedule')
    if not isinstance(schedule, dict):
        raise ValueError('발행 일정 형식 확인 필요')
    timezone_name = schedule.get('timezone')
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        raise ValueError('올바른 IANA 시간대 필요') from None
    weekdays = schedule.get('weekdays')
    if not isinstance(weekdays, list) or not weekdays or len(weekdays) > 7 \
            or any(type(day) is not int or not 0 <= day <= 6 for day in weekdays) \
            or len(set(weekdays)) != len(weekdays):
        raise ValueError('발행 요일은 중복 없는 0~6 정수 목록 필요')
    start, end = _clock(schedule.get('start'), '시작'), _clock(schedule.get('end'), '종료')
    if not start < end:
        raise ValueError('발행 시작 시각은 종료 시각보다 빨라야 함')
    limits = schedule.get('daily_limits')
    if not isinstance(limits, dict) or set(limits) != set(PROVIDERS):
        raise ValueError('쿠팡·토스 하루 편수 필요')
    minutes = (end[0] * 60 + end[1]) - (start[0] * 60 + start[1])
    result_limits = {}
    for provider in PROVIDERS:
        count = limits[provider]
        if type(count) is not int or not 0 <= count <= 100 or count > minutes:
            raise ValueError(f'{provider} 하루 편수는 0~{min(100, minutes)} 정수 필요')
        result_limits[provider] = count
    if enabled and not any(result_limits.values()):
        raise ValueError('자동 운영을 켜려면 쿠팡 또는 토스 하루 편수 중 하나 이상 필요')
    return {
        'enabled': enabled,
        'writing': result_writing,
        'schedule': {'timezone': timezone_name, 'weekdays': sorted(weekdays),
                     'start': schedule['start'], 'end': schedule['end'], 'daily_limits': result_limits},
    }


def slots_for_day(settings, day, target):
    """UTC slots for one local calendar day, evenly spread within each service."""
    from .automation_store import record_id
    schedule = settings['schedule']
    zone = ZoneInfo(schedule['timezone'])
    if day.weekday() not in schedule['weekdays'] or not target:
        return []
    start = int(schedule['start'][:2]) * 60 + int(schedule['start'][3:])
    end = int(schedule['end'][:2]) * 60 + int(schedule['end'][3:])
    slots = []
    for provider in PROVIDERS:
        count = schedule['daily_limits'][provider]
        span = end - start
        seen = set()
        for index in range(count):
            minute = start + span * index // count
            hour, rest = divmod(minute, 60)
            local = datetime.combine(day, time(hour, rest)).replace(tzinfo=zone, fold=0)
            resolved = local.astimezone(zone)
            # Skip a spring-forward gap and the repeated autumn hour; both
            # would otherwise give two indexes the same publish instant.
            if resolved.replace(tzinfo=None) != local.replace(tzinfo=None):
                continue
            run_at = resolved.astimezone(timezone.utc)
            if run_at in seen:
                continue
            seen.add(run_at)
            slots.append({
                'id': record_id('slot', target, provider, day.isoformat(), str(index)),
                'target': target, 'provider': provider, 'local_date': day.isoformat(),
                'index': index, 'run_at': run_at.isoformat(),
                'request_id': None, 'state': 'open'})
    return slots


def claimable(slot, at):
    """Assignment is allowed from 15 to 5 minutes before the publish instant."""
    delta = (datetime.fromisoformat(slot['run_at']) - at).total_seconds()
    return 300 <= delta <= 900
