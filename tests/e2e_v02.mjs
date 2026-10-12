// Actual Chromium + FastAPI + disposable real WordPress; AI here is a labeled HTTP fixture.
// Start tests/serve_v02_fixture.py first. Actual model integration is live_v02_probe.py.
import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {writeFileSync,readFileSync,existsSync} from 'node:fs';
const bin=process.env.CHROMIUM_PATH || chromium.executablePath();
assert.ok(existsSync(bin),'Set CHROMIUM_PATH or install Playwright Chromium.');
const base='http://127.0.0.1:18087';
assert.equal((await fetch(base+'/api/state')).status,200);
const browser=await chromium.launch({headless:true,executablePath:bin,args:['--no-sandbox']});
const context=await browser.newContext({viewport:{width:1440,height:1000},timezoneId:'Asia/Seoul'});
const page=await context.newPage(), errors=[], dialogs=[];
page.on('pageerror',e=>errors.push(String(e)));
page.on('dialog',async d=>{dialogs.push(d.message());await d.accept();});
const enabled=async id=>{await page.waitForFunction(id=>!document.getElementById(id).disabled,id);};
const notice=async text=>{await page.waitForFunction(t=>document.getElementById('notice').textContent.includes(t),text);};
const report={scope:'Real Chromium/FastAPI/WordPress. Synthetic product and AI HTTP fixture, not real AI in this UI test.',checks:[]};
try {
 await page.goto(base);await enabled('save-product');
 for(const [name,value] of Object.entries({name:'브라우저 검증용 키보드 '+Date.now(),source_url:'https://example.com/fixture',affiliate_url:'https://example.com/fixture-affiliate',facts:'USB 유선 연결 방식\n숫자 키 배열 제공',audience:'문서 작업이 잦은 독자',caveats:'운영체제 호환과 크기는 미확인입니다. 실제 상품이 아닌 시험 데이터입니다.'}))await page.locator(`[name="${name}"]`).fill(value);
 const localNow=await page.evaluate(()=>new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16));
 await page.locator('[name=observed_at]').fill(localNow);await page.locator('[name=ownership_confirmed]').check();await page.locator('#save-product').click();await notice('상품을 저장');
 await page.locator('[data-generate]').first().click();await page.locator('#draft-workspace').waitFor({state:'visible'});await enabled('ai-check');
 await page.locator('#approval-confirm').check();await page.locator('#approve').click();await notice('승인했습니다');
 const before=await page.locator('#draft-body').inputValue();
 assert.equal(await page.locator('#ai-generate').isDisabled(),true);
 await page.locator('#ai-check').click();await notice('모델 연결');await page.locator('#confirmed-send').check();await page.locator('#ai-style').selectOption('concise');await page.locator('#ai-generate').click();await notice('후보를 만들었습니다');
 assert.equal(await page.locator('#draft-body').inputValue(),before);await page.locator('[data-apply-candidate]').first().click();await notice('후보를 적용했습니다');
 assert.notEqual(await page.locator('#draft-body').inputValue(),before);assert.equal(await page.locator('#wordpress').isDisabled(),true);assert.equal(await page.locator('#approval-confirm').isChecked(),false);
 report.checks.push('Explicit AI check/consent → candidate comparison preserves approved original → apply invalidates approval');
 await page.screenshot({path:'artifacts/v02-ai-desktop.png',fullPage:true});
 await page.locator('#approval-confirm').check();await page.locator('#approve').click();await notice('승인했습니다');await page.locator('#wordpress').click();await notice('원격 초안을 확인');
 const future=await page.evaluate(()=>new Date(Date.now()+600000-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16));
 await page.locator('#publish-run-at').fill(future);assert.equal(await page.locator('#publish-schedule').isDisabled(),true);await page.locator('#publish-confirm').check();await page.locator('#publish-schedule').click();await notice('예약 확인됨');
 assert.ok(dialogs.some(x=>x.includes('UTC')&&x.includes('127.0.0.1:18789')&&x.includes('승인 버전')));
 assert.ok((await page.locator('#publication-list').innerText()).includes('예약 확인됨'));
 await page.locator('nav [data-view=content]').click();assert.equal(await page.locator('#draft-body').isDisabled(),true);
 await page.locator('nav [data-view=jobs]').click();await page.locator('[data-refresh-publication]').first().click();await notice('원격 조회: 예약');
 await page.screenshot({path:'artifacts/v02-publication-desktop.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'artifacts/v02-publication-mobile.png',fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.getByRole('link',{name:'WordPress에서 예약 관리'}).first().waitFor({state:'visible'});assert.equal(await page.locator('[data-cancel-publication]').count(),0);
 const manager=page.getByRole('link',{name:'WordPress에서 예약 관리'}).first();const management=new URL(await manager.getAttribute('href'));
 const auth=JSON.parse(readFileSync(process.env.STUDIO_WP_FIXTURE_AUTH,'utf8'));const u=new URL(auth.url);assert.equal(u.hostname,'127.0.0.1');assert.equal(u.protocol,'http:');assert.equal(management.origin,u.origin);
 const remoteId=Number(management.searchParams.get('post'));assert.ok(Number.isInteger(remoteId)&&remoteId>0);
 // Only the harness simulates the human operator in WP admin. The app never sends this mutation.
 const manual=await fetch(auth.url+'/wp-json/wp/v2/posts/'+remoteId,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Basic '+Buffer.from(auth.username+':'+auth.password).toString('base64')},body:JSON.stringify({status:'draft'})});assert.equal(manual.status,200);
 await page.locator('[data-refresh-publication]').first().click();await notice('원격 조회: 취소');assert.ok((await page.locator('#publication-list').innerText()).includes('취소 확인됨'));
 await page.locator('nav [data-view=content]').click();assert.equal(await page.locator('#draft-body').isDisabled(),false);
 report.checks.push('UI manual approval → real WP draft → explicit target/version/local+UTC confirmation → actual future state → edit lock → refresh → verified WP admin link (no app cancel control) → harness simulates manual WP cancellation → read-only refresh → unlock');
 report.checks.push('390px mobile has no horizontal overflow');assert.deepEqual(errors,[]);report.checks.push('Browser pageerror 0');report.passed=true;writeFileSync('artifacts/v02-browser-report.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
} catch(e) {await page.screenshot({path:'artifacts/v02-browser-failure.png',fullPage:true});throw e;} finally {await browser.close();}
