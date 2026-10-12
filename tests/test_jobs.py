import io, zipfile, json
from datetime import timedelta
import pytest
from studio.core import Store, now
from test_core import product

def ready(tmp_path):
    s=Store(tmp_path/'data.db'); p=s.add_product(product()); d=s.create_draft(p['id'])
    return s,p,d

def test_scheduled_package_durable_and_idempotent(tmp_path):
    s,p,d=ready(tmp_path)
    assert hasattr(s,'schedule'), '예약 미구현'
    due=(now()+timedelta(seconds=2)).isoformat()
    with pytest.raises(ValueError): s.schedule(d['id'],1,due)
    s.approve(d['id'],1,True)
    j=s.schedule(d['id'],1,due)
    assert s.schedule(d['id'],1,due)['id']==j['id']
    assert Store(s.path).state()['jobs'][0]['state']=='pending'
    assert s.run_due(tmp_path/'exports')==0
    assert s.run_due(tmp_path/'exports',at=now()+timedelta(seconds=4))==1
    job=s.state()['jobs'][0]
    assert job['state']=='completed'
    with zipfile.ZipFile(tmp_path/'exports'/job['artifact']) as z: assert z.testzip() is None
    assert s.run_due(tmp_path/'exports',at=now()+timedelta(seconds=4))==0

def test_edit_cancels_schedule(tmp_path):
    s,p,d=ready(tmp_path)
    assert hasattr(s,'schedule'), '예약 미구현'
    s.approve(d['id'],1,True)
    s.schedule(d['id'],1,(now()+timedelta(hours=1)).isoformat())
    s.edit_draft(d['id'],1,'새 제목','수정 본문')
    assert s.state()['jobs'][0]['state']=='cancelled'

def test_cancel_and_invalid_dates(tmp_path):
    s,p,d=ready(tmp_path)
    assert hasattr(s,'cancel_job'), '예약 취소 미구현'
    s.approve(d['id'],1,True)
    for due in ['2020-01-01T00:00:00Z','2099-01-01T00:00:00Z']:
        with pytest.raises(ValueError): s.schedule(d['id'],1,due)
    j=s.schedule(d['id'],1,(now()+timedelta(hours=1)).isoformat())
    assert s.cancel_job(j['id'])['state']=='cancelled'

def test_expired_price_blocks_existing_approval_and_job(tmp_path,monkeypatch):
    s,p,d=ready(tmp_path); s.approve(d['id'],1,True)
    s.schedule(d['id'],1,(now()+timedelta(seconds=1)).isoformat())
    future=now()+timedelta(days=2)
    monkeypatch.setattr('studio.core.now',lambda:future)
    with pytest.raises(ValueError,match='24시간'): s.package(d['id'])
    assert s.run_due(tmp_path/'exports')==1
    assert s.state()['jobs'][0]['state']=='failed'
    assert not list((tmp_path/'exports').glob('*.zip'))

def test_schedule_requires_explicit_revision(tmp_path):
    s,p,d=ready(tmp_path); s.approve(d['id'],1,True)
    with pytest.raises(ValueError,match='버전'):
        s.schedule(d['id'],None,(now()+timedelta(hours=1)).isoformat())

def test_approval_required_for_real_zip(tmp_path):
    s,p,d=ready(tmp_path)
    assert hasattr(s,'package'), 'ZIP 내보내기 미구현'
    with pytest.raises(ValueError,match='승인'): s.package(d['id'])
    s.approve(d['id'],1,True)
    raw=s.package(d['id'])
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        assert z.testzip() is None
        assert 'article.html' in z.namelist()
        assert '쿠팡 파트너스' in z.read('article.html').decode()
        assert json.loads(z.read('evidence.json'))['product']['affiliate_url']==p['affiliate_url']
    s.edit_draft(d['id'],1,'제목','수정 본문')
    with pytest.raises(ValueError): s.package(d['id'])
