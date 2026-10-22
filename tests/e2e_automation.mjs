// Browser check of the automation screen against synthetic partners.
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import fs from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {setTimeout as delay} from 'node:timers/promises';
import {chromium} from 'playwright';

const root = process.cwd();
const artifacts = path.join(root, 'artifacts');
await fs.mkdir(artifacts, {recursive: true});
const probe = net.createServer();
await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
const port = probe.address().port;
await new Promise(resolve => probe.close(resolve));
const base = `http://127.0.0.1:${port}`;
// 09:50 KST: ten minutes before the first 10:00 KST slot.
const WINDOW = '2026-10-20T00:50:00+00:00';
const server = spawn(path.join(root, '.venv/bin/python'),
  ['tests/serve_automation_fixture.py', String(port)], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']});
let log = '';
server.stdout.on('data', chunk => log += chunk);
server.stderr.on('data', chunk => log += chunk);
let browser;
const report = {fixture: '합성 Telegram·AI·WordPress / 실제 외부 발행 아님', checks: []};

try {
  let ready = false;
  for (let i = 0; i < 120; i += 1) {
    try {
      const response = await fetch(base + '/api/state');
      if (response.ok) { ready = true; break; }
    } catch (_) {}
    await delay(100);
  }
  assert.ok(ready, 'fixture server ready: ' + log);
  const bin = process.env.CHROMIUM_PATH || [chromium.executablePath(), '/usr/bin/google-chrome']
    .find(existsSync);
  browser = await chromium.launch({headless: true, ...(bin ? {executablePath: bin} : {})});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}, timezoneId: 'UTC'});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', dialog => dialog.accept());
  await page.goto(base);
  await page.waitForFunction(() => !document.querySelector('#automation-fields').disabled);

  const csrf = (await (await fetch(base + '/api/state')).json()).csrf;
  const post = (url, body) => fetch(base + url, {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
    body: JSON.stringify(body)});
  await page.locator('nav [data-view="automation"]').click();
  await page.locator('[name=automation_enabled]').check();
  await page.locator('[name=coupang_limit]').fill('2');
  await page.locator('[name=toss_limit]').fill('1');
  await page.locator('[name=automation_start]').fill('10:00');
  await page.locator('[name=automation_end]').fill('21:00');
  await page.locator('#automation-save').click();
  await page.waitForFunction(() => /저장 완료/.test(document.querySelector('#automation-notice').textContent));
  report.checks.push('서비스별 편수 저장');

  const settings = await (await fetch(base + '/api/automation')).json();
  assert.deepEqual(settings.settings.schedule.daily_limits, {coupang: 2, toss: 1});

  const send = (update_id, text, extra = {}) => post('/__fixture/update', {
    update_id, message: {message_id: update_id, date: 1792000000,
      chat: {id: 1234, type: 'private'}, from: {id: 5678, is_bot: false, first_name: 'fixture'},
      text, ...extra}});

  // A ready link plus a blocked link that needs a description.
  await send(1, 'https://link.coupang.com/a/fixture-coupang');
  let fixture;
  for (let i = 0; i < 50; i += 1) {
    fixture = await (await fetch(base + '/__fixture/state')).json();
    if (fixture.automation.requests.length) break;
    await delay(100);
  }
  assert.equal(fixture.automation.requests.length, 1);
  assert.equal(fixture.automation.requests[0].affiliate_url,
    'https://link.coupang.com/a/fixture-coupang');
  for (let i = 0; i < 40 && !fixture.sent.some(item => item.text.includes('상품명')); i += 1) {
    await post('/__fixture/tick', {at: WINDOW});
    fixture = await (await fetch(base + '/__fixture/state')).json();
    await delay(100);
  }
  const question = fixture.sent.find(item => item.text.includes('상품명'));
  assert.ok(question, 'description question delivered: ' + JSON.stringify(fixture.sent));
  const requestId = fixture.automation.requests[0].id;
  await send(2, '상품명: 합성 브라우저 상품\n특징: 확인한 사실 1\n특징: 확인한 사실 2', {
    reply_to_message: {message_id: question.id, from: {id: 900, is_bot: true},
      chat: {id: 1234, type: 'private'}}});
  for (let i = 0; i < 40; i += 1) {
    await post('/__fixture/tick', {at: WINDOW});
    fixture = await (await fetch(base + '/__fixture/state')).json();
    if (fixture.automation.requests[0].state === 'scheduled') break;
    await delay(150);
  }
  assert.equal(fixture.automation.requests[0].state, 'scheduled',
    JSON.stringify(fixture.automation.requests[0]));
  assert.equal(Object.keys(fixture.posts).length, 1);
  assert.ok(Object.values(fixture.posts)[0].content.raw.includes('https://link.coupang.com/a/fixture-coupang'));
  await page.locator('#automation-refresh').click();
  await page.waitForTimeout(500);
  assert.match(await page.locator('#automation-requests').innerText(), /예약 확인/);
  report.checks.push('Telegram 링크 접수 → 설명 답장 → WordPress 예약 확인');
  report.requestId = requestId;

  await page.screenshot({path: path.join(artifacts, 'automation-desktop.png'), fullPage: true});
  const mobile = await context.newPage();
  await mobile.setViewportSize({width: 390, height: 844});
  await mobile.goto(base);
  await mobile.locator('nav [data-view="automation"]').click();
  await mobile.waitForSelector('#view-automation:not([hidden])');
  const overflow = await mobile.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  assert.equal(overflow, false);
  await mobile.screenshot({path: path.join(artifacts, 'automation-mobile.png'), fullPage: true});
  report.checks.push('390px 자동 운영 화면 가로 넘침 없음');
  assert.deepEqual(errors, []);
  report.checks.push('브라우저 pageerror 0');
  console.log(JSON.stringify({...report, passed: true}, null, 2));
} finally {
  if (browser) await browser.close();
  server.kill('SIGTERM');
  await new Promise(resolve => server.once('exit', resolve));
}
