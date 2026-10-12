const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('jsdom');
const tick=()=>new Promise(r=>setTimeout(r,15));
async function setup(){
  const html=fs.readFileSync(__dirname+'/../studio/static/index.html','utf8');
  const file=__dirname+'/../studio/static/measurements.js';
  const dom=new JSDOM(html,{url:'http://localhost:8787',runScripts:'outside-only'});
  const w=dom.window,$=id=>w.document.getElementById(id),requests=[];
  let items=[];let failUpdate=false;
  const draft={id:'draft-1',title:'검증용 글',version:1};
  w.confirm=()=>true;
  w.fetch=async(url,opts={})=>{
    const method=opts.method||'GET',body=opts.body?JSON.parse(opts.body):null;
    requests.push({url,method,body,headers:opts.headers});
    if(url==='/api/state')return {ok:true,json:async()=>({csrf:'test-only-token',drafts:[draft]})};
    if(method==='GET')return {ok:true,json:async()=>({items:structuredClone(items)})};
    if(method==='PUT'&&failUpdate)return {ok:false,status:409,json:async()=>({error:'버전이 변경됐습니다.'})};
    if(method==='DELETE'){items=[];return {ok:true,json:async()=>({deleted:true})};}
    const r={...body,id:'metric-1',version:method==='PUT'?2:1,draft_title:draft.title,draft_version:1,rates:{click_rate:null,order_rate:null,epc_won:null,balance_won:null},warnings:[]};
    items=[r];return {ok:true,json:async()=>r};
  };
  if(fs.existsSync(file))w.eval(fs.readFileSync(file,'utf8'));
  return {w,$,requests,tick,close:()=>w.close(),conflict:()=>{failUpdate=true;},setItems:x=>{items=x;}};
}
async function open(h){const nav=h.w.document.querySelector('nav [data-view="metrics"]');assert.ok(nav,'성과 메뉴 제공');nav.click();await tick();}
function fill(h){const f=h.$('metrics-form');for(const [k,v] of Object.entries({draft_id:'draft-1',channel:'naver',page_url:'https://example.com/article',period_start:'2026-10-01',period_end:'2026-10-02',source:'직접 확인한 보고서 / KST',views:'0',clicks:'',orders:'',revenue_won:'',cost_won:''}))f.elements.namedItem(k).value=v;f.dispatchEvent(new h.w.Event('input',{bubbles:true}));}

test('money grammar rejects exponent and excess decimals before POST and clears on reset',async()=>{
  const h=await setup();try{
    await open(h);fill(h);const form=h.$('metrics-form'),amount=form.elements.revenue_won;
    for(const value of ['1e3','1.230']){
      amount.value=value;form.dispatchEvent(new h.w.Event('input',{bubbles:true}));
      assert.equal(amount.checkValidity(),false,'must match server amount grammar: '+value);
      form.dispatchEvent(new h.w.Event('submit',{bubbles:true,cancelable:true}));await tick();
      assert.equal(h.requests.some(c=>c.method==='POST'),false);assert.equal(amount.value,value);
    }
    amount.value='1.23';form.dispatchEvent(new h.w.Event('input',{bubbles:true}));assert.equal(amount.checkValidity(),true);
    amount.value='1e3';form.dispatchEvent(new h.w.Event('input',{bubbles:true}));
    h.w.confirm=()=>true;h.$('metrics-new').click();assert.equal(amount.value,'');assert.equal(amount.validationMessage,'');
  }finally{h.close();}
});

test('quick add reaches the form but never discards dirty values without consent',async()=>{
  const h=await setup();try{
    await open(h);fill(h);
    const quick=h.$('metrics-add');assert.ok(quick,'목록에서 기록 추가로 바로 이동');
    h.w.confirm=()=>false;quick.click();assert.equal(h.$('metrics-form').elements.source.value,'직접 확인한 보고서 / KST');
    h.w.confirm=()=>true;quick.click();assert.equal(h.$('metrics-form').elements.source.value,'');
  }finally{h.close();}
});

test('CSV action downloads all records rather than silently using the screen filter',async()=>{
  const h=await setup();try{
    await open(h);fill(h);h.$('metrics-form').dispatchEvent(new h.w.Event('submit',{cancelable:true}));await tick();
    const original=h.w.fetch;let downloaded=false;
    h.w.fetch=async(url,opts)=>url==='/api/measurements/export'?{ok:true,blob:async()=>new h.w.Blob(['CSV'])}:original(url,opts);
    h.w.URL.createObjectURL=()=> 'blob:test-csv';h.w.URL.revokeObjectURL=()=>{};
    h.w.HTMLAnchorElement.prototype.click=function(){downloaded=this.download==='measurements.csv';};
    h.$('metrics-filter').value='wordpress';h.$('metrics-filter').dispatchEvent(new h.w.Event('change'));
    h.$('metrics-export').click();await tick();
    assert.equal(downloaded,true);
    assert.match(h.$('metrics-notice').textContent,/전체/);
  }finally{h.close();}
});

