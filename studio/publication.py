"""Durably claimed publication operations; network is always outside SQLite locks."""
import uuid
import os
from datetime import timedelta
from .core import now, timestamp, VersionConflict


def public(record):
    return {k: v for k, v in record.items() if not k.startswith('_')}


def owner_alive(record):
    # Never steal an active claim on a timer. A slow network must not enable a
    # second remote mutation. Dead process recovery requires explicit refresh.
    pid = record.get('_pid')
    if type(pid) is not int or pid <= 0: return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def target(client, record=None):
    if client is None or (record is not None and client.url != record['target']):
        raise ValueError('현재 WordPress 연결이 원래 목적지와 다릅니다. 원래 사이트 연결을 복원하세요.')


def classify(client, record, data):
    client.verify(data, record['_draft'], record['_product'])
    state = data.get('status')
    if state == 'future':
        if timestamp(data.get('date_gmt', '') + 'Z') != timestamp(record['run_at']):
            raise ValueError('원격 예약 시각이 변경됐습니다.')
        return 'future'
    if state == 'publish': return 'publish'
    if state == 'draft': return 'cancelled'
    raise ValueError('원격 글 상태가 예상과 다릅니다.')


def finish(store, record, claim, state, error=''):
    with store.connect() as c:
        current = store.get(c, record['id'], 'publication')
        if current.get('_claim') != claim:
            raise VersionConflict('원격 상태 조회 소유권이 변경됐습니다.')
        current.update(state=state, error=error, checked_at=now().isoformat())
        current.pop('_claim', None)
        store.put(c, 'publication', current)
        return public(current)


def schedule(store, id, version, run_at, confirmed, client):
    if confirmed is not True: raise ValueError('실제 공개 게시 예약 확인이 필요합니다.')
    target(client)
    with store.connect() as c:
        d = store.get(c, id, 'draft'); store.assert_version(d, version)
        old = next((x for x in store.rows(c, 'publication') if x['draft_id'] == id
                    and x['version'] == version and x['target'] == client.url), None)
        if old: return public(old)
        store.assert_unlocked(c, id)
        d, p = store.approved_pair(c, id, version)
        requested = timestamp(run_at)
        due = requested.replace(microsecond=0)
        if (due - now()).total_seconds() < 300 or (requested - now()).total_seconds() > 30 * 86400:
            raise ValueError('공개 예약은 5분 이후부터 30일 이내로 지정하세요.')
        if p['price'] is not None and requested > timestamp(p['observed_at']) + timedelta(hours=24):
            raise ValueError('예약 시점이 가격 확인 후 24시간을 넘습니다. 가격을 제거하거나 재확인하세요.')
        delivery = next((x for x in store.rows(c, 'delivery') if x['draft_id'] == id
                         and x['version'] == version and x['target'] == client.url and x['state'] == 'completed'), None)
        if not delivery: raise ValueError('같은 버전과 사이트에 WordPress 초안을 먼저 전송하세요.')
        claim = uuid.uuid4().hex
        record = dict(id=uuid.uuid4().hex, draft_id=id, version=version, target=client.url,
                      remote_id=delivery['remote_id'], remote_url=delivery.get('remote_url'),
                      run_at=due.isoformat(), state='sending', error='', created_at=now().isoformat(),
                      checked_at=None, _claim=claim, _pid=os.getpid(), _draft=d, _product=p)
        store.put(c, 'publication', record)
    try:
        before = client.verify(client.read(record['remote_id']), d, p)
        if before.get('status') != 'draft': raise ValueError('원격 초안이 이미 변경됐습니다.')
        client.request('POST', '/' + str(record['remote_id']),
                       {'status': 'future', 'date_gmt': due.strftime('%Y-%m-%dT%H:%M:%S')})
        state = classify(client, record, client.read(record['remote_id']))
        if state != 'future': raise ValueError('공개 예약 재조회가 예상과 다릅니다.')
        return finish(store, record, claim, state)
    except Exception:
        return finish(store, record, claim, 'uncertain',
                      '공개 예약 결과를 확정할 수 없습니다. 원격 상태를 조회하세요. 자동 재전송하지 않습니다.')


def reconcile(store, id, client, cancel=False, confirmed=False):
    # WordPress REST has no atomic status CAS. Even a preflight GET cannot
    # prevent an unconditional draft POST from unpublishing a racing release.
    # Keep the legacy API fail-closed, before DB claims or any network access.
    if cancel:
        raise ValueError('앱 직접 취소는 지원하지 않습니다. WordPress 관리자에서 현재 상태를 확인하고 예약 글을 초안으로 돌린 뒤 앱의 원격 상태 조회를 실행하세요. 이미 공개된 글은 앱에서 취소하지 않습니다.')
    with store.connect() as c:
        record = store.get(c, id, 'publication'); target(client, record)
        if record.get('_claim') and owner_alive(record):
            raise VersionConflict('원격 작업이 진행 중입니다. 완료 후 조회하세요.')
        claim = uuid.uuid4().hex
        record['_claim'] = claim
        record['_pid'] = os.getpid()
        store.put(c, 'publication', record)
    try:
        state = classify(client, record, client.read(record['remote_id']))
        if record['state'] in ('cancelled', 'publish') and state != record['state']:
            raise ValueError('완료 후 원격 상태가 수동 변경됐습니다. 다시 검토하세요.')
        return finish(store, record, claim, state)
    except Exception:
        return finish(store, record, claim, 'uncertain',
                      '원격 상태·제목·본문·예약 시각을 확정하지 못했습니다. WordPress에서 확인하세요.')
