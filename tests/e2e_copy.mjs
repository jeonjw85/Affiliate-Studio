import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { existsSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';

const root = process.cwd();
const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'affiliate-copy-'));
const artifacts = path.join(root, 'artifacts');
await fs.mkdir(artifacts, { recursive: true });
const probe = net.createServer();
await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
const port = probe.address().port;
await new Promise(resolve => probe.close(resolve));
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(root, '.venv/bin/python'), ['run.py', '--port', String(port)], {
  cwd: root,
  env: { ...process.env, STUDIO_DATA_DIR: temp, WP_URL: '', WP_USERNAME: '', WP_APP_PASSWORD: '', AI_MODEL: '' },
  stdio: ['ignore', 'pipe', 'pipe'],
});
let log = '', browser;
server.stdout.on('data', data => { log += data; });
server.stderr.on('data', data => { log += data; });
const report = { scope: 'Chromium, isolated temporary SQLite, no AI or WordPress calls', checks: [] };
try {
  let ready = false;
  for (let i = 0; i < 100; i++) {
    try { if ((await fetch(base + '/api/state')).ok) { ready = true; break; } } catch {}
    await delay(50);
  }
  assert.ok(ready, log);
  const executablePath = process.env.CHROMIUM_PATH || [chromium.executablePath(), '/usr/bin/chromium', '/snap/bin/chromium', '/usr/bin/google-chrome'].find(existsSync);
  browser = await chromium.launch({ headless: true, executablePath, args: ['--no-sandbox'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [], external = [], checkedViews = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('request', request => { if (!request.url().startsWith(base)) external.push(request.url()); });
  await page.goto(base);
  await page.waitForFunction(() => !document.querySelector('#product-fields').disabled);
  assert.equal(await page.title(), 'Affiliate Studio');
  assert.equal((await page.locator('.brand').innerText()).replace(/\s+/g, ' ').trim(), 'Affiliate Studio');
  assert.match(await page.locator('footer').innerText(), /^Affiliate Studio/);
  report.checks.push('tab title, header and footer use Affiliate Studio');
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 });
    for (const view of ['products', 'content', 'jobs', 'metrics', 'guide']) {
      await page.locator(`nav [data-view="${view}"]`).click();
      await page.locator(`#view-${view}`).waitFor({ state: 'visible' });
      if (view === 'metrics') await page.waitForFunction(() => document.querySelector('#metrics-list').children.length > 0 && !document.querySelector('#metrics-refresh').disabled);
      const text = await page.locator('body').innerText();
      assert.ok(!/작업대|필요합니다|하세요[.]?|입니다[.]?|습니다[.]?/.test(text), `${width}px ${view}: verbose service copy remains`);
      assert.ok(!/[가-힣]\.(?:\s|$)|[→↔—]/u.test(text), `${width}px ${view}: decorative punctuation remains`);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${width}px ${view}: horizontal overflow`);
      checkedViews.push(`${width}:${view}`);
      if (view === 'products' && width !== 320) {
        await page.screenshot({ path: path.join(artifacts, `copy-${width === 1440 ? 'desktop' : 'mobile'}.png`), fullPage: true });
      }
    }
  }
  report.checkedViews = checkedViews;
  report.checks.push('all navigation pages: concise Korean copy, no decorative arrows or terminal periods, no horizontal overflow');
  const state = await (await fetch(base + '/api/state')).json();
  const invalid = await fetch(base + '/api/products', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf }, body: '{}',
  });
  assert.equal(invalid.status, 400);
  const error = (await invalid.json()).error;
  assert.ok(error && !/합니다|하세요|습니다|입니다|[가-힣]\./.test(error), error);
  report.checks.push('real validation error uses concise wording');
  assert.deepEqual(errors, []);
  assert.deepEqual(external, []);
  report.checks.push('no browser errors, no external network requests');
  report.passed = true;
} catch (error) {
  report.passed = false;
  report.error = error.stack;
  throw error;
} finally {
  await fs.writeFile(path.join(artifacts, 'copy-browser-report.json'), JSON.stringify(report, null, 2));
  await browser?.close();
  if (server.exitCode === null) {
    const closed = new Promise(resolve => server.once('exit', resolve));
    server.kill('SIGTERM');
    await closed;
  }
  await fs.rm(temp, { recursive: true, force: true });
  console.log(JSON.stringify(report, null, 2));
}
