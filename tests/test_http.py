import importlib, io, zipfile, time
from datetime import timedelta
from fastapi.testclient import TestClient
from studio.core import now
from test_core import product

def test_http_approval_download_and_scheduler(tmp_path):
    assert importlib.util.find_spec('studio.app') is not None, 'HTTP 경로 미구현'
    from studio.app import create_app
    with TestClient(create_app(tmp_path),base_url='http://127.0.0.1:8787') as c:
        state=c.get('/api/state').json()
        assert state['products']==[] and state['worker_running']
        headers={'Origin':'http://127.0.0.1:8787','X-CSRF-Token':state['csrf']}
        p=c.post('/api/products',json=product(),headers=headers).json()
        assert p['version']==1
        d=c.post('/api/drafts',json={'product_id':p['id']},headers=headers).json()
        assert c.get(f"/api/drafts/{d['id']}/download?version=1").status_code==400
        response=c.post(f"/api/drafts/{d['id']}/approve",json={'version':1,'confirmed':True},headers=headers)
        assert response.status_code==200
        with zipfile.ZipFile(io.BytesIO(c.get(f"/api/drafts/{d['id']}/download?version=1").content)) as z: assert z.testzip() is None
        j=c.post(f"/api/drafts/{d['id']}/schedule",json={'version':1,'run_at':(now()+timedelta(seconds=1)).isoformat()},headers=headers).json()
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            jobs=c.get('/api/state').json()['jobs']
            if jobs[0]['state']=='completed': break
            time.sleep(.1)
        assert jobs[0]['state']=='completed'
        assert c.get(f"/api/jobs/{j['id']}/download").status_code==200
        wp=c.post(f"/api/drafts/{d['id']}/wordpress",json={'version':1,'confirmed':True},headers=headers)
        assert wp.status_code==400

def test_http_download_requires_reviewed_revision_and_conflicts_are_409(tmp_path):
    from studio.app import create_app
    app=create_app(tmp_path);s=app.state.store
    p=s.add_product(product());d=s.create_draft(p['id']);s.approve(d['id'],1,True)
    with TestClient(app,base_url='http://127.0.0.1:8787') as c:
        token=c.get('/api/state').json()['csrf']
        assert c.get(f"/api/drafts/{d['id']}/download").status_code==422
        assert c.get(f"/api/drafts/{d['id']}/download?version=1").status_code==200
        s.edit_draft(d['id'],1,'변경된 제목','다른 탭에서 변경한 본문');s.approve(d['id'],2,True)
        assert c.get(f"/api/drafts/{d['id']}/download?version=1").status_code==409
        assert c.post(f"/api/drafts/{d['id']}/approve",json={'version':1,'confirmed':True},headers={'X-CSRF-Token':token}).status_code==409
        assert c.get(f"/api/drafts/{d['id']}/download?version=2").status_code==200


def test_http_blocks_cross_origin_and_missing_csrf(tmp_path):
    assert importlib.util.find_spec('studio.app') is not None
    from studio.app import create_app
    with TestClient(create_app(tmp_path),base_url='http://127.0.0.1:8787') as c:
        token=c.get('/api/state').json()['csrf']
        assert c.get('/api/state',headers={'Host':'attacker.example'}).status_code==403
        assert c.get('/api/state',headers={'Origin':'https://attacker.example'}).status_code==403
        assert c.post('/api/products',json=product()).status_code==403
        assert c.post('/api/products',json=product(),headers={'X-CSRF-Token':token,'Origin':'https://attacker.example'}).status_code==403
        assert c.post('/api/products',content='x'*140000,headers={'X-CSRF-Token':token,'Content-Type':'application/json','Origin':'http://127.0.0.1:8787'}).status_code==413
        assert c.get('/api/state').json()['products']==[]
