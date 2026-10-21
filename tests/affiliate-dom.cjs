const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const html = fs.readFileSync(__dirname+'/../studio/static/index.html','utf8');
const js = fs.readFileSync(__dirname+'/../studio/static/app.js','utf8');
const tick = () => new Promise(resolve=>setTimeout(resolve,10));

async function setup(configured=true) {
  const state={csrf:'fixture-csrf',products:[],drafts:[],jobs:[],publications:[],connections:{affiliate:{
    coupang:{configured,error:configured?'':'쿠팡 API 설정 필요'},toss:{configured,error:configured?'':'토스 API 설정 필요'}
  }}};
  const requests=[];
  const dom=new JSDOM(html,{url:'http://localhost:8787',runScripts:'outside-only'}), w=dom.window;
  const $=id=>w.document.getElementById(id), field=name=>$('product-form').elements.namedItem(name);
  let respond=async request=>({ok:true,json:async()=>({provider:request.body.provider,source_url:request.body.source_url,
    affiliate_url:request.body.provider==='toss'?'https://toss.im/_m/fixture':'https://link.coupang.com/a/fixture'})});
  w.confirm=()=>true;
  w.fetch=async(url,options={})=>{
    const request={url,method:options.method||'GET',body:options.body?JSON.parse(options.body):null,headers:options.headers};
    requests.push(request);
    if(url==='/api/state')return {ok:true,json:async()=>JSON.parse(JSON.stringify(state))};
    if(url==='/api/affiliate-links')return respond(request);
    if(url==='/api/products' || url==='/api/products/saved-product') {
      const product={...request.body,id:'saved-product',version:(request.body.version||0)+1};
      state.products=[product];return {ok:true,json:async()=>JSON.parse(JSON.stringify(product))};
    }
    throw Error('Unexpected request: '+url);
  };
  w.eval(js);await tick();
  function input(name,value) { field(name).value=value;field(name).dispatchEvent(new w.Event('input',{bubbles:true})); }
  return {w,$,field,input,requests,state,reply:fn=>{respond=fn;},close:()=>w.close()};
}

test('unconfigured service explains setup and allows manual affiliate input',async()=>{
  const h=await setup(false);try{
    assert.ok(h.$('generate-affiliate'),'affiliate generation button');
    h.input('source_url','https://www.coupang.com/vp/products/123');
    assert.equal(h.$('generate-affiliate').disabled,true);
    assert.match(h.$('affiliate-status').textContent,/쿠팡.*설정/);
    assert.equal(h.$('product-fields').disabled,false);
    h.input('affiliate_url','https://link.coupang.com/a/manual');
    assert.equal(h.field('affiliate_url').value,'https://link.coupang.com/a/manual');
    h.input('provider','toss');
    assert.match(h.$('affiliate-status').textContent,/토스.*설정/);
    assert.equal(h.requests.filter(r=>r.method==='POST').length,0);
  }finally{h.close();}
});

for(const [provider,source,link] of [
  ['coupang','https://www.coupang.com/vp/products/123?itemId=456','https://link.coupang.com/a/fixture'],
  ['toss','https://toss.shopping/t/9876','https://toss.im/_m/fixture']
])test(`${provider}: explicit generation fills only the affiliate field and marks unsaved form`,async()=>{
  const h=await setup();try{
    assert.ok(h.$('generate-affiliate'),'affiliate generation button');
    assert.equal(h.$('generate-affiliate').disabled,true);
    h.input('provider',provider);h.input('source_url',source);h.input('name','작성 중인 상품');
    h.field('ownership_confirmed').checked=true;
    assert.equal(h.$('generate-affiliate').disabled,false);
    assert.match(h.$('affiliate-status').textContent,/설정됨.*미확인/);
    assert.equal(h.requests.filter(r=>r.method==='POST').length,0);
    h.$('generate-affiliate').click();h.$('generate-affiliate').click();
    assert.equal(h.$('product-fields').disabled,true);
    await tick();
    const calls=h.requests.filter(r=>r.method==='POST');assert.equal(calls.length,1);
    assert.deepEqual(calls[0].body,{provider,source_url:source});
    assert.equal(calls[0].headers['X-CSRF-Token'],'fixture-csrf');
    assert.equal(h.field('affiliate_url').value,link);
    assert.equal(h.field('name').value,'작성 중인 상품');assert.equal(h.field('source_url').value,source);
    assert.equal(h.field('ownership_confirmed').checked,false);
    assert.match(h.$('notice').textContent,/생성 완료.*저장/);
    assert.equal(h.$('product-fields').disabled,false);
    const leave=new h.w.Event('beforeunload',{cancelable:true});h.w.dispatchEvent(leave);
    assert.equal(leave.defaultPrevented,true);
  }finally{h.close();}
});

