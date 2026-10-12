"""Regression from an actual local model inventing 100–150mm keyboard dimensions.
This is a limited quantity check, NOT semantic factual verification.
"""
from studio.core import Store, now


def evidence():
    return dict(provider='coupang',name='USB 키보드',source_url='https://example.com/keyboard',affiliate_url='https://example.com/ref',price=None,observed_at=now().isoformat(),facts=['USB 유선 연결','숫자 키패드 포함'],audience='문서 작성 사용자',caveats='크기와 호환 운영체제 미확인',ownership_confirmed=True)


def test_approval_checks_reject_dimensions_not_in_registered_evidence(tmp_path):
    s=Store(tmp_path/'s.db');p=s.add_product(evidence());d=s.create_draft(p['id'])
    d=s.edit_draft(d['id'],d['version'],d['title'],'이 키보드의 크기는 100~150mm입니다.')
    errors=s.checks(d,p)['errors']
    assert any('150' in e and '근거' in e for e in errors)


def test_quantity_check_accepts_registered_measurements_with_spaces(tmp_path):
    s=Store(tmp_path/'s.db');data=evidence();data['facts'].append('폭 150mm, 무게 200g')
    p=s.add_product(data);d=s.create_draft(p['id'])
    d=s.edit_draft(d['id'],d['version'],d['title'],'등록된 제품 폭은 150 mm이며 무게는 200 g입니다.')
    assert not any('수치' in e for e in s.checks(d,p)['errors'])
