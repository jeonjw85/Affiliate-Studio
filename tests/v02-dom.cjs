const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const html = fs.readFileSync(__dirname+'/../studio/static/index.html','utf8');
const js = fs.readFileSync(__dirname+'/../studio/static/app.js','utf8');
const tick = () => new Promise(r=>setTimeout(r,10));
async function setup() {
  const product={id:1,version:1,name:'샘플',provider:'coupang',facts:['확인된 사실'],source_url:'https://example.test',affiliate_url:'https://example.test/a',observed_at:new Date().toISOString(),price:null};
  const draft={id:1,product_id:1,version:1,title:'검토 제목',body:'기존 본문',status:'approved'};
  const state={csrf:'test-csrf',products:[product],drafts:[draft],jobs:[{id:7,draft_id:1,version:1,kind:'wordpress_draft',state:'completed',target:'https://wp.example.test'}],publications:[],connections:{wordpress:true,wordpress_url:'https://wp.example.test',ai:true,ai_model:'local-test'}};
  const detail={draft,product,candidates:[],publication_lock:false,checks:{errors:[],warnings:[]},preview:{html:'<p>미리보기</p>'}};
  const requests=[];
  const dom=new JSDOM(html,{url:'http://localhost:8090',runScripts:'outside-only'});
  const w=dom.window; const $=id=>w.document.getElementById(id); const confirmations=[];
  w.confirm=text=>{confirmations.push(text);return true;};
  // JSDOM has no native editing/undo engine; E2E verifies real browser history.
  w.document.execCommand=(command,_ui,text)=>{
    assert.equal(command,'insertText');const input=w.document.activeElement;
    input.setRangeText(text,input.selectionStart,input.selectionEnd,'end');
    input.dispatchEvent(new w.Event('input',{bubbles:true}));return true;
  };
  let custom=()=>undefined;
  w.fetch=async(url,options={})=>{
    const req={url,method:options.method||'GET',body:options.body?JSON.parse(options.body):null}; requests.push(req);
    let payload=custom(req);
    if(payload===undefined){if(url==='/api/state')payload=state;else if(url==='/api/drafts/1')payload=detail;else if(url==='/api/ai/check')payload={available:true,model:'local-test',models:['local-test']};else throw Error('Unexpected request: '+url);}
    return {ok:true,status:200,json:async()=>JSON.parse(JSON.stringify(payload))};
  };
  w.eval(js); await tick();
  w.document.querySelector('[data-select-draft]').click(); await tick();
  return {w,$,state,detail,requests,confirmations,tick,route:fn=>{custom=fn;},close:()=>w.close()};
}


test('evidence table inserts after the selected line without replacing reviewed text or numbers',async()=>{
  const h=await setup();try{
    const mode=h.$('body-format');mode.value='markdown-v1';mode.dispatchEvent(new h.w.Event('change'));
    const body=h.$('draft-body'),button=h.w.document.querySelector('[data-format="table"]');
    const source='지우면 안 되는 직접 검토 문장 001.20\n다음 단락 007.10';
    for(const [start,end] of [[0,source.length],[3,8],[6,6],[0,0]]){
      body.value=source;body.setSelectionRange(start,end);button.click();
      const table=body.value.match(/\n\n\| 항목 \| 확인한 내용 \|[\s\S]*?\n\n/);
      assert.ok(table,'inserted evidence table');assert.equal(body.value.replace(table[0],''),source,'all original prose must survive');
      assert.ok(body.value.includes('지우면 안 되는 직접 검토 문장 001.20'),'do not split the original line');
      assert.equal(h.$('download').disabled,true);
    }
    body.value='x'.repeat(19999);body.setSelectionRange(4,40);button.click();
    assert.equal(body.value,'x'.repeat(19999),'oversized insertion is rejected without replacing selection');
  }finally{h.close();}
});


