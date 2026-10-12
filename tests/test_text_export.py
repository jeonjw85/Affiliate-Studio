from fastapi.testclient import TestClient
from studio.app import create_app
from studio.exporting import DISCLOSURES, render
from test_core import product


def test_approved_text_preserves_disclosure_links_and_exact_content(tmp_path):
    app = create_app(tmp_path)
    store = app.state.store
    p = store.add_product(product())
    d = store.create_draft(p['id'])
    store.approve(d['id'], d['version'], True)
    with TestClient(app, base_url='http://127.0.0.1:8787') as c:
        r = c.get(f"/api/drafts/{d['id']}/text?version=1")
        assert r.status_code == 200
        assert r.headers['content-type'].startswith('text/plain')
        assert '.txt' in r.headers['content-disposition']
        assert r.text == render(d, p)['text']
        assert r.text.startswith(DISCLOSURES[p['provider']])
        assert p['affiliate_url'] in r.text
        assert p['source_url'] in r.text


def test_text_requires_approval_and_current_explicit_version(tmp_path):
    app = create_app(tmp_path)
    store = app.state.store
    p = store.add_product(product())
    d = store.create_draft(p['id'])
    with TestClient(app, base_url='http://127.0.0.1:8787') as c:
        url = f"/api/drafts/{d['id']}/text"
        assert c.get(url+'?version=1').status_code == 400
        store.approve(d['id'],1,True)
        assert c.get(url).status_code == 422
        store.edit_draft(d['id'],1,'변경된 제목','수정 내용')
        store.approve(d['id'],2,True)
        assert c.get(url+'?version=1').status_code == 409
        assert c.get(url+'?version=2').status_code == 200
        assert c.get(url+'?version=2',headers={'Origin':'https://other.example'}).status_code == 403
