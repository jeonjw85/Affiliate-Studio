"""Thin HTTP connection for the automation screen.

Read and settings paths use the store directly so the screen works even when
Telegram/AI/WordPress are not all connected. Only runtime status comes from
the optional automation worker.
"""
from .automation_store import SETTINGS_ID, get_optional, get_settings, public, public_state, save_settings
from .core import now


def install_routes(app, store, status, refresh=None, retry_update=None):
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
        return {'request': public(row), 'article': public(article) if article else None,
                'publications': [public(item) for item in publications],
                'notifications': [public(item) for item in notices]}

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

    @app.put('/api/automation/settings')
    def automation_settings(body: dict):
        current = get_settings(store, now())
        with store.connect() as c:
            exists = get_optional(store, c, 'automation_settings', SETTINGS_ID) is not None
        # A first-ever save may use the version shown before the row existed.
        expected = body.get('version') if exists else current['version']
        return save_settings(store, body, expected, now())
