"""Opt-in browser integration: fake AI protocol, real disposable local WP.
This does not run on public sites and never reads user WordPress settings.
"""
import sys, os, json, tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_ai import ai_server
from studio.wordpress import WordPress
from studio.app import create_app
import uvicorn

if __name__=='__main__':
    auth=json.loads(Path(os.environ['STUDIO_WP_FIXTURE_AUTH']).read_text())
    assert urlsplit(auth['url']).hostname=='127.0.0.1' and urlsplit(auth['url']).scheme=='http'
    with tempfile.TemporaryDirectory(prefix='studio-ui-v02-') as data, ai_server() as (url, remote):
        remote.answer={'title':'USB 키보드 구매 전 연결 조건','body':'등록된 정보에 따르면 USB 유선 연결 방식의 키보드입니다. 숫자 키 배열을 확인했으며, 운영체제와 호환 여부는 구매 전에 확인하세요.','evidence_refs':[1,2]}
        with patch.dict(os.environ,dict(AI_BASE_URL=url,AI_MODEL='local-test',WP_URL=auth['url'],WP_USERNAME=auth['username'],WP_APP_PASSWORD=auth['password'])):
            with patch('studio.app.WordPress',lambda *args,**kwargs:WordPress(*args,allow_local_test=True)):
                app=create_app(data)
            uvicorn.run(app,host='127.0.0.1',port=18087)
