import json
import os
import pytest
from fastapi.testclient import TestClient
from studio.ai import LocalAI
from studio.core import Store, now
from studio.app import create_app
from test_jobs import ready


def test_bounded_schema_and_rewrite_instruction(tmp_path):
    s,p,d=ready(tmp_path);ai=LocalAI('http://127.0.0.1:11434/v1','local-test');seen=[]
    def reply(method,path,payload):
        seen.append(payload)
        return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'title':'새 안내','body':'USB 연결 조건을 확인하세요.','evidence_refs':[1]})}}]}
    ai.request=reply
    ai.generate(ai.source(d,p,'guide'),len(p['facts']))
    f=seen[0]['response_format'];assert f['type']=='json_schema'
    schema=f['json_schema']['schema'];assert schema['properties']['evidence_refs']['maxItems']==len(p['facts'])
    assert schema['properties']['body']['maxLength']<=600
    assert '복사' in seen[0]['messages'][0]['content']


@pytest.mark.parametrize('model',['qwen3:cloud','qwen3:480b-cloud','custom-cloud:latest'])
def test_cloud_model_tags_are_not_local_inference(model):
    with pytest.raises(ValueError): LocalAI('http://localhost:11434/v1',model)


def test_finish_reason_length_is_not_a_completed_candidate():
    ai=LocalAI('http://127.0.0.1:11434/v1','local-test')
    ai.request=lambda *args:{'choices':[{'finish_reason':'length','message':{'content':json.dumps({'title':'제목','body':'본문','evidence_refs':[1]})}}]}
    with pytest.raises(ValueError): ai.generate('{}',1)


@pytest.mark.parametrize('kind,state',[('operation','running'),('delivery','sending'),('publication','sending')])
def test_dead_process_work_is_recovered_without_network(tmp_path,monkeypatch,kind,state):
    s,p,d=ready(tmp_path)
    record=dict(id='interrupted',draft_id=d['id'],version=d['version'],source_version=d['version'],product_version=p['version'],type='ai',state=state,created_at=now().isoformat(),_pid=999999999)
    with s.connect() as c:s.put(c,kind,record)
    monkeypatch.setattr('studio.publication.owner_alive',lambda record:False)
    again=Store(s.path)
    with again.connect() as c: recovered=again.get(c,record['id'],kind)
    assert recovered['state']==('failed' if kind=='operation' else 'uncertain')
    assert again.detail(d['id'])['publication_lock'] is (kind=='publication')


def test_live_process_work_is_not_recovered(tmp_path):
    s,p,d=ready(tmp_path)
    with s.connect() as c:s.put(c,'delivery',dict(id='live',draft_id=d['id'],version=d['version'],state='sending',_pid=os.getpid()))
    again=Store(s.path)
    assert again.detail(d['id'])['publication_lock']


def test_confirmation_can_show_exact_wordpress_destination(tmp_path,monkeypatch):
    for key,value in {'WP_URL':'https://example.com/blog','WP_USERNAME':'fixture','WP_APP_PASSWORD':'fixture-only'}.items():monkeypatch.setenv(key,value)
    with TestClient(create_app(tmp_path),base_url='http://127.0.0.1') as c:
        assert c.get('/api/state').json()['connections']['wordpress_url']=='https://example.com/blog'
