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


STATIC = Path(__file__).parent / 'static'


def create_app(data_dir=None):
    data = Path(data_dir or os.environ.get('STUDIO_DATA_DIR', Path(__file__).resolve().parent.parent / 'data'))
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    store = Store(data / 'studio.db')
    token = secrets.token_urlsafe(32)
    task = None
    worker_error = ''


    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({'error': str(error)}, status_code=409 if isinstance(error, VersionConflict) else 400)

    @app.middleware('http')
    async def boundary(request: Request, call_next):
        host = request.headers.get('host', '')
        if not re.fullmatch(r'(?:127\.0\.0\.1|localhost)(?::[0-9]{1,5})?', host):
            return JSONResponse({'error':'로컬 접속만 지원합니다.'}, status_code=403)
        origin = request.headers.get('origin')
        if origin and origin != f'{request.url.scheme}://{host}':
            return JSONResponse({'error':'다른 출처의 요청은 허용하지 않습니다.'}, status_code=403)
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'error':'외부 사이트 요청은 차단됩니다.'}, status_code=403)
        if request.method not in ('GET', 'HEAD'):
            supplied = request.headers.get('x-csrf-token', '')
            if not secrets.compare_digest(supplied, token):
                return JSONResponse({'error':'화면을 새로고침하고 다시 시도하세요.'}, status_code=403)
            if request.headers.get('content-type', '').split(';')[0] != 'application/json':
                return JSONResponse({'error':'JSON 요청만 지원합니다.'}, status_code=415)
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 131072:
                    return JSONResponse({'error':'요청이 너무 큽니다.'}, status_code=413)
            request._body = bytes(raw)
        response = await call_next(request)
        response.headers.update({
            'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff',
            'Referrer-Policy':'no-referrer', 'X-Frame-Options':'DENY',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-src 'self' about:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        })
        return response

    @app.get('/api/state')
    def state():
        return dict(store.state(), csrf=token, connections={}, worker_running=task is not None and not task.done() and not worker_error, worker_error=worker_error)







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
        return store.edit_draft(id, body.get('version'), body.get('title'), body.get('body'))

    @app.post('/api/drafts/{id}/approve')
    def approve(id: str, body: dict):
        return store.approve(id, body.get('version'), body.get('confirmed'))







    @app.get('/')
    def index():
        return FileResponse(STATIC / 'index.html')

    app.mount('/static', StaticFiles(directory=STATIC, check_dir=False), name='static')
    return app