test('heading tools replace existing heading prefixes without accumulating or losing values',async()=>{
  const h=await setup();try{
    h.$('body-format').value='markdown-v1';h.$('body-format').dispatchEvent(new h.w.Event('change'));
    const body=h.$('draft-body'),h2=h.w.document.querySelector('[data-format="h2"]'),h3=h.w.document.querySelector('[data-format="h3"]');
    body.value='첫 줄 001.20\n둘째 줄 007.10';body.setSelectionRange(0,0);h2.click();
    assert.equal(body.value,'## 첫 줄 001.20\n둘째 줄 007.10');h3.click();
    assert.equal(body.value,'### 첫 줄 001.20\n둘째 줄 007.10');h3.click();
    assert.equal(body.value,'### 첫 줄 001.20\n둘째 줄 007.10');h2.click();
    assert.equal(body.value,'## 첫 줄 001.20\n둘째 줄 007.10');
    body.value='### ## 이미 중첩된 제목 001.20';body.setSelectionRange(0,0);h3.click();
    assert.equal(body.value,'### 이미 중첩된 제목 001.20');
    body.value='\\## 문자 그대로';body.setSelectionRange(0,0);h2.click();assert.equal(body.value,'## \\## 문자 그대로');
  }finally{h.close();}
});


test('unsupported native editing leaves original text and selection untouched',async()=>{
  const h=await setup();try{
    h.$('body-format').value='markdown-v1';h.$('body-format').dispatchEvent(new h.w.Event('change'));
    const body=h.$('draft-body');
    for(const exec of [undefined,()=>false,()=>{throw Error('not supported');}]){
      h.w.document.execCommand=exec;body.value='원문 001.20';body.setSelectionRange(1,4,'backward');
      h.w.document.querySelector('[data-format="table"]').click();
      assert.equal(body.value,'원문 001.20');assert.equal(body.selectionStart,1);assert.equal(body.selectionEnd,4);assert.equal(body.selectionDirection,'backward');
      assert.equal(h.requests.some(r=>r.method==='PUT'),false);
    }
  }finally{h.close();}
});

test('format selection and tools preserve approval gates, selected text, and remote locks',async()=>{
  const h=await setup();try{
    const mode=h.$('body-format');assert.ok(mode,'format selector');
    assert.equal(mode.value,'plain');
    assert.equal(h.$('download').disabled,false);
    const bold=h.w.document.querySelector('[data-format="bold"]');assert.equal(bold.disabled,true);
    mode.value='markdown-v1';mode.dispatchEvent(new h.w.Event('change'));
    assert.equal(h.$('download').disabled,true);assert.equal(h.$('save-draft').disabled,false);
    const body=h.$('draft-body');body.value='확인한 조건';body.setSelectionRange(0,3);bold.click();
    assert.equal(body.value,'**확인한** 조건');
    body.value='\n본문';body.setSelectionRange(0,0);h.w.document.querySelector('[data-format="h2"]').click();
    assert.equal(body.value,'## \n본문','first empty line does not erase the next paragraph');
    h.route(r=>{if(r.method==='PUT'){assert.equal(r.body.body_format,'markdown-v1');h.detail.draft={...h.detail.draft,...r.body,version:2,status:'review'};return h.detail.draft;}});
    h.$('draft-form').dispatchEvent(new h.w.Event('submit',{cancelable:true}));await tick();
    assert.equal(h.$('body-format').value,'markdown-v1');
    assert.equal(h.$('download').disabled,true);
    h.detail.publication_lock=true;h.$('refresh').click();await tick();
    const before=body.value;bold.click();assert.equal(body.value,before);assert.equal(bold.disabled,true);
  }finally{h.close();}
});

test('approved text download uses the same approval and dirty gates as ZIP',async()=>{
  const h=await setup();try{
    assert.ok(h.$('download-text'),'approved text action is available');
    assert.equal(h.$('download-text').disabled,false);
    h.$('draft-body').value='아직 저장 안 함';h.$('draft-body').dispatchEvent(new h.w.Event('input'));
    assert.equal(h.$('download-text').disabled,true);
    h.$('draft-body').value='기존 본문';h.$('draft-body').dispatchEvent(new h.w.Event('input'));
    h.detail.publication_lock=true;h.$('refresh').click();await tick();
    assert.equal(h.$('download-text').disabled,true);
  }finally{h.close();}
});