test('delete is explicit, versioned, and does not remove linked content',async()=>{
  const h=await setup();try{
    await open(h);fill(h);h.$('metrics-form').dispatchEvent(new h.w.Event('submit',{cancelable:true}));await tick();
    const del=h.$('metrics-list').querySelector('[data-delete-measurement]');assert.ok(del,'삭제 제어');
    h.w.confirm=()=>false;del.click();await tick();assert.equal(h.requests.some(x=>x.method==='DELETE'),false);
    h.w.confirm=()=>true;del.click();await tick();
    const req=h.requests.find(x=>x.method==='DELETE');assert.equal(req.body.version,1);
    assert.equal(req.url,'/api/measurements/metric-1');
    assert.equal(h.requests.some(x=>x.method!=='GET'&&x.url.startsWith('/api/drafts')),false);
    assert.equal(h.$('metrics-list').querySelectorAll('[data-delete-measurement]').length,0);
  }finally{h.close();}
});

test('edit sends reviewed record version and conflict preserves unsaved form',async()=>{
  const h=await setup();try{
    await open(h);fill(h);h.$('metrics-form').dispatchEvent(new h.w.Event('submit',{cancelable:true}));await tick();
    const edit=h.$('metrics-list').querySelector('[data-edit-measurement]');assert.ok(edit,'수정 진입');edit.click();
    assert.equal(h.$('metrics-draft').disabled,true);
    h.$('metrics-form').elements.note.value='유지할 수정';h.$('metrics-form').dispatchEvent(new h.w.Event('input'));
    h.conflict();h.$('metrics-form').dispatchEvent(new h.w.Event('submit',{cancelable:true}));await tick();
    assert.equal(h.requests.find(x=>x.method==='PUT').body.version,1);
    assert.equal(h.$('metrics-form').elements.note.value,'유지할 수정');
    assert.match(h.$('metrics-notice').textContent,/버전/);
    h.$('metrics-refresh').click();await tick();
    assert.equal(h.$('metrics-form').elements.note.value,'유지할 수정');
  }finally{h.close();}
});

test('unsafe text is inert and filter changes display only, not saved records',async()=>{
  const h=await setup();try{
    const row={id:'metric-2',version:1,draft_id:'draft-1',draft_version:1,draft_title:'<img src=x onerror=alert(1)>',channel:'other',page_url:'https://example.com/a',period_start:'2026-10-01',period_end:'2026-10-02',source:'<script>alert(1)</script>',views:0,clicks:0,orders:0,revenue_won:'0.00',cost_won:null,note:'시험',rates:{click_rate:null,order_rate:null,epc_won:null,balance_won:null},warnings:[]};
    h.setItems([row]);await open(h);
    assert.equal(h.$('metrics-list').querySelectorAll('img,script').length,0);
    assert.ok(h.$('metrics-list').textContent.includes('<script>'));
    h.$('metrics-filter').value='wordpress';h.$('metrics-filter').dispatchEvent(new h.w.Event('change'));
    assert.equal(h.$('metrics-list').querySelectorAll('[data-edit-measurement]').length,0);
    h.$('metrics-filter').value='other';h.$('metrics-filter').dispatchEvent(new h.w.Event('change'));
    assert.equal(h.$('metrics-list').querySelectorAll('[data-edit-measurement]').length,1);
    assert.equal(h.requests.filter(x=>x.method!=='GET').length,0);
  }finally{h.close();}
});

test('manual metrics preserve unknown versus zero and are channel independent',async()=>{
  const h=await setup();try{
    await open(h);fill(h);
    h.$('metrics-form').dispatchEvent(new h.w.Event('submit',{cancelable:true}));await tick();
    const req=h.requests.find(x=>x.method==='POST');assert.ok(req);
    assert.equal(req.url,'/api/measurements');assert.equal(req.body.channel,'naver');
    assert.equal(req.body.views,0);assert.equal(req.body.clicks,null);assert.equal(req.body.revenue_won,null);
    assert.equal(req.headers['X-CSRF-Token'],'test-only-token');
    assert.match(h.$('metrics-list').textContent,/미입력/);
    assert.match(h.$('metrics-list').textContent,/계산 불가/);
    assert.match(h.$('metrics-list').textContent,/수동/);
  }finally{h.close();}
});
