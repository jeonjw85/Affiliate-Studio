"""Opt-in real local-model + disposable local WordPress integration. No public sites.
Run with STUDIO_WP_FIXTURE_AUTH=/absolute/path/to/test-auth.json AI_MODEL=...
This file is not collected by pytest. Credentials are not printed or archived.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi.testclient import TestClient
from studio.app import create_app
from studio.wordpress import WordPress


def main():
    fixture = json.loads(Path(os.environ['STUDIO_WP_FIXTURE_AUTH']).read_text())
    url = urlsplit(fixture['url'])
    assert url.scheme == 'http' and url.hostname == '127.0.0.1', 'Disposable localhost fixture only'
    assert fixture['username'] == 'studio_fixture', 'Never use a real account in this probe'
    model = os.environ['AI_MODEL']
    env = {'WP_URL':fixture['url'], 'WP_USERNAME':fixture['username'], 'WP_APP_PASSWORD':fixture['password']}
    report = {'scope':'Real local model + real disposable local WordPress. Synthetic product, not a public user site.', 'model':model, 'wordpress_version':fixture['version'], 'checks':[]}
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='studio-v02-live-') as tmp, patch.dict(os.environ, env), patch('studio.app.WordPress', lambda *args, **kwargs: WordPress(*args, allow_local_test=True)):
        with TestClient(create_app(tmp), base_url='http://127.0.0.1') as c:
            state=c.get('/api/state').json(); headers={'X-CSRF-Token':state['csrf']}
            def post(path, data):
                r=c.post(path, json=data, headers=headers)
                assert r.status_code == 200, (path, r.status_code, r.text)
                return r.json()
            ai=post('/api/ai/check', {}); assert ai['available'], ai
            report['checks'].append('Local model availability via app endpoint')
            p=post('/api/products', dict(provider='coupang', name='검증용 USB 키보드', source_url='https://example.com/fixture-keyboard', affiliate_url='https://example.com/fixture-affiliate', price=None, observed_at=datetime.now(timezone.utc).isoformat(), facts=['USB 유선 연결','숫자 키패드 포함'], audience='문서 작업이 잦은 독자', caveats='호환 운영체제와 제품 크기는 확인하지 않았습니다. 실제 상품이 아닌 합성 검증용 데이터입니다.', ownership_confirmed=True))
            d=post('/api/drafts', {'product_id':p['id']}); original=d.copy(); route='/api/drafts/'+d['id']
            ai_started=time.monotonic()
            candidate=post(route+'/ai', {'version':d['version'],'style':'concise','confirmed_send':True})
            report['ai_seconds']=round(time.monotonic()-ai_started,2)
            report['candidate']={k:candidate[k] for k in ('title','body','evidence_refs','model')}
            assert c.get(route).json()['draft']==original, 'Generation must not overwrite or approve'
            d=post(route+'/candidates/'+candidate['id']+'/apply', {'version':d['version']})
            assert d['status']=='review' and d['body']==candidate['body']
            d=post(route+'/approve', {'version':d['version'],'confirmed':True})
            report['checks'].append('Actual local AI generation → candidate → apply → human approval')
            delivery=post(route+'/wordpress', {'version':d['version'],'confirmed':True})
            assert delivery['state']=='completed', delivery
            report['checks'].append('Actual WordPress draft creation and read-back')
            payload={'version':d['version'],'run_at':(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat(),'confirmed_publish':True}
            publication=post(route+'/publish-schedule',payload); assert publication['state']=='future', publication
            repeat=post(route+'/publish-schedule',payload); assert repeat['id']==publication['id']
            locked=c.put(route,json={'version':d['version'],'title':d['title'],'body':'예약 중 수정 시도'},headers=headers)
            assert locked.status_code in (400,409), locked.text
            refreshed=post('/api/publications/'+publication['id']+'/refresh',{}); assert refreshed['state']=='future',refreshed
            denied=c.post('/api/publications/'+publication['id']+'/cancel',headers=headers,json={'confirmed_cancel':True});assert denied.status_code in (400,409)
            # Explicitly simulate the operator changing this disposable local site in WP admin.
            with httpx.Client(auth=(fixture['username'],fixture['password']),trust_env=False) as remote:
                before_cancel=remote.get(fixture['url']+'/wp-json/wp/v2/posts/'+str(publication['remote_id']),params={'context':'edit'});before_cancel.raise_for_status();assert before_cancel.json()['status']=='future'
                manual=remote.post(fixture['url']+'/wp-json/wp/v2/posts/'+str(publication['remote_id']),json={'status':'draft'});manual.raise_for_status()
            cancelled=post('/api/publications/'+publication['id']+'/refresh',{});assert cancelled['state']=='cancelled'
            remote=httpx.get(fixture['url']+'/wp-json/wp/v2/posts/'+str(delivery['remote_id']),params={'context':'edit'},auth=(fixture['username'],fixture['password']),trust_env=False)
            remote.raise_for_status(); assert remote.json()['status']=='draft'
            unlocked=c.put(route,json={'version':d['version'],'title':d['title'],'body':d['body']+'\n검증용 수정 기록.'},headers=headers)
            assert unlocked.status_code==200, unlocked.text
            report['checks'].append('Actual future reservation → idempotency → edit lock → app cancellation rejected → simulated WP-admin cancellation on real local WP → read-only refresh → remote draft → unlock')
    report.update(passed=True, seconds=round(time.monotonic()-start,2))
    out=Path(__file__).resolve().parents[1]/'artifacts/live-v02-report.json'
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
