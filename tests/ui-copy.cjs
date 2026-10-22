const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const source = name => fs.readFileSync(`${__dirname}/../studio/static/${name}`, 'utf8');
const verboseCopy = /[가-힣]\.(?=\s|$|['"`])|(?:습니다|합니다|됩니다|입니다|하세요)|[→⇒➜✨🚀]/g;
function briefCopy(text) {
  assert.deepEqual(text.match(verboseCopy) || [], [], 'UI 안내에는 종결 마침표, 장황한 어미, 장식 기호를 쓰지 않음');
}

test('static guidance uses short copy while retaining consent and publication limits', () => {
  const dom = new JSDOM(source('index.html'));
  try {
    const d = dom.window.document;
    const visibleCopy = d.body.textContent + [...d.querySelectorAll('[placeholder]')].map(el => el.placeholder).join('\n');
    briefCopy(visibleCopy);
    assert.equal(d.querySelector('#products-heading').textContent, '상품 정보 등록');
    assert.equal(d.querySelector('#content-heading').textContent, '콘텐츠 검토와 승인');
    assert.equal(d.querySelector('#metrics-heading').textContent, '성과 기록');
    assert.equal(d.querySelector('#guide-heading').textContent, '사용 방법');
    assert.match(d.querySelector('#confirmed-send').closest('label').textContent, /현재 저장본.*설정된 AI.*전송.*동의/);
    assert.match(d.querySelector('#approval-confirm').closest('label').textContent, /상품 정보.*제휴 고지.*표현.*정책.*직접 확인/);
    assert.match(d.querySelector('#publish-confirm').closest('label').textContent, /WordPress.*공개.*예약.*동의.*로컬 ZIP.*다름/);
    assert.match(d.querySelector('.ai-card').textContent, /제휴 URL.*WordPress 비밀번호.*전송 안 함/);
    assert.match(d.querySelector('.ai-card').textContent, /자동 적용.*승인 안 함.*사실 검증.*아님/);
    assert.match(d.querySelector('.publication-panel').textContent, /5분.*30일.*24시간/);
    assert.match(d.querySelector('#view-jobs').textContent, /결과 불명.*자동 재시도 안 함/);
    assert.match(d.querySelector('#view-jobs').textContent, /이미 공개된 글.*취소.*삭제.*비공개 전환 불가/);
    const help = d.querySelector('#format-help').textContent;
    for (const token of ['## 소제목', '### 작은 소제목', '**굵게**', '- 목록', '1. 순서', '> 주의', '2~8열', '1~40행']) assert.ok(help.includes(token), token);
    assert.equal(d.querySelector('[name="revenue_won"]').step, '0.01');
    assert.equal(d.querySelector('[name="source_url"]').placeholder, 'https://');
  } finally { dom.window.close(); }
});

test('app-owned copy is brief without changing saved content, times or explicit consent gates', async () => {
  briefCopy(source('app.js'));
  const dom = new JSDOM(source('index.html'), {url:'http://localhost:8090', runScripts:'outside-only'});
  try {
    const w = dom.window, $ = id => w.document.getElementById(id);
    const tick = () => new Promise(resolve => setTimeout(resolve, 10));
    const body = '## 원문입니다.\n\n| 항목 | 확인한 내용 |\n| --- | --- |\n| 값 | 001.20 |\n\n고지입니다. https://example.test/a';
    const product = {id:1,version:1,name:'상품입니다.',facts:['값 001.20'],observed_at:'2026-10-11T00:00:00Z',price:null};
    const draft = {id:1,product_id:1,version:1,title:'저장 제목입니다.',body,body_format:'markdown-v1',status:'approved'};
    const state = {csrf:'test-only',products:[product],drafts:[draft],jobs:[],publications:[],connections:{wordpress:true,wordpress_url:'https://wp.example.test',ai:true,ai_model:'local-test'}};
    const detail = {product,draft,checks:{errors:[],warnings:[]},preview:{html:'<p>고지입니다. 001.20</p>',text:body,telegram:body,prompt:body},candidates:[]};
    const requests = [], confirmations = [];
    w.confirm = text => { confirmations.push(text); return false; };
    w.fetch = async (url, options={}) => {
      requests.push({url,method:options.method || 'GET'});
      assert.equal(options.method || 'GET','GET','cancelled confirmations never send a mutation');
      assert.ok(['/api/state','/api/drafts/1'].includes(url),url);
      return {ok:true,json:async()=>structuredClone(url==='/api/state'?state:detail)};
    };
    w.eval(source('app.js')); await tick();
    w.document.querySelector('[data-select-draft]').click(); await tick();
    assert.equal($('draft-body').value,body);
    assert.equal($('preview-text').textContent,body);
    assert.equal($('preview-frame').srcdoc,detail.preview.html);
    assert.ok($('product-facts').textContent.includes(new w.Date(product.observed_at).toLocaleString('ko-KR')));
    assert.equal($('ai-generate').disabled,true);
    assert.equal($('publish-schedule').disabled,true);
    $('wordpress').click();
    assert.match(confirmations.at(-1),/승인본.*외부 WordPress.*전송/);
    assert.match(confirmations.at(-1),/원격 초안만 생성.*공개 게시 안 함/);
    briefCopy(confirmations.at(-1));
    w.Date.now = () => new Date('2026-10-11T00:00Z').getTime();
    $('publish-run-at').value='2026-10-12T12:00';
    $('publish-confirm').checked=true; $('publish-confirm').dispatchEvent(new w.Event('change'));
    $('publish-form').dispatchEvent(new w.Event('submit',{cancelable:true}));
    const confirmation = confirmations.at(-1);
    assert.match(confirmation,/실제 WordPress 공개 게시 예약/);
    assert.ok(confirmation.includes(draft.title),'saved title is not reworded');
    assert.ok(confirmation.includes(new w.Date('2026-10-12T12:00').toISOString()),'UTC value is unchanged');
    assert.match(confirmation,/대상: https:\/\/wp.example.test\//);
    assert.match(confirmation,/앱 종료 후에도 예약 유지/);
    assert.equal(requests.some(r=>r.method!=='GET'),false);
    state.publications=[{id:8,draft_id:1,version:1,state:'cancelled',target:'https://wp.example.test'}];
    $('refresh').click(); await tick();
    assert.equal($('publish-schedule').disabled,true);
    assert.match($('publication-list').textContent,/동일 버전.*재예약 불가/);
    assert.match($('publication-list').textContent,/취소 확인.*수정·저장.*재승인.*새 원격 초안.*새 예약/);
    assert.equal($('draft-body').value,body);
  } finally { dom.window.close(); }
});

test('automation copy is brief and distinguishes scheduled from published', async () => {
  briefCopy(source('automation.js'));
  const dom = new JSDOM(source('index.html'));
  try {
    const d = dom.window.document;
    briefCopy(d.querySelector('#view-automation').textContent);
    assert.match(d.querySelector('#view-automation').textContent, /쿠팡.*토스.*편수|편수.*각각/);
    assert.match(d.querySelector('#view-automation').textContent, /API 키.*필요 없음/);
  } finally { dom.window.close(); }
});

test('metrics copy is brief while decimal examples, stored values and deletion scope stay explicit', async () => {
  briefCopy(source('measurements.js'));
  const dom = new JSDOM(source('index.html'), {url:'http://localhost:8090', runScripts:'outside-only'});
  try {
    const w=dom.window, $=id=>w.document.getElementById(id), requests=[], confirmations=[];
    const record={id:1,version:1,draft_id:1,draft_title:'제목입니다.',draft_version:1,channel:'naver',period_start:'2026-10-01',period_end:'2026-10-02',source:'보고서입니다. https://example.test/a',note:'유지할 메모입니다. 001.20',views:0,clicks:null,orders:null,revenue_won:'1000.50',cost_won:null,rates:{},warnings:[]};
    w.fetch=async(url,options={})=>{
      requests.push({url,method:options.method || 'GET'});
      assert.equal(options.method || 'GET','GET');
      return {ok:true,json:async()=>url==='/api/state'?{csrf:'test-only',drafts:[{id:1,title:record.draft_title,version:1}]}:{items:[structuredClone(record)]}};
    };
    w.confirm=text=>{confirmations.push(text);return false;};
    w.eval(source('measurements.js'));
    w.document.querySelector('nav [data-view="metrics"]').click();
    await new Promise(resolve=>setTimeout(resolve,15));
    const card=$('metrics-list');
    for(const value of [record.source,record.note,record.period_start,record.period_end]) assert.ok(card.textContent.includes(value),value);
    assert.ok(card.textContent.includes('1,000.5원'));
    assert.match(card.textContent,/미입력/);
    card.querySelector('[data-edit-measurement]').click();
    const amount=$('metrics-form').elements.revenue_won;
    amount.value='1e3';$('metrics-form').dispatchEvent(new w.Event('input'));
    assert.equal(amount.checkValidity(),false);
    assert.match(amount.validationMessage,/1000\.50/);
    briefCopy(amount.validationMessage);
    card.querySelector('[data-delete-measurement]').click();
    assert.match(confirmations.at(-1),/성과 기록만 삭제/);
    assert.match(confirmations.at(-1),/원본 콘텐츠와 외부 게시글 유지/);
    assert.ok(confirmations.at(-1).includes(record.draft_title));
    assert.equal(requests.some(r=>r.method!=='GET'),false);
  } finally { dom.window.close(); }
});

test('service name is Affiliate Studio in title, header, fallback, guide and footer', () => {
  const dom = new JSDOM(source('index.html'));
  try {
    const d = dom.window.document;
    assert.equal(d.title, 'Affiliate Studio');
    assert.equal(d.querySelector('.brand').textContent, 'Affiliate Studio');
    assert.match(d.querySelector('noscript').textContent, /Affiliate Studio/);
    assert.match(d.querySelector('#view-guide').textContent, /Affiliate Studio/);
    assert.match(d.querySelector('footer').textContent, /^Affiliate Studio v0\.4\.1/);
    assert.doesNotMatch(d.body.textContent, /작업대/);
  } finally { dom.window.close(); }
});
