"""Exercise the release ZIP in a fresh directory and environment on this host."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]


def main():
    archive=ROOT/'dist/affiliate-studio-0.4.1.zip'
    with tempfile.TemporaryDirectory(prefix='affiliate-release-') as temp:
        with zipfile.ZipFile(archive) as z:
            assert z.testzip() is None
            z.extractall(temp)
        target=Path(temp)/'affiliate-studio'
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        env={**os.environ,'STUDIO_DATA_DIR':str(target/'data'),'WP_URL':'','WP_USERNAME':'','WP_APP_PASSWORD':'','AI_MODEL':'','AI_BASE_URL':'','AI_TIMEOUT_SECONDS':'180'}
        def fetch(url='/api/state',data=None,token=''):
            req=urllib.request.Request(base+url,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json','X-CSRF-Token':token})
            with urllib.request.urlopen(req,timeout=5) as response:return response.read()
        def launch(log):
            server=subprocess.Popen(['sh','start.sh','--port',str(port)],cwd=target,env=env,stdout=log,stderr=subprocess.STDOUT)
            end=time.monotonic()+180
            while time.monotonic()<end:
                if server.poll() is not None: raise RuntimeError('Release startup failed; see install-release.log')
                try:
                    json.loads(fetch());return server
                except (OSError,ValueError):time.sleep(.2)
            server.terminate();server.wait(timeout=20)
            raise TimeoutError('Release readiness timeout')
        with (ROOT/'artifacts/install-release.log').open('w') as log:
            server=launch(log)
            try:
                state=json.loads(fetch());assert state['products']==[] and state['worker_running']
                assert 'Affiliate Studio'.encode() in fetch('/')
                assert len(fetch('/static/style.css'))>8000
                payload=dict(provider='coupang',name='릴리스 실행 검증용 합성 데이터',source_url='https://example.com/product/fixture',affiliate_url='https://example.com/affiliate/fixture',price=10000,observed_at=datetime.now(timezone.utc).isoformat(),facts=['합성 테스트 항목'],audience='실행 검증',caveats='실제 제휴 상품이 아닙니다.',ownership_confirmed=True)
                product=json.loads(fetch('/api/products',payload,state['csrf']))
            finally:
                server.terminate();server.wait(timeout=20)
            server=launch(log)
            try:
                state=json.loads(fetch());assert state['products'][0]['id']==product['id']
                assert (target/'.venv/installed.lock').read_bytes()==(target/'requirements.lock').read_bytes()
            finally:
                server.terminate();server.wait(timeout=20)
        result=subprocess.run([str(target/'.venv/bin/python'),'-m','pytest','tests','-q'],cwd=target,env=env,capture_output=True,text=True,timeout=180)
        assert result.returncode==0,result.stdout+result.stderr
        npm=subprocess.run(['npm','ci','--ignore-scripts'],cwd=target,env=env,capture_output=True,text=True,timeout=120)
        assert npm.returncode==0,npm.stdout+npm.stderr
        dom=subprocess.run(['node','--test','tests/v02-actions.cjs','tests/v02-dom.cjs','tests/v03-metrics.cjs'],cwd=target,env=env,capture_output=True,text=True,timeout=45)
        assert dom.returncode==0,dom.stdout+dom.stderr
        browser=subprocess.run(['node','tests/e2e.mjs'],cwd=target,env=env,capture_output=True,text=True,timeout=120)
        assert browser.returncode==0,browser.stdout+browser.stderr
        v03=subprocess.run(['node','tests/e2e_v03.mjs'],cwd=target,env=env,capture_output=True,text=True,timeout=120)
        assert v03.returncode==0,v03.stdout+v03.stderr
        v04=subprocess.run(['node','tests/e2e_v04.mjs'],cwd=target,env=env,capture_output=True,text=True,timeout=120)
        assert v04.returncode==0,v04.stdout+v04.stderr
        report=dict(extracted_v04_browser=v04.stdout.strip(),extracted_v03_browser=v03.stdout.strip(),fresh_install=True,http_ui=True,restart_persistence=True,extracted_tests=result.stdout.strip(),extracted_dom=dom.stdout.strip(),extracted_browser=browser.stdout.strip(),archive_bytes=archive.stat().st_size)
        (ROOT/'artifacts/release-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
