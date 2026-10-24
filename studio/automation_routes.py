"""Thin HTTP connection for the automation screen.

Read and settings paths use the store directly so the screen works even when
Telegram/AI/WordPress are not all connected. Only runtime status comes from
the optional automation worker.
"""
from pathlib import Path

from fastapi.responses import FileResponse

from .automation_store import SETTINGS_ID, get_optional, get_settings, public, public_state, save_settings
from .core import now


def install_routes(app, store, status, refresh=None, retry_update=None, data_dir=None):
    def runtime():
        return status() or {'configured': False, 'telegram': False, 'ai': False,
                            'wordpress': False, 'running': False, 'last_error': '',
                            'affiliate_required': False}

    @app.get('/api/automation')
    def automation_state():
        current = runtime()
        payload = public_state(store, now(), target=current.get('wordpress_url') or None)
        payload['connections'] = current
        payload['connections']['affiliate_required'] = False
        payload['worker'] = {'running': bool(current.get('worker_running', current.get('running')))}
        return payload

    @app.get('/api/automation/requests/{request_id}')
    def automation_request(request_id: str):
        with store.connect() as c:
            row = next((item for item in store.rows(c, 'automation_request')
                        if item['id'] == request_id), None)
            if row is None:
                raise ValueError('자동 운영 요청 없음')
            article = next((item for item in store.rows(c, 'automation_article')
                            if item['request_id'] == request_id), None)
            publications = [item for item in store.rows(c, 'automation_publication')
                            if item['request_id'] == request_id]
            notices = [item for item in store.rows(c, 'notice')
                       if item.get('request_id') == request_id]
            images = {kind: media for kind in ('featured', 'body')
                      if (media := next((item for item in store.rows(c, 'automation_media')
                                         if item['request_id'] == request_id
                                         and item['kind'] == kind), None)) is not None}
            images = {kind: public(item) for kind, item in images.items()}
        directory = Path(data_dir) / 'media' if data_dir else None
        files = list(directory.glob('*')) if directory and directory.is_dir() else []
        media_usage = {'files': len(files), 'bytes': sum(path.stat().st_size for path in files)}
        return {'request': public(row), 'article': public(article) if article else None,
                'publications': [public(item) for item in publications],
                'notifications': [public(item) for item in notices],
                'images': images, 'media_usage': media_usage}

    @app.get('/api/automation/requests/{request_id}/media/{kind}')
    def automation_media(request_id: str, kind: str):
        if kind not in ('featured', 'body'):
            raise ValueError('이미지 종류 확인 필요')
        if data_dir is None:
            raise ValueError('이미지 저장 폴더 없음')
        with store.connect() as c:
            row = next((item for item in store.rows(c, 'automation_media')
                        if item['request_id'] == request_id and item['kind'] == kind
                        and item.get('state') == 'uploaded'), None)
        if row is None or not row.get('local_path'):
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail='이미지 없음')
        base = (Path(data_dir) / 'media').resolve()
        target = (base / Path(row['local_path']).name).resolve()
        if base not in target.parents or not target.is_file():
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail='이미지 파일 없음')
        return FileResponse(target, media_type=row.get('content_type') or 'application/octet-stream')

    @app.post('/api/automation/requests/{request_id}/update')
    def automation_update(request_id: str):
        if retry_update is None:
            raise ValueError('자동 운영 연결 미설정: Telegram·AI·WordPress 확인 필요')
        return retry_update(request_id)

    @app.post('/api/automation/requests/{request_id}/refresh')
    def automation_refresh(request_id: str):
        if refresh is None:
            raise ValueError('자동 운영 연결 미설정: Telegram·AI·WordPress 확인 필요')
        return refresh(request_id)

    @app.post('/api/automation/requests/{request_id}/update')
    def automation_update(request_id: str):
        if retry_update is None:
            raise ValueError('자동 운영 연결 미설정: Telegram·AI·WordPress 확인 필요')
        return retry_update(request_id)

    @app.post('/api/automation/requests/{request_id}/refresh')
    def automation_refresh(request_id: str):
        if refresh is None:
            raise ValueError('자동 운영 연결 미설정: Telegram·AI·WordPress 확인 필요')
        return refresh(request_id)

    @app.put('/api/automation/settings')
    def automation_settings(body: dict):
        current = get_settings(store, now())
        with store.connect() as c:
            exists = get_optional(store, c, 'automation_settings', SETTINGS_ID) is not None
        # A first-ever save may use the version shown before the row existed.
        expected = body.get('version') if exists else current['version']
        return save_settings(store, body, expected, now())
