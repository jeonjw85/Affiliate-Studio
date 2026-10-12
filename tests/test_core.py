from datetime import datetime, timezone
import importlib

def product(**changes):
    data = dict(provider="coupang", name="검증용 키보드", source_url="https://www.coupang.com/vp/products/123", affiliate_url="https://link.coupang.com/a/TEST_ONLY", price=49000, observed_at=datetime.now(timezone.utc).isoformat(), facts=["유선 USB 연결", "숫자 키패드 포함"], audience="책상에서 문서를 작성하는 사람", caveats="크기와 연결 방식을 구매 전에 확인", ownership_confirmed=True)
    return dict(data, **changes)

def test_product_persists(tmp_path):
    assert importlib.util.find_spec("studio.core") is not None, "상품 저장 기능이 아직 없음"
    from studio.core import Store
    s=Store(tmp_path / "data.db")
    p=s.add_product(product())
    assert Store(tmp_path / "data.db").state()["products"][0] == p
    assert p["facts"] == ["유선 USB 연결", "숫자 키패드 포함"]

import pytest

@pytest.mark.parametrize("change", [
    {"affiliate_url":"javascript:alert(1)"}, {"source_url":"https://a.invalid@coupang.com"},
    {"source_url":"https://127.0.0.1/private"}, {"price":True}, {"price":-1},
    {"price":"49,000"}, {"observed_at":"2099-01-01T00:00:00Z"},
    {"observed_at":"2026-01-01"}, {"facts":[]}, {"ownership_confirmed":False},
    {"name":" "}, {"provider":"unknown"}
])
def test_reject_invalid_product(tmp_path,change):
    from studio.core import Store
    with pytest.raises(ValueError): Store(tmp_path/"data.db").add_product(product(**change))

def test_duplicate_product_blocked(tmp_path):
    from studio.core import Store
    s=Store(tmp_path/"data.db"); s.add_product(product())
    with pytest.raises(ValueError,match="중복"): s.add_product(product())
