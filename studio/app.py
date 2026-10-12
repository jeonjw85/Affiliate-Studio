"""Single-user loopback server. Public hosting requires a separate auth design."""
import asyncio
import os
import re
import secrets
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from .core import Store, VersionConflict
from .wordpress import WordPress
from .ai import LocalAI
from .article_document import CSS_HASH


STATIC = Path(__file__).parent / 'static'


def create_app(data_dir=None):
    data = Path(data_dir or os.environ.get('STUDIO_DATA_DIR', Path(__file__).resolve().parent.parent / 'data'))
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    store = Store(data / 'studio.db')
    token = secrets.token_urlsafe(32)
    wp = None
    wp_error = ''
    env = [os.environ.get(k, '') for k in ('WP_URL', 'WP_USERNAME', 'WP_APP_PASSWORD')]
    if all(env):
        try:
            wp = WordPress(*env)
        except ValueError:
            wp_error = 'WordPress 서버 환경변수 형식 확인 필요'
    elif any(env):
        wp_error = 'WordPress 환경변수 세 개 모두 설정 필요'
    task = None
    worker_error = ''
    ai = None
    ai_error = ''
    ai_model = os.environ.get('AI_MODEL', '')
    if ai_model:
        try:
            ai = LocalAI(os.environ.get('AI_BASE_URL', 'http://127.0.0.1:11434/v1'), ai_model,
                         os.environ.get('AI_TIMEOUT_SECONDS', '180'), os.environ.get('AI_DAILY_LIMIT', '10'))
        except ValueError:
            ai_error = '로컬 AI 환경변수 형식 확인 필요 (외부 AI 주소는 허용 안 함)'
    else:
        ai_error = '로컬 모델 서버 실행과 AI_MODEL 설정 필요'

    async def worker():
        nonlocal worker_error
        while True:
            try:
                await asyncio.to_thread(store.run_due, data / 'exports')
                worker_error = ''
            except Exception:
                worker_error = '예약 처리 오류: DB 또는 저장 폴더 확인 필요'
            await asyncio.sleep(1)

    @asynccontextmanager
    async def lifespan(app):
        nonlocal task
        task = asyncio.create_task(worker())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({'error': str(error)}, status_code=409 if isinstance(error, VersionConflict) else 400)

    @app.middleware('http')
    async def boundary(request: Request, call_next):
        host = request.headers.get('host', '')
        if not re.fullmatch(r'(?:127\.0\.0\.1|localhost)(?::[0-9]{1,5})?', host):
            return JSONResponse({'error':'로컬 접속만 지원'}, status_code=403)
        origin = request.headers.get('origin')
        if origin and origin != f'{request.url.scheme}://{host}':
            return JSONResponse({'error':'다른 출처의 요청은 허용 안 함'}, status_code=403)
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'error':'외부 사이트 요청 차단'}, status_code=403)
        if request.method not in ('GET', 'HEAD'):
            supplied = request.headers.get('x-csrf-token', '')
            if not secrets.compare_digest(supplied, token):
                return JSONResponse({'error':'화면 새로고침 후 다시 시도 필요'}, status_code=403)
            if request.headers.get('content-type', '').split(';')[0] != 'application/json':
                return JSONResponse({'error':'JSON 요청만 지원'}, status_code=415)
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 131072:
                    return JSONResponse({'error':'요청 크기 초과'}, status_code=413)
            request._body = bytes(raw)
        response = await call_next(request)
        response.headers.update({
            'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff',
            'Referrer-Policy':'no-referrer', 'X-Frame-Options':'DENY',
            'Content-Security-Policy':f"default-src 'self'; script-src 'self'; style-src 'self' '{CSS_HASH}'; img-src 'self'; connect-src 'self'; frame-src 'self' about:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        })
        return response

    @app.get('/favicon.ico', include_in_schema=False)
    async def favicon():
        return Response(status_code=204)

    @app.get('/api/state')
    def state():
        return dict(store.state(), csrf=token, connections={'wordpress':wp is not None, 'wordpress_error':wp_error, 'wordpress_url': wp.url if wp else '',
                    'ai': ai is not None, 'ai_model': ai_model, 'ai_error': ai_error}, worker_running=task is not None and not task.done() and not worker_error, worker_error=worker_error)

    @app.post('/api/ai/check')
    def ai_check(body: dict):
        return ai.check() if ai else dict(available=False, model=ai_model, models=[], error=ai_error)

    @app.post('/api/drafts/{id}/ai')
    def generate_ai(id: str, body: dict):
        return store.generate_ai(id, body.get('version'), body.get('style'), body.get('confirmed_send'), ai)

    @app.post('/api/drafts/{id}/candidates/{candidate_id}/apply')
    def apply_ai(id: str, candidate_id: str, body: dict):
        return store.apply_candidate(id, candidate_id, body.get('version'))

    @app.post('/api/drafts/{id}/publish-schedule')
    def publish_schedule(id: str, body: dict):
        return store.publish_schedule(id, body.get('version'), body.get('run_at'), body.get('confirmed_publish'), wp)

    @app.post('/api/publications/{id}/refresh')
    def refresh_publication(id: str, body: dict):
        return store.refresh_publication(id, wp)

    @app.post('/api/publications/{id}/cancel')
    def cancel_publication(id: str, body: dict):
        return store.cancel_publication(id, body.get('confirmed_cancel'), wp)

    @app.post('/api/products')
    def add_product(body: dict):
        return store.add_product(body)

    @app.put('/api/products/{id}')
    def update_product(id: str, body: dict):
        return store.update_product(id, body)

    @app.post('/api/drafts')
    def create_draft(body: dict):
        return store.create_draft(body.get('product_id'))

    @app.get('/api/drafts/{id}')
    def detail(id: str):
        return store.detail(id)

    @app.put('/api/drafts/{id}')
    def edit(id: str, body: dict):
        return store.edit_draft(id, body.get('version'), body.get('title'), body.get('body'), body.get('body_format'))

    @app.post('/api/drafts/{id}/approve')
    def approve(id: str, body: dict):
        return store.approve(id, body.get('version'), body.get('confirmed'))

    @app.get('/api/drafts/{id}/download')
    def download(id: str, version: int):
        return Response(store.package(id, version), media_type='application/zip', headers={'Content-Disposition':f'attachment; filename="content-{id}.zip"'})

    @app.get('/api/drafts/{id}/text')
    def approved_text(id: str, version: int):
        from .exporting import render
        with store.connect() as c:
            d, p = store.approved_pair(c, id, version)
            content = render(d, p)['text']
        return Response(content, media_type='text/plain', headers={
            'Content-Disposition': f'attachment; filename="content-{d["id"]}-v{d["version"]}.txt"'})

    @app.post('/api/drafts/{id}/schedule')
    def schedule(id: str, body: dict):
        return store.schedule(id, body.get('version'), body.get('run_at'))

    @app.post('/api/jobs/{id}/cancel')
    def cancel(id: str):
        return store.cancel_job(id)

    @app.get('/api/jobs/{id}/download')
    def download_job(id: str):
        with store.connect() as c:
            j = store.get(c, id, 'job')
        if j['state'] != 'completed' or not j['artifact']:
            raise ValueError('완료된 파일 없음')
        path = data / 'exports' / j['artifact']
        if not path.is_file():
            raise ValueError('생성한 파일이 삭제되었거나 이동됨')
        return FileResponse(path, media_type='application/zip', filename=j['artifact'])

    @app.post('/api/drafts/{id}/wordpress')
    def wordpress(id: str, body: dict):
        return store.wordpress_draft(id, body.get('version'), body.get('confirmed'), wp)

    @app.get('/')
    def index():
        return FileResponse(STATIC / 'index.html')

    from .measurements import install_routes
    install_routes(app, store)
    app.mount('/static', StaticFiles(directory=STATIC, check_dir=False), name='static')
    return app
