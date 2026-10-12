from datetime import timedelta
import pytest
from studio.core import now, Store
from test_jobs import ready


def test_format_change_is_a_new_revision_and_cancels_pending_job(tmp_path):
    s, p, d = ready(tmp_path)
    assert d.get('body_format') == 'markdown-v1'
    s.approve(d['id'], 1, True)
    s.schedule(d['id'], 1, (now()+timedelta(hours=1)).isoformat())
    changed = s.edit_draft(d['id'], 1, d['title'], d['body'], 'plain')
    assert changed['body_format'] == 'plain' and changed['version'] == 2
    assert changed['approved_at'] is None and changed['status'] == 'review'
    assert s.state()['jobs'][0]['state'] == 'cancelled'
    assert Store(s.path).detail(d['id'])['draft']['body_format'] == 'plain'


def test_format_api_rejects_invalid_values_and_respects_revision_and_lock(tmp_path):
    from fastapi.testclient import TestClient
    from studio.app import create_app
    app = create_app(tmp_path)
    s=app.state.store
    from test_core import product
    p=s.add_product(product()); d=s.create_draft(p['id'])
    with TestClient(app,base_url='http://localhost') as client:
        headers={'X-CSRF-Token':client.get('/api/state').json()['csrf']}
        path=f"/api/drafts/{d['id']}"
        payload=dict(version=1,title=d['title'],body=d['body'],body_format='plain')
        response=client.put(path,json=payload,headers=headers)
        assert response.status_code==200 and response.json()['body_format']=='plain'
        for mode in ['html','markdown-v2',True,{},[]]:
            r=client.put(path,json=dict(payload,version=2,body_format=mode),headers=headers)
            assert r.status_code==400
        assert client.put(path,json=payload,headers=headers).status_code==409
        with s.connect() as c:
            s.put(c,'publication',dict(id='lock',draft_id=d['id'],state='future'))
        assert client.put(path,json=dict(payload,version=2,body_format='markdown-v1'),headers=headers).status_code==409
        assert s.state()['drafts'][0]['body_format']=='plain'


def test_ai_prompt_selects_supported_format_and_apply_sets_mode(tmp_path):
    from studio.ai import LocalAI
    from test_ai import ai_server
    s,p,d=ready(tmp_path)
    with ai_server() as (url,remote):
        remote.answer={'title':'안내','body':'## 연결 확인\n\n**USB 연결**을 확인하세요.','evidence_refs':[1]}
        candidate=s.generate_ai(d['id'],1,'guide',True,LocalAI(url,'local-test'))
        prompt=remote.requests[-1][1]['messages'][0]['content']
        assert '##' in prompt and '**' in prompt and '표' in prompt
        assert '효과' in prompt and '편안' in prompt and '추론' in prompt
        assert candidate.get('body_format')=='markdown-v1'
        assert s.apply_candidate(d['id'],candidate['id'],1)['body_format']=='markdown-v1'


def test_new_template_has_section_but_does_not_change_legacy_candidate_format(tmp_path):
    s,p,d=ready(tmp_path)
    assert '## 구매 전 확인할 점' in d['body']
    with s.connect() as c:
        s.put(c,'ai_candidate',dict(id='old',draft_id=d['id'],source_version=1,product_version=1,title='이전 제목',body='## 그대로 표시'))
    applied=s.apply_candidate(d['id'],'old',1)
    assert applied['body_format']=='plain' and applied['version']==2


def test_same_formatted_html_in_preview_zip_and_wordpress_transport(tmp_path):
    import io, zipfile
    from test_publication import wp_server
    s,p,d=ready(tmp_path)
    body='## 연결 확인\n\n**USB 연결**을 확인하세요.\n\n| 항목 | 내용 |\n| --- | --- |\n| 연결 | USB |\n\n> 옵션을 확인하세요.'
    d=s.edit_draft(d['id'],1,d['title'],body,'markdown-v1')
    preview=s.detail(d['id'])['preview']
    assert '<h2>연결 확인</h2>' in preview['html']
    assert '<strong>USB 연결</strong>' in preview['html']
    assert '<table' in preview['html'] and '<blockquote>' in preview['html']
    assert '## 연결' not in preview['text'] and '**USB' not in preview['text']
    assert all(x in preview['text'] for x in ['연결 확인','항목','내용','USB','옵션'])
    assert preview['html'] in preview['document']
    assert "style-src 'sha256-" in preview['document']
    s.approve(d['id'],2,True)
    with zipfile.ZipFile(io.BytesIO(s.package(d['id'],2))) as z:
        assert z.read('article.html').decode()==preview['document']
    with wp_server() as (wp,remote):
        assert s.wordpress_draft(d['id'],2,True,wp)['state']=='completed'
        assert remote.post['content']==preview['html']
        pub=s.publish_schedule(d['id'],2,(now()+timedelta(minutes=15)).isoformat(),True,wp)
        assert pub['state']=='future'
        assert s.refresh_publication(pub['id'],wp)['state']=='future'


def test_preview_document_has_fixed_css_and_restrictive_csp():
    from studio.exporting import render
    from test_core import product
    result=render(dict(title='제목 <script>',body='본문'),product())
    doc=result.get('document','')
    assert '<meta name="viewport"' in doc
    assert "default-src 'none'" in doc and "style-src 'sha256-" in doc
    assert '<title>제목 &lt;script&gt;</title>' in doc
    assert result['html'] in doc


def test_legacy_render_is_byte_identical_to_archived_v03():
    from studio.exporting import render
    from legacy_exporting_v03 import render as legacy_render
    from test_core import product
    p=product()
    d=dict(title='기존 승인본',body='## 표기 그대로\n\n**특징** <img src=x> &문자\n다음줄')
    before=legacy_render(d,p);after=render(d,p)
    for key in ('html','text','telegram'):
        assert after[key]==before[key]
    assert render(dict(d,body_format='plain'),p)['html']==before['html']


@pytest.mark.parametrize('body,issue', [('**100**W 출력','수치'),('무**조건** 추천','단정')])
def test_formatting_cannot_hide_unsupported_claims_from_approval(tmp_path,body,issue):
    s,p,d=ready(tmp_path)
    d=s.edit_draft(d['id'],1,d['title'],body,'markdown-v1')
    with pytest.raises(ValueError,match=issue):
        s.approve(d['id'],2,True)
