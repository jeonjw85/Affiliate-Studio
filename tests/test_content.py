from studio.core import Store
from test_core import product
import pytest
from datetime import timedelta
from studio.core import now

def test_edit_and_approval_revision(tmp_path):
    s=Store(tmp_path/'data.db'); p=s.add_product(product()); d=s.create_draft(p['id'])
    assert hasattr(s,'approve'), '승인 미구현'
    with pytest.raises(ValueError): s.approve(d['id'],1,False)
    approved=s.approve(d['id'],1,True)
    assert approved['status']=='approved'
    changed=s.edit_draft(d['id'],1,'새 제목','검토한 새로운 구매 안내문입니다.')
    assert changed['status']=='review' and changed['version']==2
    with pytest.raises(ValueError,match='버전'): s.approve(d['id'],1,True)
    with pytest.raises(ValueError,match='버전'): s.edit_draft(d['id'],1,'예전 제목','덮어쓰기 금지')
    assert s.approve(d['id'],2,True)['status']=='approved'

@pytest.mark.parametrize('change',[{'observed_at':(now()-timedelta(days=2)).isoformat()},{'facts':['써보니 무조건 최저가 제품']}])
def test_risky_or_stale_blocked(tmp_path,change):
    s=Store(tmp_path/'data.db'); p=s.add_product(product(**change)); d=s.create_draft(p['id'])
    assert hasattr(s,'detail'), '검토 점검 미구현'
    assert s.detail(d['id'])['checks']['errors']
    with pytest.raises(ValueError): s.approve(d['id'],1,True)

def test_product_edit_invalidates_content(tmp_path):
    s=Store(tmp_path/'data.db'); p=s.add_product(product()); d=s.create_draft(p['id'])
    assert hasattr(s,'update_product'), '근거 재확인 미구현'
    s.approve(d['id'],1,True)
    q=s.update_product(p['id'],dict(product(price=45000),version=p['version']))
    assert q['version']==p['version']+1
    detail=s.detail(d['id'])
    assert detail['draft']['status']=='review' and detail['draft']['version']==2
    assert '45,000원' in detail['preview']['text']


def test_template_and_safe_render(tmp_path):
    s=Store(tmp_path/"data.db"); p=s.add_product(product(name="키보드 <script>alert(1)</script>"))
    assert hasattr(s,"create_draft"), "콘텐츠 생성 기능 미구현"
    d=s.create_draft(p["id"])
    from studio.exporting import render
    result=render(d,p)
    assert d["status"] == "review"
    assert result["html"].index("쿠팡 파트너스") < result["html"].index("<h1>")
    assert "<script>" not in result["html"]
    assert "유선 USB 연결" in result["html"]
    assert "49,000원" in result["text"]
    assert "직접 사용" not in result["text"]
    assert p["affiliate_url"] in result["telegram"]
    assert "템플릿" in result["prompt"]
    assert s.create_draft(p["id"])["id"] == d["id"]
