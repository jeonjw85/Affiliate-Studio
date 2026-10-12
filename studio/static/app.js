'use strict';
function actionState(draft, dirty, confirmed, busy, hasErrors) {
  const ready = Boolean(draft) && !dirty && !busy && !hasErrors;
  const approved = ready && draft.status === 'approved';
  return {approve: ready && Boolean(confirmed) && !approved, download: approved, schedule: approved};
}
if (typeof module !== 'undefined') module.exports = {actionState};

if (typeof document !== 'undefined') (() => {
  const $ = id => document.getElementById(id);
  let state = {products:[], drafts:[], jobs:[]}, detail = null;
  let productId = null, productVersion = null, productDirty = false, busy = false, stale = false;
  const gates = new WeakMap();
  const names = ['provider','name','source_url','affiliate_url','price','observed_at','facts','audience','caveats','ownership_confirmed'];
  const field = name => $('product-form').elements.namedItem(name);
  const idPath = id => encodeURIComponent(String(id));
  const states = {review:'검토 필요',approved:'승인됨',pending:'대기 중',completed:'완료',failed:'실패',cancelled:'취소됨',uncertain:'결과 불명'};
  const dirty = () => Boolean(detail) && ($('draft-title').value !== detail.draft.title || $('draft-body').value !== detail.draft.body);
  const hasErrors = () => Boolean(detail?.checks?.errors?.length);
  function message(text, error = false) { $('notice').textContent = text; $('notice').className = error ? 'notice error' : 'notice'; $('notice').hidden = !text; }
  function node(tag, text, className) { const el = document.createElement(tag); if (text != null) el.textContent = String(text); if (className) el.className = className; return el; }
  function button(text, callback, attr, gate = () => true) { const el = node('button',text,'small'); el.type='button'; el.dataset.action='true'; if (attr) el.setAttribute(attr,''); gates.set(el,gate); el.addEventListener('click',()=>{if (!el.disabled && !busy && gate()) callback();}); return el; }
  function dateText(value) { if (!value) return '미확인'; const date = new Date(value); return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('ko-KR'); }
  function localDate(value) { if (!value) return ''; const d = new Date(value); if (Number.isNaN(d.getTime())) return ''; return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16); }
  function iso(value) { const d = new Date(value); if (!value || Number.isNaN(d.getTime())) throw new Error('유효한 날짜와 시각을 입력하세요.'); return d.toISOString(); }
  function show(view) {
    for (const section of document.querySelectorAll('main > section')) section.hidden = section.id !== `view-${view}`;
    for (const item of document.querySelectorAll('nav [data-view]')) { if(item.dataset.view===view) item.setAttribute('aria-current','page'); else item.removeAttribute('aria-current'); }
  }
  async function api(url, method='GET', data) {
    const options = {method, credentials:'same-origin', cache:'no-store', headers:{}};
    if (method !== 'GET') {
      if (!state.csrf) throw new Error('서버 연결을 확인한 뒤 다시 시도하세요.');
      options.headers = {'Content-Type':'application/json','X-CSRF-Token':state.csrf}; options.body = JSON.stringify(data);
    }
    let response;
    try { response = await fetch(url,options); } catch (_) { throw new Error('서버에 연결하지 못했습니다. 서버 실행 상태와 네트워크를 확인하세요.'); }
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(payload.error || payload.detail || `요청 실패 (${response.status})`); error.status = response.status; throw error; }
    return payload;
  }
  function sync() {
    for (const el of document.querySelectorAll('[data-action]')) el.disabled = busy || !(gates.get(el)?.() ?? true);
    const edited = dirty();
    const actions = actionState(detail?.draft, edited || stale || productDirty, $('approval-confirm').checked, busy, hasErrors());
    for (const name of ['approve','download','schedule']) $(name).disabled = !actions[name];
    $('download-text').disabled = !actions.download;
    $('product-fields').disabled = busy || !state.csrf;
    $('draft-fields').disabled = busy || !detail || stale;
    $('save-draft').disabled = busy || !detail || !edited || stale;
    $('approval-confirm').disabled = busy || !detail || edited || stale || productDirty || hasErrors() || detail?.draft.status === 'approved';
    $('run-at').disabled = busy || !actions.schedule;
    $('reload-draft').disabled = busy || !detail;
    $('refresh').disabled = busy; $('refresh-jobs').disabled = busy; $('new-product').disabled = busy;
    $('dirty-status').textContent = stale ? '서버 버전 충돌 · 저장본을 다시 불러오세요.' : edited ? '저장되지 않은 수정 · 승인과 내보내기가 잠겼습니다.' : detail ? `저장된 버전 ${detail.draft.version}` : '';
  }
  async function run(task, success) {
    if (busy) return;
    busy=true; message('처리 중입니다.'); sync();
    try { const result=await task(); if(success) message(typeof success==='function' ? success(result) : success); else message(''); }
    catch(error) {
      if (error.status===409) { stale=true; $('approval-confirm').checked=false; }
      // A failed external mutation can still have reached the remote service.
      // Only re-read local backend state; never automatically repeat its POST.
      try { await refreshState(); } catch(_) {}
      message(`${error.message}${error.status===409 ? '\n다른 변경이 감지되었습니다. 서버 저장본을 다시 불러와 검토하세요. 현재 입력 내용은 유지됩니다.' : ''}`,true);
    } finally { busy=false; sync(); }
  }
  function renderProducts() {
    const list=$('product-list'); list.replaceChildren(); $('product-count').textContent=state.products.length;
    if (!state.products.length) list.append(node('p','등록된 상품이 없습니다. 오른쪽 입력란에서 첫 상품을 저장하세요.','empty'));
    for (const p of state.products) {
      const card=node('article',null,'record'); card.append(node('h3',p.name),node('p',`${p.provider==='toss'?'토스 쇼핑':'쿠팡 파트너스'} · ${p.price == null ? '가격 미입력' : Number(p.price).toLocaleString('ko-KR')+'원'}`,'meta'));
      const actions=node('div',null,'actions');
      actions.append(button('상품 수정',()=>{if (canLeaveProduct()) fillProduct(p);},'data-edit-product'),button('초안 만들기',()=>{
        if (productDirty) { message('상품 입력에 저장되지 않은 수정이 있습니다. 먼저 저장하거나 새 상품 버튼으로 취소하세요.',true); return; }
        if (!canLeaveDraft()) return;
        run(async()=>{const d=await api('/api/drafts','POST',{product_id:p.id}); await refreshState(); await loadDraft(d.id); show('content');},'템플릿 초안을 만들었습니다. 사실과 경고를 직접 검토하세요.');
      },'data-generate')); card.append(actions); list.append(card);
    }
  }
  function canLeaveDraft() { return !dirty() || window.confirm('저장하지 않은 콘텐츠 수정이 있습니다. 수정 내용을 버리고 계속할까요?'); }
  function canLeaveProduct() { return !productDirty || window.confirm('저장하지 않은 상품 정보가 있습니다. 수정 내용을 버릴까요?'); }
  function fillProduct(p) {
    $('product-form').reset(); productId=p?.id ?? null; productVersion=p?.version ?? null;
    if (p) for (const name of names) { if(name==='ownership_confirmed') field(name).checked=p[name]===true; else if(name==='facts') field(name).value=(p[name]||[]).join('\n'); else if(name==='observed_at') field(name).value=localDate(p[name]); else field(name).value=p[name]??''; }
    productDirty=false; $('product-form-title').textContent=p?'상품 수정':'상품 등록'; $('save-product').textContent=p?'변경 저장':'상품 저장';
    $('product-edit-note').textContent=p?'상품을 수정하면 연결된 콘텐츠의 승인이 해제됩니다.':'저장 후 템플릿 초안을 만들 수 있습니다.';
    sync();
  }
  function renderDrafts() {
    $('draft-list').replaceChildren(); $('draft-count').textContent=state.drafts.length;
    if (!state.drafts.length) $('draft-list').append(node('p','저장된 콘텐츠가 없습니다.','muted'));
    for (const d of state.drafts) {
      const item=button(`${d.title} · ${states[d.status]||d.status}`,()=>{if(canLeaveDraft()) run(()=>loadDraft(d.id));},'data-select-draft');
      item.setAttribute('aria-pressed',String(detail?.draft.id===d.id)); $('draft-list').append(item);
    }
  }
  function renderJobs() {
    $('job-list').replaceChildren();
    $('worker-status').textContent=state.worker_running?'작업 실행기 동작 중 · 서버가 실행 중일 때만 대기 작업을 처리합니다.':'작업 실행기 중지 · 대기 작업이 실행되지 않습니다. 서버 상태를 확인하세요.';
    if (!state.jobs.length) $('job-list').append(node('div','예약된 작업이 없습니다. 승인한 콘텐츠에서 로컬 패키지 생성을 예약하세요.','card empty'));
    for (const j of state.jobs) {
      const card=node('article',null,'card job-card'), info=node('div'), actions=node('div',null,'actions');
      const draft=state.drafts.find(d=>d.id===j.draft_id);
      info.append(node('h3',draft?.title || `콘텐츠 #${j.draft_id}`),node('span',states[j.state]||j.state,`badge ${j.state}`),node('p',`${dateText(j.run_at)} · 버전 ${j.version}`,'meta'));
      if(j.error) info.append(node('p',j.error,'job-error'));
      if(j.state==='uncertain') info.append(node('p','결과를 확인한 뒤 조치하세요. 자동으로 재시도하지 않습니다.','job-error'));
      if(j.state==='pending') actions.append(button('예약 취소',()=>run(async()=>{await api(`/api/jobs/${idPath(j.id)}/cancel`,'POST',{});await refreshState();if(detail&&!dirty())await loadDraft(detail.draft.id);},'예약을 취소했습니다.')));
      if(j.state==='completed') actions.append(button('ZIP 다운로드',()=>run(()=>download(`/api/jobs/${idPath(j.id)}/download`,`package-${j.id}.zip`),'패키지를 내려받았습니다.')));
      card.append(info,actions); $('job-list').append(card);
    }
  }
  async function refreshState() {
    const next=await api('/api/state');
    state=next; state.products ||= []; state.drafts ||= []; state.jobs ||= [];
    if (detail && state.drafts.some(d => d.id === detail.draft.id && d.version !== detail.draft.version)) stale = true;
    if (detail) {
      const latest=await api(`/api/drafts/${idPath(detail.draft.id)}`);
      if(latest.draft.version !== detail.draft.version) stale=true;
      detail.checks=latest.checks;
      if(!stale) { detail.draft.status=latest.draft.status; }
      renderReview();
    }
    $('connection-status').textContent=`서버 연결됨 · 상품 ${state.products.length} · 콘텐츠 ${state.drafts.length}`;
    renderProducts();renderDrafts();renderJobs();sync();
  }
  async function loadDraft(id) {
    const next=await api(`/api/drafts/${idPath(id)}`);
    detail=next; stale=false; $('draft-title').value=detail.draft.title; $('draft-body').value=detail.draft.body; $('approval-confirm').checked=false;
    $('content-empty').hidden=true; $('draft-workspace').hidden=false;
    $('preview-frame').srcdoc=detail.preview?.html || '';
    for(const name of ['text','telegram','prompt']) $('preview-'+name).textContent=detail.preview?.[name]||'';
    $('product-facts').replaceChildren();
    const p=detail.product;
    for (const [key,value] of [['상품',p.name],['가격',p.price == null?'미확인':Number(p.price).toLocaleString('ko-KR')+'원'],['정보 확인 시각',dateText(p.observed_at)],['사실',(p.facts||[]).join('\n')],['출처',p.source_url],['제휴 URL',p.affiliate_url],['주의사항',p.caveats||'입력 없음']]) $('product-facts').append(node('dt',key),node('dd',value));
    renderReview();
    renderDrafts();sync();
  }
  function renderReview() {
    $('draft-status').textContent=(stale ? '이전 저장본 · ' : '')+(states[detail.draft.status]||detail.draft.status);
    $('draft-status').className=`badge ${detail.draft.status}`;
    $('checks').replaceChildren();
    for(const [type,label] of [['errors','오류'],['warnings','주의']]) for(const check of detail.checks?.[type]||[]) $('checks').append(node('p',`${label}: ${typeof check==='string'?check:JSON.stringify(check)}`,type==='errors'?'check-error':'check-warning'));
    if(!hasErrors()) $('checks').append(node('p','차단 오류 없음 · 자동 검사는 사실 확인과 정책 검토를 대신하지 않습니다.','muted'));
  }
  async function download(url, filename) {
    const res=await fetch(url,{credentials:'same-origin',cache:'no-store'});
    if(!res.ok) { const p=await res.json().catch(()=>({})); const error=new Error(p.error||`다운로드 실패 (${res.status})`);error.status=res.status;throw error; }
    const blob=await res.blob(), objectURL=URL.createObjectURL(blob), a=node('a'); a.href=objectURL; a.download=filename; document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(objectURL),1000);
  }
  async function refreshDetailAndState() { await refreshState(); if(detail) await loadDraft(detail.draft.id); }
  document.querySelectorAll('[data-view]').forEach(el=>el.addEventListener('click',()=>show(el.dataset.view)));
  $('refresh').addEventListener('click',()=>run(refreshState,'상태를 갱신했습니다. 콘텐츠 편집 내용은 유지됩니다.'));
  $('refresh-jobs').addEventListener('click',()=>run(refreshState,'예약 상태를 갱신했습니다.'));
  $('new-product').addEventListener('click',()=>{if(canLeaveProduct()) fillProduct(null);});
  $('product-form').addEventListener('input',()=>{productDirty=true;sync();});
  $('product-form').addEventListener('change',()=>{productDirty=true;sync();});
  $('product-form').addEventListener('submit',event=>{
    event.preventDefault(); if(busy || !$('product-form').reportValidity())return;
    if(detail && !canLeaveDraft())return;
    run(async()=>{
      const data={};for(const name of names) data[name]=field(name).value.trim();
      data.price=data.price===''?null:Number(data.price);
      if(data.price!==null && (!Number.isSafeInteger(data.price)||data.price<=0))throw new Error('가격은 양의 정수로 입력하거나 비워두세요.');
      data.observed_at=iso(data.observed_at);data.facts=data.facts.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);data.ownership_confirmed=field('ownership_confirmed').checked===true;
      if(!data.facts.length)throw new Error('확인한 사실을 한 줄 이상 입력하세요.');
      if(productId!==null)data.version=productVersion;
      const p=await api(productId===null?'/api/products':`/api/products/${idPath(productId)}`,productId===null?'POST':'PUT',data);
      fillProduct(p);await refreshDetailAndState();
    },'상품을 저장했습니다. 상품 목록에서 초안을 만들 수 있습니다.');
  });
  const onEdit=()=>{$('approval-confirm').checked=false;sync();};
  $('draft-title').addEventListener('input',onEdit);$('draft-body').addEventListener('input',onEdit);
  $('approval-confirm').addEventListener('change',sync);
  $('reload-draft').addEventListener('click',()=>{if(canLeaveDraft())run(()=>loadDraft(detail.draft.id),'서버 저장본을 불러왔습니다. 다시 검토하세요.');});
  $('draft-form').addEventListener('submit',event=>{
    event.preventDefault();if(!detail||!dirty()||stale||busy||!$('draft-form').reportValidity())return;
    run(async()=>{await api(`/api/drafts/${idPath(detail.draft.id)}`,'PUT',{version:detail.draft.version,title:$('draft-title').value,body:$('draft-body').value});await refreshDetailAndState();},'수정본을 저장했습니다. 기존 승인이 해제되었으므로 다시 확인하세요.');
  });
  $('approve').addEventListener('click',()=>{if($('approve').disabled)return;run(async()=>{await api(`/api/drafts/${idPath(detail.draft.id)}/approve`,'POST',{version:detail.draft.version,confirmed:true});await refreshDetailAndState();},'현재 저장본을 승인했습니다.');});
  $('download').addEventListener('click',()=>{if($('download').disabled)return;run(async()=>{await download(`/api/drafts/${idPath(detail.draft.id)}/download?version=${detail.draft.version}`,`content-${detail.draft.id}.zip`);await refreshDetailAndState();},'승인본 패키지를 내려받았습니다.');});
  $('download-text').addEventListener('click',()=>{if($('download-text').disabled)return;run(()=>download(`/api/drafts/${idPath(detail.draft.id)}/text?version=${detail.draft.version}`,`content-${detail.draft.id}-v${detail.draft.version}.txt`),'광고 고지와 링크를 포함한 승인본문을 가져왔습니다. 게시 채널의 허용 조건을 직접 확인하세요.');});
  $('schedule-form').addEventListener('submit',event=>{
    event.preventDefault();if($('schedule').disabled||!$('schedule-form').reportValidity())return;
    run(async()=>{const run_at=iso($('run-at').value);if(new Date(run_at).getTime()<=Date.now())throw new Error('예약 시각은 현재보다 이후여야 합니다.');await api(`/api/drafts/${idPath(detail.draft.id)}/schedule`,'POST',{version:detail.draft.version,run_at});await refreshDetailAndState();show('jobs');},'로컬 패키지 생성을 예약했습니다. 외부에 게시되지 않습니다.');
  });
  window.addEventListener('beforeunload',event=>{if(dirty()||productDirty){event.preventDefault();event.returnValue='';}});
  run(refreshState);
})();