test('refresh renders fresh review errors and badge without losing unsaved editor',async()=>{
  const h=await setup();try{
    h.$('draft-body').value='저장하지 않은 글';h.$('draft-body').dispatchEvent(new h.w.Event('input'));
    h.detail.checks={errors:['가격 정보가 24시간 지났습니다.'],warnings:['재확인 필요']};
    h.detail.draft.status='review';
    h.$('refresh').click();await tick();
    assert.match(h.$('checks').textContent,/24시간/);
    assert.equal(h.$('draft-status').textContent,'검토 필요');
    assert.equal(h.$('draft-body').value,'저장하지 않은 글');
    assert.equal(h.$('download').disabled,true);
  }finally{h.close();}
});

test('explicit AI connection/generation, safe comparison and apply require new review',async()=>{
  const h=await setup(); try {
    assert.ok(h.$('ai-check'),'explicit local AI check control is present');
    assert.match(h.$('ai-status').textContent,/미확인/);
    assert.equal(h.requests.some(r=>r.url==='/api/ai/check'),false);
    h.$('ai-check').click(); await tick();
    assert.match(h.$('ai-status').textContent,/연결 확인/);
    assert.equal(h.$('ai-generate').disabled,true);
    h.$('confirmed-send').checked=true; h.$('confirmed-send').dispatchEvent(new h.w.Event('change'));
    h.route(r=>{if(r.url.endsWith('/ai')){const c={id:3,draft_id:1,source_version:1,product_version:1,title:'<img src=x onerror=alert(1)>',body:'AI 후보',evidence_refs:[1],warnings:['사실 확인 필요'],model:'local-test'};h.detail.candidates=[c];return c;}if(r.url.endsWith('/apply')){h.detail.draft={...h.detail.draft,version:2,status:'review',body:'AI 후보'};return h.detail.draft;}});
    h.$('ai-generate').click(); await tick();
    assert.equal(h.$('draft-body').value,'기존 본문');
    assert.equal(h.$('candidate-list').querySelector('img'),null);
    assert.match(h.$('candidate-list').textContent,/<img src=x/);
    assert.equal(h.requests.find(r=>r.url.endsWith('/ai')).body.confirmed_send,true);
    h.w.document.querySelector('[data-apply-candidate]').click(); await tick();
    assert.equal(h.$('draft-body').value,'AI 후보');
    assert.equal(h.$('wordpress').disabled,true);
    assert.equal(h.$('approval-confirm').checked,false);
  } finally {h.close();}
});
test('public schedule requires explicit consent and content/site/time confirmation; HTTP 200 uncertain is not success',async()=>{
  const h=await setup();try{
    assert.ok(h.$('publish-schedule'),'separate public scheduling action exists');
    assert.equal(h.$('publish-schedule').disabled,true);
    h.$('publish-run-at').value='2026-10-11T12:00';
    // Freeze current time only for deterministic future-date validation.
    h.w.Date.now=()=>new Date('2026-10-11T00:00Z').getTime();
    h.$('publish-confirm').checked=true;h.$('publish-confirm').dispatchEvent(new h.w.Event('change'));
    h.route(r=>{if(r.url.endsWith('/publish-schedule')){const p={id:8,draft_id:1,version:1,target:'https://wp.example.test',state:'uncertain',error:'읽기 확인 실패',run_at:r.body.run_at};h.state.publications=[p];return p;}});
    h.$('publish-form').dispatchEvent(new h.w.Event('submit',{cancelable:true}));await tick();
    const req=h.requests.find(r=>r.url.endsWith('/publish-schedule'));
    assert.equal(req.body.confirmed_publish,true);
    assert.match(h.confirmations.at(-1),/검토 제목/);assert.match(h.confirmations.at(-1),/wp.example.test/);assert.match(h.confirmations.at(-1),/UTC/);
    assert.ok(h.$('notice').classList.contains('error'));assert.match(h.$('notice').textContent,/읽기 확인 실패/);
    assert.match(h.$('publication-list').textContent,/결과 불명/);
    assert.equal(h.$('wordpress').disabled,true);assert.equal(h.$('draft-fields').disabled,true);
    assert.equal(h.w.document.querySelector('[data-edit-product]').disabled,true);
    assert.equal(h.w.document.querySelector('[data-cancel-publication]'),null);
  }finally{h.close();}
});
test('publication readback preserves dirty editor and confirms manual administrator cancellation',async()=>{
  const h=await setup();try{
    h.state.publications=[{id:8,draft_id:1,version:1,target:'https://wp.example.test/blog',remote_id:42,state:'future',run_at:'2026-10-12T00:00Z',remote_url:'javascript:alert(1)',checked_at:'2026-10-11T00:00Z'}];
    h.$('draft-body').value='유지할 수정본';h.$('draft-body').dispatchEvent(new h.w.Event('input'));
    h.$('refresh').click();await tick();
    assert.ok(h.$('publication-list'),'WordPress operations list exists');
    const admin=h.$('publication-list').querySelector('[data-manage-publication]');
    assert.equal(admin?.href,'https://wp.example.test/blog/wp-admin/post.php?post=42&action=edit');
    assert.equal(admin.textContent,'WordPress에서 예약 관리');
    assert.equal(admin.rel,'noopener noreferrer');
    assert.equal(h.$('publication-list').querySelectorAll('a').length,1);
    assert.match(h.$('publication-list').textContent,/초안.*원격 상태 조회/);
    h.route(r=>{if(r.url.endsWith('/refresh'))return h.state.publications[0];});
    h.w.document.querySelector('[data-refresh-publication]').click();await tick();
    assert.equal(h.$('draft-body').value,'유지할 수정본');
    h.state.publications[0].state='cancelled'; // Administrator changed the remote post to draft.
    h.w.document.querySelector('[data-refresh-publication]').click();await tick();
    assert.equal(h.requests.some(r=>r.url.endsWith('/cancel')),false);
    assert.match(h.$('publication-list').textContent,/취소 확인/);
    assert.equal(h.$('draft-body').value,'유지할 수정본');
    assert.equal(h.$('draft-fields').disabled,false);
    assert.equal(h.$('publish-schedule').disabled,true);
    assert.match(h.$('publication-list').textContent,/동일 버전.*재예약.*불가/);
    h.$('draft-body').value='기존 본문';h.$('draft-body').dispatchEvent(new h.w.Event('input'));
    h.$('publish-confirm').checked=true;h.$('publish-confirm').dispatchEvent(new h.w.Event('change'));
    assert.equal(h.$('publish-schedule').disabled,true,'unchanged approved version cannot reschedule');
  }finally{h.close();}
});
test('stale version and old candidate remain disabled after generic action sync',async()=>{
  const h=await setup();try{
    h.detail.candidates=[{id:4,source_version:0,product_version:1,title:'오래됨',body:'본문'}];
    h.$('reload-draft').click();await tick();
    assert.equal(h.w.document.querySelector('[data-apply-candidate]').disabled,true);
    h.$('draft-body').value='저장하지 않은 내용';h.$('draft-body').dispatchEvent(new h.w.Event('input'));
    h.state.drafts=[{...h.detail.draft,version:2}];h.$('refresh').click();await tick();
    assert.equal(h.$('draft-body').value,'저장하지 않은 내용');
    assert.equal(h.$('save-draft').disabled,true);assert.equal(h.$('wordpress').disabled,true);
    assert.equal(h.$('preview-frame').getAttribute('sandbox'),'');
  }finally{h.close();}
});
test('uncertain WordPress draft send re-reads lock before enabling editing',async()=>{
  const h=await setup();try{
    h.route(r=>{if(r.url.endsWith('/wordpress')){h.detail.publication_lock=true;return {state:'uncertain',error:'전송 불명'};}});
    h.$('wordpress').click();await tick();
    assert.match(h.$('notice').textContent,/전송 불명/);
    assert.equal(h.$('draft-fields').disabled,true);
    assert.equal(h.$('wordpress').disabled,true);
  }finally{h.close();}
});
test('a failed explicit AI recheck invalidates prior available connection',async()=>{
  const h=await setup();try{
    h.$('ai-check').click();await tick();
    h.$('confirmed-send').checked=true;h.$('confirmed-send').dispatchEvent(new h.w.Event('change'));
    assert.equal(h.$('ai-generate').disabled,false);
    h.route(r=>{if(r.url==='/api/ai/check')throw Error('offline');});
    h.$('ai-check').click();await tick();
    assert.equal(h.$('ai-generate').disabled,true);
    assert.doesNotMatch(h.$('ai-status').textContent,/연결 확인됨/);
  }finally{h.close();}
});
test('busy gate prevents repeated remote calls and confirmation cancellation makes no POST',async()=>{
  const h=await setup();try{
    h.w.confirm=()=>false;h.$('wordpress').click();await tick();
    assert.equal(h.requests.some(r=>r.url.endsWith('/wordpress')),false);
    let resolve;const original=h.w.fetch;
    h.w.fetch=(url,options)=>url==='/api/ai/check'?new Promise(r=>{resolve=r;}):original(url,options);
    h.$('ai-check').click();h.$('ai-check').click();
    assert.equal(h.$('wordpress').disabled,true);
    assert.equal(h.$('draft-fields').disabled,true);
    assert.equal(h.w.document.querySelector('[data-edit-product]').disabled,true);
    resolve({ok:true,json:async()=>({available:true,model:'local-test'})});await tick();
    assert.equal(h.$('draft-fields').disabled,false);
  }finally{h.close();}
});
test('interrupted sending/uncertain UI exposes explicit readback without a cancellation mutation',async()=>{
  const h=await setup();try{
    for(const state of ['sending','uncertain']){
      h.state.publications=[{id:8,draft_id:1,version:1,target:'https://wp.example.test',remote_id:42,state}];
      h.$('refresh').click();await tick();
      const refresh=h.w.document.querySelector('[data-refresh-publication]');
      assert.equal(refresh.disabled,false);
      assert.equal(h.w.document.querySelector('[data-cancel-publication]'),null);
      if(state==='uncertain')assert.ok(h.w.document.querySelector('[data-manage-publication]'));
      h.route(r=>{if(r.url.endsWith('/refresh')){h.state.publications[0].state='future';return h.state.publications[0];}});
      const count=h.requests.filter(r=>r.url.endsWith('/refresh')).length;
      refresh.click();await tick();
      assert.equal(h.requests.filter(r=>r.url.endsWith('/refresh')).length,count+1);
      assert.match(h.$('publication-list').textContent,/예약 확인됨/);
      assert.equal(h.$('draft-fields').disabled,true);
    }
    assert.equal(h.requests.some(r=>r.url.endsWith('/cancel')||r.url.endsWith('/publish-schedule')),false);
  }finally{h.close();}
});
test('administrator links reject unsafe targets and non-positive/non-integer remote IDs',async()=>{
  const h=await setup();try{
    const bad=[['javascript:alert(1)',42],['https://u:p@wp.example.test',42],['https://wp.example.test/?x=1',42],['https://wp.example.test/#x',42],['https://wp.example.test',0],['https://wp.example.test',-1],['https://wp.example.test',1.5],['https://wp.example.test','42'],['https://wp.example.test',true]];
    for(const [target,remote_id] of bad){
      h.state.publications=[{id:8,draft_id:1,version:1,state:'uncertain',target,remote_id}];
      h.$('refresh').click();await tick();
      assert.equal(h.w.document.querySelector('[data-manage-publication]'),null);
      assert.equal(h.w.document.querySelector('[data-refresh-publication]').disabled,false);
    }
  }finally{h.close();}
});
module.exports={setup,tick};
