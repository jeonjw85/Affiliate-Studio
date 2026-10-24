const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const html = () => fs.readFileSync(__dirname + '/../studio/static/index.html', 'utf8');
const script = () => fs.readFileSync(__dirname + '/../studio/static/automation.js', 'utf8');
const tick = () => new Promise(resolve => setTimeout(resolve, 20));

function settings(overrides = {}) {
  return {version: 3, enabled: true, pending_schedule: null,
    writing: {topic: '생활용품', audience: '정리하는 독자', tone: '차분한 존댓말', purpose: '',
              extra_instructions: [], category_instructions: {}},
    schedule: {timezone: 'Asia/Seoul', weekdays: [0,1,2,3,4,5,6], start: '09:00', end: '21:00',
               daily_limits: {coupang: 10, toss: 5}}, ...overrides};
}

async function setup(options = {}) {
  const state = {settings: settings(), counts: {coupang: {scheduled: 3}, toss: {awaiting_info: 1}},
    requests: [{id: 'req_one', provider: 'coupang', state: 'scheduled', affiliate_url: 'https://link.coupang.com/a/fixture',
                evidence: {name: '합성 상품', facts: [{text: '사실', source: 'page'}]}, created_at: '2026-10-20T00:00:00Z'},
               {id: 'req_two', provider: 'toss', state: 'awaiting_info', affiliate_url: 'https://toss.im/_m/fixture',
                evidence: {name: '', facts: []}, created_at: '2026-10-20T00:01:00Z'}],
    publications: [], notifications: [],
    connections: {configured: true, telegram: true, ai: true, wordpress: true, running: true,
                  affiliate_required: false, last_error: ''},
    worker: {running: true}};
  const detail = {request: state.requests[0], article: {title: '합성 제목', html: '<p>본문</p>', style: 'intro'},
    publications: [{id: 'pub', state: 'scheduled', run_at: '2026-10-20T00:15:00Z', remote_url: 'https://wp.example.test/wp-admin/post.php?post=42&action=edit'}],
    notifications: [{id: 'n', state: 'sent', text: '예약 확인'}]};
  const dom = new JSDOM(html(), {url: 'http://localhost:8787', runScripts: 'outside-only'});
  const w = dom.window, $ = id => w.document.getElementById(id), requests = [];
  let reply = async request => ({ok: true, json: async () => structuredClone(
    request.url === '/api/automation' ? state : detail)});
  w.confirm = () => true;
  w.fetch = async (url, options = {}) => {
    requests.push({url, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null,
      headers: options.headers || {}});
    if (url === '/api/state') return {ok: true, json: async () => ({csrf: 'fixture-csrf'})};
    return reply({url, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null});
  };
  w.eval(fs.readFileSync(__dirname + '/../studio/static/app.js', 'utf8'));
  w.eval(script());
  await tick();
  const field = name => $('automation-form').elements.namedItem(name);
  return {w, $, state, requests, detail, field, reply: fn => { reply = fn; }, close: () => w.close()};
}

test('automation view exposes independent service limits and applies them', async () => {
  const h = await setup();
  try {
    h.w.document.querySelector('nav [data-view="automation"]').click();
    assert.equal(h.$('view-automation').hidden, false);
    assert.equal(h.field('coupang_limit').value, '10');
    assert.equal(h.field('toss_limit').value, '5');
    assert.equal(h.field('automation_enabled').checked, true);
    assert.match(h.$('automation-status').textContent, /연결됨|정상/);
    h.field('coupang_limit').value = '4';
    h.field('toss_limit').value = '0';
    h.$('automation-form').dispatchEvent(new h.w.Event('submit', {cancelable: true}));
    await tick();
    const save = h.requests.filter(r => r.method === 'PUT');
    assert.equal(save.length, 1);
    assert.deepEqual(save[0].body.schedule.daily_limits, {coupang: 4, toss: 0});
    assert.equal(save[0].body.version, 3);
    assert.match(h.$('automation-notice').textContent, /저장/);
  } finally { h.close(); }
});

