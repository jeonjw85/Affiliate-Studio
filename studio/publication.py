"""Durably claimed publication operations; network is always outside SQLite locks."""
import uuid
import os
from datetime import timedelta
from .core import now, timestamp, VersionConflict


PROCESS_OWNER = uuid.uuid4().hex


def process_owner():
    return {'_pid': os.getpid(), '_owner': PROCESS_OWNER}


def public(record):
    return {k: v for k, v in record.items() if not k.startswith('_')}


def owner_alive(record):
    # Never steal an active claim on a timer. A slow network must not enable a
    # second remote mutation. Dead process recovery requires explicit refresh.
    pid = record.get('_pid')
    if type(pid) is not int or pid <= 0: return True
    # Container restarts commonly reuse this PID; an instance token distinguishes
    # the previous process from a live claim in the current single-process app.
    if pid == os.getpid() and record.get('_owner'):
        return record['_owner'] == PROCESS_OWNER
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def target(client, record=None):
    if client is None or (record is not None and client.url != record['target']):
        raise ValueError('현재 WordPress 연결이 원래 목적지와 다름: 원래 사이트 연결 복원 필요')


def classify(client, record, data):
    client.verify(data, record['_draft'], record['_product'])
    state = data.get('status')
    if state == 'future':
        if timestamp(data.get('date_gmt', '') + 'Z') != timestamp(record['run_at']):
            raise ValueError('원격 예약 시각 변경됨')
        return 'future'
    if state == 'publish': return 'publish'
    if state == 'draft': return 'cancelled'
    raise ValueError('원격 글 상태가 예상과 다름')


def finish(store, record, claim, state, error=''):
    with store.connect() as c:
        current = store.get(c, record['id'], 'publication')
        if current.get('_claim') != claim:
            raise VersionConflict('원격 상태 조회 소유권 변경됨')
        current.update(state=state, error=error, checked_at=now().isoformat())
        current.pop('_claim', None)
        store.put(c, 'publication', current)
        return public(current)


def schedule(store, id, version, run_at, confirmed, client):
    if confirmed is not True: raise ValueError('실제 공개 게시 예약 확인 필요')
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
            raise ValueError('공개 예약은 5분 이후부터 30일 이내로 지정 필요')
        if p['price'] is not None and requested > timestamp(p['observed_at']) + timedelta(hours=24):
            raise ValueError('예약 시점이 가격 확인 후 24시간 초과: 가격 제거 또는 재확인 필요')
        delivery = next((x for x in store.rows(c, 'delivery') if x['draft_id'] == id
                         and x['version'] == version and x['target'] == client.url and x['state'] == 'completed'), None)
        if not delivery: raise ValueError('같은 버전과 사이트에 WordPress 초안 먼저 전송 필요')
        claim = uuid.uuid4().hex
        record = dict(id=uuid.uuid4().hex, draft_id=id, version=version, target=client.url,
                      remote_id=delivery['remote_id'], remote_url=delivery.get('remote_url'),
                      run_at=due.isoformat(), state='sending', error='', created_at=now().isoformat(),
                       checked_at=None, _claim=claim, _draft=d, _product=p, **process_owner())
        store.put(c, 'publication', record)
    try:
        before = client.verify(client.read(record['remote_id']), d, p)
        if before.get('status') != 'draft': raise ValueError('원격 초안이 이미 변경됨')
        client.request('POST', '/' + str(record['remote_id']),
                       {'status': 'future', 'date_gmt': due.strftime('%Y-%m-%dT%H:%M:%S')})
        state = classify(client, record, client.read(record['remote_id']))
        if state != 'future': raise ValueError('공개 예약 재조회 결과가 예상과 다름')
        return finish(store, record, claim, state)
    except Exception:
        return finish(store, record, claim, 'uncertain',
                      '공개 예약 결과 불명: 원격 상태 조회 필요 (자동 재전송 안 함)')


def reconcile(store, id, client, cancel=False, confirmed=False):
    # WordPress REST has no atomic status CAS. Even a preflight GET cannot
    # prevent an unconditional draft POST from unpublishing a racing release.
    # Keep the legacy API fail-closed, before DB claims or any network access.
    if cancel:
        raise ValueError('앱에서 직접 취소 불가: WordPress 관리자에서 현재 상태 확인 후 예약 글을 초안으로 변경하고 앱의 원격 상태 조회 실행 필요 (이미 공개된 글은 앱에서 취소하지 않음)')
    with store.connect() as c:
        record = store.get(c, id, 'publication'); target(client, record)
        if record.get('_claim') and owner_alive(record):
            raise VersionConflict('원격 작업 진행 중: 완료 후 조회 필요')
        claim = uuid.uuid4().hex
        record['_claim'] = claim
        record.update(process_owner())
        store.put(c, 'publication', record)
    try:
        state = classify(client, record, client.read(record['remote_id']))
        if record['state'] in ('cancelled', 'publish') and state != record['state']:
            raise ValueError('완료 후 원격 상태가 수동 변경됨: 다시 검토 필요')
        return finish(store, record, claim, state)
    except Exception:
        return finish(store, record, claim, 'uncertain',
                      '원격 상태·제목·본문·예약 시각 확인 불가: WordPress에서 확인 필요')