test('API failure keeps existing affiliate URL and form values with no automatic retry',async()=>{
  const h=await setup();try{
    assert.ok(h.$('generate-affiliate'),'affiliate generation button');
    h.input('source_url','https://www.coupang.com/vp/products/123');
    h.input('affiliate_url','https://link.coupang.com/a/manual');h.input('facts','작성한 사실');
    h.reply(async()=>({ok:false,status:400,json:async()=>({error:'쿠팡 API 인증·권한 오류'})}));
    h.$('generate-affiliate').click();await tick();
    assert.equal(h.field('affiliate_url').value,'https://link.coupang.com/a/manual');
    assert.equal(h.field('facts').value,'작성한 사실');
    assert.match(h.$('notice').textContent,/인증·권한 오류/);
    assert.equal(h.requests.filter(r=>r.method==='POST').length,1);
    assert.equal(h.$('generate-affiliate').disabled,false);
  }finally{h.close();}
});

test('changing source or provider clears a generated link, but preserves manually edited links',async()=>{
  const h=await setup();try{
    assert.ok(h.$('generate-affiliate'),'affiliate generation button');
    h.input('source_url','https://www.coupang.com/vp/products/123');
    h.$('generate-affiliate').click();await tick();
    h.input('source_url','https://www.coupang.com/vp/products/456');
    assert.equal(h.field('affiliate_url').value,'');
    h.$('generate-affiliate').click();await tick();
    h.input('provider','toss');assert.equal(h.field('affiliate_url').value,'');
    h.input('source_url','https://toss.shopping/t/9876');h.$('generate-affiliate').click();await tick();
    h.input('affiliate_url','https://toss.im/_m/manual');
    h.input('source_url','https://toss.shopping/t/9999');
    assert.equal(h.field('affiliate_url').value,'https://toss.im/_m/manual');
  }finally{h.close();}
});

test('a delayed response cannot fill a different product URL',async()=>{
  const h=await setup();try{
    assert.ok(h.$('generate-affiliate'),'affiliate generation button');
    h.input('source_url','https://www.coupang.com/vp/products/123');
    let resolve;h.reply(()=>new Promise(r=>{resolve=r;}));
    h.$('generate-affiliate').click();
    h.input('source_url','https://www.coupang.com/vp/products/456');
    resolve({ok:true,json:async()=>({provider:'coupang',source_url:'https://www.coupang.com/vp/products/123',affiliate_url:'https://link.coupang.com/a/fixture'})});
    await tick();assert.equal(h.field('affiliate_url').value,'');
    assert.match(h.$('notice').textContent,/변경/);
  }finally{h.close();}
});

test('locked products cannot issue a replacement link',async()=>{
  const h=await setup();try{
    assert.ok(h.$('generate-affiliate'),'affiliate generation button');
    h.state.products=[{id:1,version:1,name:'상품',provider:'coupang',source_url:'https://www.coupang.com/vp/products/123',
      affiliate_url:'https://link.coupang.com/a/existing',facts:['사실'],ownership_confirmed:true}];
    h.$('refresh').click();await tick();h.w.document.querySelector('[data-edit-product]').click();
    h.state.products[0].publication_lock=true;h.$('refresh').click();await tick();
    assert.equal(h.$('generate-affiliate').disabled,true);h.$('generate-affiliate').click();
    assert.equal(h.requests.filter(r=>r.method==='POST').length,0);
  }finally{h.close();}
});

for(const reselect of [false,true])for(const changed of ['source_url','provider'])
test(`saved link stays bound to its product when ${changed} changes${reselect?' after reselecting':''}`,async()=>{
  const h=await setup();try{
    h.input('source_url','https://www.coupang.com/vp/products/123');
    h.$('generate-affiliate').click();await tick();
    for(const [name,value] of Object.entries({name:'저장 상품',facts:'확인한 사실',audience:'독자',caveats:'주의사항',observed_at:'2026-10-19T10:00'}))h.input(name,value);
    h.field('ownership_confirmed').checked=true;
    h.$('product-form').dispatchEvent(new h.w.Event('submit',{cancelable:true}));await tick();
    assert.equal(h.requests.filter(r=>r.url==='/api/products').length,1,'save completed before changing the target');
    if(reselect){h.$('new-product').click();h.w.document.querySelector('[data-edit-product]').click();}
    assert.equal(h.field('ownership_confirmed').checked,true);
    h.input(changed,changed==='provider'?'toss':'https://www.coupang.com/vp/products/456');
    assert.equal(h.field('affiliate_url').value,'','link for the prior saved target must be invalidated');
    assert.equal(h.field('ownership_confirmed').checked,false);
    h.$('product-form').dispatchEvent(new h.w.Event('submit',{cancelable:true}));await tick();
    assert.equal(h.requests.filter(r=>r.method==='PUT').length,0,'stale link cannot be saved under the new target');
    const replacement=changed==='provider'?'https://toss.im/_m/manual':'https://link.coupang.com/a/manual';
    if(changed==='provider')h.input('source_url','https://toss.shopping/t/9876');
    h.input('affiliate_url',replacement);h.field('ownership_confirmed').checked=true;
    h.$('product-form').dispatchEvent(new h.w.Event('submit',{cancelable:true}));await tick();
    assert.equal(h.requests.find(r=>r.method==='PUT').body.affiliate_url,replacement,'manual replacement still saves normally');
  }finally{h.close();}
});