test('edits survive a refresh and a conflict; no duplicate save request', async () => {
  const h = await setup();
  try {
    h.w.document.querySelector('nav [data-view="automation"]').click();
    h.field('automation_topic').value = '작성 중 주제';
    h.field('automation_topic').dispatchEvent(new h.w.Event('input', {bubbles: true}));
    h.$('automation-refresh').click();
    await tick();
    assert.equal(h.field('automation_topic').value, '작성 중 주제');
    h.reply(async () => ({ok: false, status: 409, json: async () => ({error: '설정 변경됨'})}));
    h.$('automation-form').dispatchEvent(new h.w.Event('submit', {cancelable: true}));
    await tick();
    assert.equal(h.field('automation_topic').value, '작성 중 주제');
    assert.match(h.$('automation-notice').textContent, /변경됨/);
    assert.equal(h.requests.filter(r => r.method === 'PUT').length, 1);
  } finally { h.close(); }
});

test('request states stay distinct and user text is inert', async () => {
  const h = await setup();
  try {
    h.state.requests[1].evidence.name = '<img src=x onerror=alert(1)>';
    h.w.document.querySelector('nav [data-view="automation"]').click();
    h.$('automation-refresh').click();
    await tick();
    const list = h.$('automation-requests');
    assert.match(list.textContent, /예약 확인|예약/);
    assert.match(list.textContent, /설명 대기/);
    assert.equal(list.querySelectorAll('img').length, 0);
    assert.ok(!list.textContent.includes('발행 확인'), 'scheduled is not published');
    list.querySelector('[data-automation-request="req_one"]').click();
    await tick();
    assert.match(h.$('automation-detail').textContent, /합성 제목/);
    assert.equal(h.$('automation-preview').getAttribute('sandbox'), '');
    assert.equal(h.$('automation-preview').srcdoc, '<p>본문</p>');
  } finally { h.close(); }
});

test('multi-product detail lists products and retries a blocked update', async () => {
  const h = await setup();
  try {
    h.detail.request.products = [
      {index: 1, provider: 'coupang', state: 'ready', affiliate_url: 'https://link.coupang.com/a/fixture',
       evidence: {name: '첫 상품', facts: [{text: '첫 사실', source: 'page'}]}},
      {index: 2, provider: 'toss', state: 'awaiting_info', affiliate_url: 'https://toss.im/_m/fixture',
       evidence: {name: '', facts: []}}];
    h.detail.request.update_state = 'update_blocked';
    h.detail.request.update_error = '원격 본문이 마지막 확인본과 다름';
    h.w.document.querySelector('nav [data-view="automation"]').click();
    h.$('automation-requests').querySelector('[data-automation-request="req_one"]').click();
    await tick();
    const products = h.$('automation-products');
    assert.ok(products, 'product list');
    assert.equal(products.querySelectorAll('[data-product-index]').length, 2);
    assert.match(products.textContent, /첫 상품/);
    assert.match(products.textContent, /설명 대기/);
    assert.match(h.$('automation-detail').textContent, /마지막 확인본/);
    const retry = h.$('automation-update-retry');
    assert.ok(retry, 'update retry button');
    h.detail.request.update_state = 'updating';
    h.detail.request.products = h.detail.request.products || [];
    retry.click();
    await tick();
    const call = h.requests.find(r => r.url === '/api/automation/requests/req_one/update');
    assert.equal(call.method, 'POST');
    assert.equal(call.headers['X-CSRF-Token'], 'fixture-csrf');
    assert.equal(h.requests.filter(r => r.url === '/api/automation/requests/req_one/update').length, 1);
  } finally { h.close(); }
});

test('affiliate API keys are not an activation requirement', async () => {
  const h = await setup();
  try {
    h.state.connections.affiliate_required = false;
    h.w.document.querySelector('nav [data-view="automation"]').click();
    assert.equal(h.field('automation_enabled').disabled, false);
    assert.match(h.$('automation-status').textContent, /제휴 링크 생성 API.*필요 없|API 키.*필요 없/);
  } finally { h.close(); }
});

test('view is reachable and keeps short copy rules', () => {
  const dom = new JSDOM(html());
  try {
    const d = dom.window.document;
    assert.ok(d.querySelector('nav [data-view="automation"]'));
    assert.equal(d.querySelector('#automation-heading').textContent.length > 0, true);
    const visible = d.querySelector('#view-automation').textContent;
    assert.match(visible, /링크|Telegram|텔레그램/);
    assert.ok(!/→/.test(visible), 'no decorative arrow');
  } finally { dom.window.close(); }
});
