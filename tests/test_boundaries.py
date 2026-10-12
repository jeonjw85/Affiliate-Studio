"""Extra negative-path regressions; no live services or real credentials."""
import pytest
from test_jobs import ready
from test_core import product
from studio.core import Store


def test_wordpress_requires_explicit_revision_before_network(tmp_path):
    s,p,d=ready(tmp_path); s.approve(d['id'],1,True)
    class NoNetwork:
        url='https://blog.example.com'
        calls=0
        def create(self,*args):
            self.calls+=1
            return {'remote_id':1}
    wp=NoNetwork()
    with pytest.raises(ValueError,match='버전'):
        s.wordpress_draft(d['id'],None,True,wp)
    assert wp.calls==0


def test_edit_product_stale_revision_rejected(tmp_path):
    s,p,d=ready(tmp_path)
    changed=s.update_product(p['id'],dict(product(price=20000),version=1))
    with pytest.raises(ValueError,match='버전'):
        s.update_product(p['id'],dict(product(price=10000),version=1))
    assert s.state()['products'][0]['price']==changed['price']


def test_parallel_schedule_creates_one_job(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import timedelta
    from studio.core import now
    s,p,d=ready(tmp_path); s.approve(d['id'],1,True)
    due=(now()+timedelta(hours=1)).isoformat()
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids=list(pool.map(lambda _: Store(s.path).schedule(d['id'],1,due)['id'],range(8)))
    assert len(set(ids))==1


def test_wordpress_rejects_insecure_urls():
    from studio.wordpress import WordPress
    for url in ['http://example.com','https://example.com/?redirect=x','https://user:pw@example.com','https://127.0.0.1']:
        with pytest.raises(ValueError): WordPress(url,'fixture','fixture-password')


def test_disclosure_and_no_script_in_all_html_fields(tmp_path):
    from studio.exporting import render
    s,p,d=ready(tmp_path)
    p['facts']=['<img src=x onerror=alert(1)>']
    d['body']='<script>alert(1)</script>'
    d['title']='<svg onload=alert(1)>'
    html=render(d,p)['html']
    assert '<script>' not in html and '<img ' not in html and '<svg ' not in html
    assert 'rel="sponsored nofollow noreferrer"' in html
