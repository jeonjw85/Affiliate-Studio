"""Automatic WordPress publication with readback-first recovery.

Remote writes are never repeated blindly. A confirmed draft ID is stored
immediately; a lost create response is resolved by slug before any retry.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from urllib.parse import urlsplit

from .automation_store import claim, finish, get_optional, record_id
from .core import safe_url

STATUSES = ('draft', 'future', 'publish', 'pending', 'private', 'trash')


def _slug(request_id):
    return 'affiliate-' + sha256(request_id.encode()).hexdigest()[:24]


def _publication(store, c, request_id):
    return get_optional(store, c, 'automation_publication', record_id('pub', request_id))


def _snapshot(store, c, request_id):
    article = next((row for row in store.rows(c, 'automation_article')
                    if row['request_id'] == request_id), None)
    if article is None:
        raise ValueError('자동 글 작성 결과 없음')
    return article['snapshot'] if article.get('snapshot') else {
        'title': article['title'], 'html': article['html'],
        'provider': article['provider'], 'affiliate_url': article['affiliate_url']}


def _request(store, c, request_id):
    return store.get(c, request_id, 'automation_request')


def _target(client, request, publication=None):
    if client is None or getattr(client, 'url', None) != request.get('target') \
            or any(row.get('target') != request.get('target')
                   for row in ([publication] if publication else [])):
        raise ValueError('현재 WordPress 연결이 원래 대상과 다름: 설정 확인 필요')


def _find_by_slug(client, slug):
    rows = client.request('GET', '', params={'slug': slug, 'status': ','.join(STATUSES), 'per_page': 2})
    if not isinstance(rows, list):
        raise ValueError('WordPress 원격 글 조회 형식 확인 불가')
    return rows


def _match(client, rows, snapshot):
    matched = []
    for row in rows:
        if not isinstance(row, dict) or type(row.get('id')) is not int or row['id'] <= 0:
            raise ValueError('WordPress 원격 글 형식 확인 불가')
        try:
            client.verify_snapshot(row, snapshot)
        except ValueError:
            continue
        matched.append(row)
    if len(matched) != 1:
        raise ValueError('원격 글 상태 확인 불가 또는 본문이 변경됨: WordPress에서 확인 필요')
    return matched[0]


def _state(status):
    return {'future': 'scheduled', 'publish': 'published', 'draft': 'draft'}.get(status, 'uncertain')


def _store(store, record):
    with store.connect() as c:
        store.put(c, 'automation_publication', record)
    return record


def ensure_draft(store, request_id, client, at):
    """Create or reconcile one draft for this request; never a second one."""
    at = at.astimezone(timezone.utc)
    states = ('ready', 'composing', 'checking', 'draft', 'draft_sending',
              'scheduled', 'published', 'uncertain', 'failed')
    with store.connect() as c:
        request = _request(store, c, request_id)
        _target(client, request)
        publication = _publication(store, c, request_id)
        snapshot = _snapshot(store, c, request_id)
        if publication is None:
            publication = {'id': record_id('pub', request_id), 'request_id': request_id,
                           'target': client.url, 'slug': _slug(request_id), 'remote_id': None,
                           'remote_url': '', 'state': 'draft_sending', 'run_at': None,
                           'checked_at': None, 'snapshot': snapshot, 'pending_snapshot': None,
                           'error': '', 'created_at': at.isoformat()}
            store.put(c, 'automation_publication', publication)
        elif publication.get('state') == 'published':
            return publication
        claimed = claim(store, c, 'automation_request', request_id, states, at)
        token = claimed['_claim'] if claimed else None
        publication = dict(publication)
    if not token:
        return publication

    remote_id = publication.get('remote_id')
    try:
        if remote_id:
            data = client.read(remote_id)
            client.verify_snapshot(data, publication['snapshot'])
            state = _state(data.get('status'))
            if state == 'published':
                # A public post is never mutated by automatic recovery; its
                # verified status is recorded so the workflow can complete.
                publication.update(state='published', remote_url=_admin(client.url, remote_id),
                                   checked_at=at.isoformat(), error='')
                _store(store, publication)
                _release(store, request_id, token, 'published')
                return publication
            publication.update(remote_url=_admin(client.url, remote_id), checked_at=at.isoformat(),
                               error='')
            publication['state'] = state if state != 'uncertain' else 'draft'
            _store(store, publication)
            _release(store, request_id, token, publication['state'])
            return publication
        # A prior create may have reached WordPress even if its response was
        # lost, so look up the deterministic slug before creating another.
        found = _find_by_slug(client, publication['slug'])
        if found:
            match = _match(client, found, publication['snapshot'])
            publication.update(remote_id=match['id'], state=_state(match.get('status')),
                               remote_url=_admin(client.url, match['id']),
                               checked_at=at.isoformat(), error='')
            _store(store, publication)
            _release(store, request_id, token, publication['state'])
            return publication
        created = client.request('POST', '', {
            'title': publication['snapshot']['title'], 'content': publication['snapshot']['html'],
            'slug': publication['slug'], 'status': 'draft',
            'comment_status': 'closed', 'ping_status': 'closed'})
        if not isinstance(created, dict) or type(created.get('id')) is not int or created['id'] <= 0:
            raise ValueError('WordPress 초안 ID 확인 불가')
        publication.update(remote_id=created['id'], state='draft', checked_at=at.isoformat(), error='')
        _store(store, publication)  # Persist the ID before any read-back.
        data = client.read(created['id'])
        client.verify_snapshot(data, publication['snapshot'])
        if data.get('status') != 'draft':
            raise ValueError('원격 글이 초안 상태가 아님')
        publication['remote_url'] = _admin(client.url, created['id'])
        _store(store, publication)
        _release(store, request_id, token, 'draft')
        return publication
    except Exception:
        # A lost create response cannot be told apart from a create that never
        # ran; the slug lookup on the next pass is the only safe recovery.
        # Transport errors (not just ValueError) must release the claim too,
        # otherwise the item stays owned by this live process forever.
        publication.update(state='uncertain', checked_at=at.isoformat(),
                           error='전송 결과 불명: WordPress 관리자에서 확인 필요 (자동 재전송 안 함)')
        _store(store, publication)
        _release(store, request_id, token, 'uncertain')
        return publication


def _message(error):
    return str(error) if isinstance(error, ValueError) else 'WordPress 연결 실패: 잠시 후 다시 확인 필요'


def _admin(site, remote_id):
    return site.rstrip('/') + f'/wp-admin/post.php?post={remote_id}&action=edit'


def _release(store, request_id, token, state):
    with store.connect() as c:
        request = _request(store, c, request_id)
        finish(store, c, 'automation_request', request_id, token, {'state': state})


def _with_publication(store, request_id):
    with store.connect() as c:
        return _publication(store, c, request_id)


def schedule_post(store, request_id, client, run_at, at):
    """Change only the confirmed draft of this request to a future post."""
    at = at.astimezone(timezone.utc)
    due = run_at.astimezone(timezone.utc).replace(microsecond=0)
    if (due - at).total_seconds() < 300 or (due - at).total_seconds() > 30 * 86400:
        raise ValueError('자동 예약은 현재부터 5분 이후 30일 이내 필요')
    with store.connect() as c:
        request = _request(store, c, request_id)
        _target(client, request)
        publication = _publication(store, c, request_id)
        if publication is None or not publication.get('remote_id'):
            raise ValueError('확인된 원격 초안 먼저 필요')
        if publication['state'] in ('scheduled', 'published'):
            if publication['state'] == 'scheduled' and publication.get('run_at') == due.isoformat():
                return publication
            if publication['state'] == 'published':
                raise ValueError('이미 공개된 글은 변경하지 않음')
        if publication['state'] not in ('draft', 'uncertain', 'scheduled'):
            raise ValueError('원격 초안 상태 확인 필요')
        claimed = claim(store, c, 'automation_request', request_id,
                        ('draft', 'draft_sending', 'checking', 'scheduled', 'uncertain', 'composing'), at)
        if claimed is None:
            raise ValueError('자동 예약 작업이 이미 진행 중')
        token = claimed['_claim']
        snapshot = publication['snapshot']
        remote_id = publication['remote_id']
    try:
        before = client.read(remote_id)
        client.verify_snapshot(before, snapshot)
        if before.get('status') == 'publish':
            publication.update(state='published', checked_at=at.isoformat(), error='')
            _store(store, publication)
            _release(store, request_id, token, 'published')
            return publication
        client.request('POST', '/' + str(remote_id),
                       {'status': 'future', 'date_gmt': due.strftime('%Y-%m-%dT%H:%M:%S')})
        after = client.read(remote_id)
        client.verify_snapshot(after, snapshot)
        if after.get('status') != 'future' or not _same_due(after.get('date_gmt'), due):
            raise ValueError('원격 예약 시각·상태 확인 불가')
    except Exception as error:
        publication.update(state='uncertain', error=_message(error), checked_at=at.isoformat())
        _store(store, publication)
        _release(store, request_id, token, 'uncertain')
        return publication
    publication.update(state='scheduled', run_at=due.isoformat(), checked_at=at.isoformat(), error='')
    _store(store, publication)
    _release(store, request_id, token, 'scheduled')
    return publication


def _same_due(value, due):
    try:
        parsed = datetime.fromisoformat(str(value).rstrip('Z') + '+00:00')
    except ValueError:
        return False
    return parsed.astimezone(timezone.utc).replace(microsecond=0) == due


def replace_draft_snapshot(store, request_id, client, new_snapshot, at):
    """Update the one confirmed draft in place when its snapshot ages out."""
    at = at.astimezone(timezone.utc)
    with store.connect() as c:
        request = _request(store, c, request_id)
        _target(client, request)
        publication = _publication(store, c, request_id)
        if publication is None or not publication.get('remote_id'):
            raise ValueError('확인된 원격 초안 먼저 필요')
        if publication['state'] in ('scheduled', 'published'):
            raise ValueError('예약·공개된 글의 본문은 자동 변경하지 않음')
        claimed = claim(store, c, 'automation_request', request_id, ('draft', 'uncertain'), at)
        token = claimed['_claim'] if claimed else None
        publication = dict(publication, pending_snapshot=new_snapshot)
        store.put(c, 'automation_publication', publication)
    remote_id = publication['remote_id']
    try:
        client.request('POST', '/' + str(remote_id),
                       {'title': new_snapshot['title'], 'content': new_snapshot['html']})
        data = client.read(remote_id)
        client.verify_snapshot(data, new_snapshot)
    except Exception as error:
        try:
            data = client.read(remote_id)
            # Recover from a lost response: the new snapshot may already be live.
            client.verify_snapshot(data, new_snapshot)
        except ValueError:
            publication.update(state='uncertain', error='초안 갱신 결과 불명: WordPress에서 확인 필요',
                               checked_at=at.isoformat())
            _store(store, publication)
            if token:
                _release(store, request_id, token, 'uncertain')
            return publication
    publication.update(snapshot=new_snapshot, pending_snapshot=None, state='draft',
                       checked_at=at.isoformat(), error='')
    _store(store, publication)
    if token:
        _release(store, request_id, token, 'draft')
    return publication


def reconcile_post(store, request_id, client, at):
    """Read-only refresh; records only the status WordPress actually shows."""
    at = at.astimezone(timezone.utc)
    with store.connect() as c:
        request = _request(store, c, request_id)
        _target(client, request)
        publication = _publication(store, c, request_id)
        if publication is None or not publication.get('remote_id'):
            raise ValueError('확인된 원격 글 없음')
        snapshot = publication['snapshot']
        remote_id = publication['remote_id']
        run_at = publication.get('run_at')
    data = client.read(remote_id)
    client.verify_snapshot(data, snapshot)
    state = _state(data.get('status'))
    if state == 'scheduled' and run_at and not _same_due(data.get('date_gmt'), datetime.fromisoformat(run_at)):
        raise ValueError('원격 예약 시각 변경됨')
    public_url = data.get('link') if state == 'published' else None
    try:
        public_url = safe_url(public_url) if public_url else None
    except ValueError:
        public_url = None
    publication = dict(publication, state=state, checked_at=at.isoformat(), error='',
                       remote_url=public_url or _admin(client.url, remote_id))
    _store(store, publication)
    with store.connect() as c:
        current = _request(store, c, request_id)
        if state in ('published', 'draft'):
            store.put(c, 'automation_request', {**current, 'state': state})
    return publication
