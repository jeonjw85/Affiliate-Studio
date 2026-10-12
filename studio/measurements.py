"""Manual, unverified reporting only. Never fetch report or page URLs."""
import csv
import io
import json
import re
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from fastapi import Request
from fastapi.responses import Response

from .core import VersionConflict, now, safe_url, text


async def _body(request):
    def reject_constant(value):
        raise ValueError('NaN/Infinity는 허용하지 않습니다.')
    try:
        body = json.loads(await request.body(), parse_constant=reject_constant)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise ValueError('올바른 JSON 객체가 필요합니다.') from None
    if not isinstance(body, dict):
        raise ValueError('JSON 객체가 필요합니다.')
    return body


def _validate(body):
    result = {'draft_id': text(body.get('draft_id'), 64)}
    channel = body.get('channel')
    if not isinstance(channel, str) or channel not in ('wordpress', 'naver', 'tistory', 'other'):
        raise ValueError('올바른 채널을 선택하세요.')
    result['channel'] = channel
    url = body.get('page_url', '')
    result['page_url'] = '' if url == '' else safe_url(url)
    dates = []
    for field in ('period_start', 'period_end'):
        value = body.get(field)
        if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
            raise ValueError('기간은 YYYY-MM-DD 형식이어야 합니다.')
        dates.append(date.fromisoformat(value))
        result[field] = value
    if dates[1] < dates[0] or dates[1] > now().date() + timedelta(days=1):
        raise ValueError('기간 순서 또는 미래 날짜를 확인하세요.')
    result['source'] = text(body.get('source'), 500)
    note = body.get('note', '')
    if not isinstance(note, str) or len(note) > 1000:
        raise ValueError('메모는 1000자 이하 문자열이어야 합니다.')
    result['note'] = text(note, 1000) if note.strip() else ''
    for field in ('views', 'clicks', 'orders'):
        value = body.get(field)
        if value is not None and (type(value) is not int or not 0 <= value <= 10**9):
            raise ValueError('조회/클릭/주문은 null 또는 0~10억 정수여야 합니다.')
        result[field] = value
    for field in ('revenue_won', 'cost_won'):
        value = body.get(field)
        if value is not None:
            if not isinstance(value, str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?', value):
                raise ValueError('금액은 소수 2자리 이하 숫자 문자열이어야 합니다.')
            if Decimal(value) > 10**12:
                raise ValueError('금액은 1조 이하여야 합니다.')
            value = _format(Decimal(value))
        result[field] = value
    return result


def _format(value):
    return format(value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP), 'f')


def _public(row, others=()):
    def ratio(numerator, denominator, scale=1):
        if numerator is None or denominator in (None, 0):
            return None
        return _format(Decimal(numerator) * scale / Decimal(denominator))

    warnings = []
    for high, low, message in (
        ('clicks', 'views', '클릭이 조회보다 많습니다. 반복 클릭 또는 귀속 기준을 확인하세요.'),
        ('orders', 'clicks', '주문이 클릭보다 많습니다. 귀속 기준을 확인하세요.'),
    ):
        if row[high] is not None and row[low] is not None and row[high] > row[low]:
            warnings.append(message)
    if any(other['id'] != row['id'] and _key(other)[:3] == _key(row)[:3]
           and other['period_start'] <= row['period_end'] and row['period_start'] <= other['period_end']
           for other in others):
        warnings.append('동일 콘텐츠/채널/URL의 기록 기간이 겹칩니다. 단순 합산하지 마세요.')
    revenue, cost = row.get('revenue_won'), row.get('cost_won')
    return dict(row, rates=dict(click_rate=ratio(row.get('clicks'), row.get('views'), 100),
                               order_rate=ratio(row.get('orders'), row.get('clicks'), 100),
                               epc_won=ratio(revenue, row.get('clicks')),
                               balance_won=None if revenue is None or cost is None else _format(Decimal(revenue) - Decimal(cost))),
                warnings=warnings)


def _key(row):
    return tuple(row[field] for field in ('draft_id', 'channel', 'page_url', 'period_start', 'period_end'))


def _unique(row, others):
    if any(other['id'] != row.get('id') and _key(other) == _key(row) for other in others):
        raise VersionConflict('동일 콘텐츠/채널/URL/기간의 중복 기록입니다.')


def _version(row, version):
    if type(version) is not int or version < 1:
        raise ValueError('버전은 양의 정수여야 합니다.')
    if row['version'] != version:
        raise VersionConflict('기록이 변경되었습니다. 새로고침 후 다시 시도하세요.')


def _csv(rows):
    fields = ('id', 'version', 'draft_id', 'draft_version', 'draft_title', 'channel', 'page_url',
              'period_start', 'period_end', 'source', 'views', 'clicks', 'orders', 'revenue_won',
              'cost_won', 'note', 'created_at', 'updated_at', 'click_rate', 'order_rate',
              'epc_won', 'balance_won', 'warnings')
    numeric = {'version', 'draft_version', 'views', 'clicks', 'orders', 'revenue_won', 'cost_won',
               'click_rate', 'order_rate', 'epc_won', 'balance_won'}
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        record = _public(row, rows)
        record.update(record.pop('rates'))
        record['warnings'] = ' | '.join(record['warnings'])
        for field, value in record.items():
            if field not in numeric and isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
                record[field] = "'" + value
        writer.writerow(record)
    return stream.getvalue().encode('utf-8-sig')


def install_routes(app, store):
    """Register on create_app's loopback/CSRF-protected FastAPI application."""
    @app.get('/api/measurements/export')
    def export():
        with store.connect() as c:
            content = _csv(store.rows(c, 'measurement'))
        return Response(content, media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition': 'attachment; filename="measurements.csv"'})

    @app.get('/api/measurements')
    def listing():
        with store.connect() as c:
            rows = store.rows(c, 'measurement')
            return {'items': [_public(row, rows) for row in rows]}

    @app.post('/api/measurements')
    async def create(request: Request):
        body = _validate(await _body(request))
        with store.connect() as c:
            draft = store.get(c, body['draft_id'], 'draft')
            rows = store.rows(c, 'measurement')
            _unique(body, rows)
            stamp = now().isoformat()
            row = dict(body, id=uuid4().hex, version=1, draft_version=draft['version'],
                       draft_title=draft['title'], created_at=stamp, updated_at=stamp)
            store.put(c, 'measurement', row)
            return _public(row, rows)

    @app.put('/api/measurements/{id}')
    async def update(id: str, request: Request):
        body = await _body(request)
        with store.connect() as c:
            old = store.get(c, id, 'measurement')
            _version(old, body.get('version'))
            valid = _validate(body)
            if valid['draft_id'] != old['draft_id']:
                raise ValueError('연결된 콘텐츠는 변경할 수 없습니다.')
            row = dict(old, **valid)
            rows = store.rows(c, 'measurement')
            _unique(row, rows)
            row.update(version=old['version'] + 1, updated_at=now().isoformat())
            store.put(c, 'measurement', row)
            return _public(row, rows)

    @app.delete('/api/measurements/{id}')
    async def delete(id: str, request: Request):
        body = await _body(request)
        with store.connect() as c:
            row = store.get(c, id, 'measurement')
            _version(row, body.get('version'))
            rows = store.rows(c, 'measurement')
            c.execute('DELETE FROM records WHERE id=? AND kind=?', (row['id'], 'measurement'))
            return _public(row, rows)
