import pytest
from fastapi.testclient import TestClient
from studio.app import create_app
from test_core import product


@pytest.mark.parametrize('edge', ['0001-01-01T00:00:00+14:00', '9999-12-31T23:59:59-14:00'])
def test_timezone_overflow_is_client_error(tmp_path, edge):
    app=create_app(tmp_path); store=app.state.store
    p=store.add_product(product());d=store.create_draft(p['id']);store.approve(d['id'],1,True)
    with TestClient(app,base_url='http://127.0.0.1:8787',raise_server_exceptions=False) as c:
        token=c.get('/api/state').json()['csrf'];headers={'X-CSRF-Token':token}
        response=c.post('/api/products',json=product(observed_at=edge),headers=headers)
        assert response.status_code==400
        assert '날짜' in response.json()['error']
        response=c.post(f"/api/drafts/{d['id']}/schedule",json={'version':1,'run_at':edge},headers=headers)
        assert response.status_code==400
        assert c.get('/api/state').json()['jobs']==[]


@pytest.mark.parametrize('bad_id', [{}, []])
def test_invalid_product_identifier_is_client_error(tmp_path, bad_id):
    with TestClient(create_app(tmp_path),base_url='http://127.0.0.1:8787',raise_server_exceptions=False) as c:
        token=c.get('/api/state').json()['csrf']
        response=c.post('/api/drafts',json={'product_id':bad_id},headers={'X-CSRF-Token':token})
        assert response.status_code==400
        assert 'error' in response.json()
        assert c.get('/api/state').json()['drafts']==[]
