'use strict';
function actionState(draft, dirty, confirmed, wordpress, busy, hasErrors, locked = false) {
  const ready = Boolean(draft) && !dirty && !busy && !hasErrors && !locked;
  const approved = ready && draft.status === 'approved';
  return {approve: ready && Boolean(confirmed) && !approved, download: approved, schedule: approved, wordpress: approved && Boolean(wordpress)};
}
if (typeof module !== 'undefined') module.exports = {actionState};

if (typeof document !== 'undefined') (() => {
  const $ = id => document.getElementById(id);
  let state = {products:[], drafts:[], jobs:[], connections:{wordpress:false}}, detail = null;
  let productId = null, productVersion = null, productDirty = false, busy = false, stale = false, aiCheck = null;
  const gates = new WeakMap();
  const activePublication = p => ['sending','future','uncertain'].includes(p.state);
  const draftLocked = () => Boolean(detail?.publication_lock || detail?.draft.publication_lock || (state.publications || []).some(p => p.draft_id === detail?.draft.id && activePublication(p)));
  const productLocked = id => id != null && (state.products.some(p => p.id === id && p.publication_lock) || (detail?.product.id === id && draftLocked()) || (state.publications || []).some(p => activePublication(p) && state.drafts.some(d => d.id === p.draft_id && d.product_id === id)));
  const canAI = () => Boolean(detail && !busy && !dirty() && !productDirty && !stale && !draftLocked());
  const publicationStates = {sending:'전송 중 · 미확정',future:'예약 확인됨 · 아직 공개 아님',publish:'공개 확인됨',cancelled:'취소 확인됨 · 원격 초안',uncertain:'결과 불명 · 수동 조회 필요'};
  function safeURL(value, target) { try { const u=new URL(value); if(!['http:','https:'].includes(u.protocol)||u.username||u.password)return null; if(target && u.origin!==new URL(target).origin)return null; return u.href; } catch (_) { return null; } }
  function adminURL(p) {
    if (!Number.isSafeInteger(p.remote_id) || p.remote_id <= 0 || typeof p.target !== 'string' || /[\s\\?#]/.test(p.target)) return null;
    const base = safeURL(p.target); if (!base) return null;
    const u = new URL(base); u.pathname = u.pathname.replace(/\/+$/, '') + '/wp-admin/post.php';
    u.search = `post=${p.remote_id}&action=edit`; return u.href;
  }
  const targetLabel = () => safeURL(state.connections?.wordpress_url) || '설정된 WordPress (사이트 주소 미제공, WP_URL 직접 확인 필요)';
  const names = ['provider','name','source_url','affiliate_url','price','observed_at','facts','audience','caveats','ownership_confirmed'];
  const field = name => $('product-form').elements.namedItem(name);
  const idPath = id => encodeURIComponent(String(id));
  const states = {review:'검토 필요',approved:'승인됨',pending:'대기 중',completed:'완료',failed:'실패',cancelled:'취소됨',uncertain:'결과 불명'};
  const dirty = () => Boolean(detail) && ($('draft-title').value !== detail.draft.title || $('draft-body').value !== detail.draft.body || $('body-format').value !== (detail.draft.body_format || 'plain'));
  const hasErrors = () => Boolean(detail?.checks?.errors?.length);
  function message(text, error = false) { $('notice').textContent = text; $('notice').className = error ? 'notice error' : 'notice'; $('notice').hidden = !text; }
  function node(tag, text, className) { const el = document.createElement(tag); if (text != null) el.textContent = String(text); if (className) el.className = className; return el; }
  function button(text, callback, attr, gate = () => true) { const el = node('button',text,'small'); el.type='button'; el.dataset.action='true'; if (attr) el.setAttribute(attr,''); gates.set(el,gate); el.addEventListener('click',()=>{if (!el.disabled && !busy && gate()) callback();}); return el; }
  function dateText(value) { if (!value) return '미확인'; const date = new Date(value); return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('ko-KR'); }
  function localDate(value) { if (!value) return ''; const d = new Date(value); if (Number.isNaN(d.getTime())) return ''; return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16); }
  function iso(value) { const d = new Date(value); if (!value || Number.isNaN(d.getTime())) throw new Error('유효한 날짜와 시각 입력 필요'); return d.toISOString(); }
  function show(view) {
    for (const section of document.querySelectorAll('main > section')) section.hidden = section.id !== `view-${view}`;
    for (const item of document.querySelectorAll('nav [data-view]')) { if(item.dataset.view===view) item.setAttribute('aria-current','page'); else item.removeAttribute('aria-current'); }
  }
  async function api(url, method='GET', data) {
    const options = {method, credentials:'same-origin', cache:'no-store', headers:{}};
    if (method !== 'GET') {
      if (!state.csrf) throw new Error('서버 연결 확인 후 재시도 필요');
      options.headers = {'Content-Type':'application/json','X-CSRF-Token':state.csrf}; options.body = JSON.stringify(data);
    }
    let response;
    try { response = await fetch(url,options); } catch (_) { throw new Error('서버 연결 실패, 서버 실행 상태와 네트워크 확인 필요'); }
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(payload.error || payload.detail || `요청 실패 (${response.status})`); error.status = response.status; throw error; }
    return payload;
  }
  function sync() {
    for (const el of document.querySelectorAll('[data-action]')) el.disabled = busy || !(gates.get(el)?.() ?? true);
    const edited = dirty();
    const locked = draftLocked();
    const actions = actionState(detail?.draft, edited || stale || productDirty, $('approval-confirm').checked, state.connections?.wordpress, busy, hasErrors(), locked);
    for (const name of ['approve','download','schedule','wordpress']) $(name).disabled = !actions[name];
    $('download-text').disabled = !actions.download;
    $('product-fields').disabled = busy || !state.csrf || productLocked(productId);
    $('draft-fields').disabled = busy || !detail || locked || stale;
    $('save-draft').disabled = busy || !detail || !edited || stale || locked;
    for (const el of document.querySelectorAll('[data-format]')) el.disabled = busy || !detail || stale || locked || $('body-format').value !== 'markdown-v1';
    $('approval-confirm').disabled = busy || !detail || edited || stale || productDirty || locked || hasErrors() || detail?.draft.status === 'approved';
    $('run-at').disabled = busy || !actions.schedule;
    $('reload-draft').disabled = busy || !detail;
    $('refresh').disabled = busy; $('refresh-jobs').disabled = busy; $('new-product').disabled = busy;
    $('ai-check').disabled = busy || !state.csrf || !state.connections?.ai;
    $('ai-style').disabled = !canAI(); $('confirmed-send').disabled = !canAI();
    $('ai-generate').disabled = !canAI() || !state.connections?.ai || !aiCheck?.available || !$('confirmed-send').checked;
    const cancelledVersion = (state.publications || []).some(p => p.draft_id === detail?.draft.id && p.version === detail?.draft.version && p.state === 'cancelled');
    $('publish-confirm').disabled = !actions.wordpress || cancelledVersion; $('publish-run-at').disabled = !actions.wordpress || cancelledVersion;
    $('publish-schedule').disabled = !actions.wordpress || cancelledVersion || !$('publish-confirm').checked;
    $('publish-target').textContent = `대상 사이트: ${targetLabel()} · 콘텐츠: ${detail?.draft.title || '선택 없음'} · 버전 ${detail?.draft.version || '-'}`;
    $('publish-timezone').textContent = `입력 시간대: ${Intl.DateTimeFormat().resolvedOptions().timeZone} · 확인 창에 현지 시각과 UTC 표시`;
    $('ai-status').textContent = !state.connections?.ai ? `로컬 AI 미설정 · 시작하기에서 설정 방법 확인 ${state.connections?.ai_error || ''}` : aiCheck ? (aiCheck.available ? `연결 확인됨 · ${aiCheck.model || state.connections.ai_model} · ${dateText(aiCheck.checked_at)} (마지막 조회 결과)` : `연결 확인 실패 · ${aiCheck.error || '로컬 서버와 모델 확인 필요'}`) : `설정됨 · 연결 미확인 · 모델 ${state.connections.ai_model || '미확인'}`;
    $('dirty-status').textContent = locked ? '공개 예약/전송 잠금 · WordPress에서 예약 관리 후 앱의 원격 상태 조회 필요' : stale ? '서버 버전 충돌 · 저장본 다시 불러오기 필요' : edited ? '저장하지 않은 수정 · 승인과 내보내기 잠금' : detail ? `저장된 버전 ${detail.draft.version}` : '';
    $('wordpress-note').textContent = state.connections?.wordpress ? 'WordPress 설정됨 · 초안 전송은 공개 게시 아님, 실제 공개는 아래 별도 공개 게시 예약으로만 요청' : 'WordPress 미연결 · 서버 환경 변수 설정 필요, 시작하기 참고';
  }
  async function run(task, success) {
    if (busy) return;
    busy=true; message('처리 중'); sync();
    try { const result=await task(); if(success) message(typeof success==='function' ? success(result) : success); else message(''); }
    catch(error) {
      if (error.status===409) { stale=true; $('approval-confirm').checked=false; }
      // A failed external mutation can still have reached the remote service.
      // Only re-read local backend state; never automatically repeat its POST.
      try { await refreshState(); } catch(_) {}
      message(`${error.message}${error.status===409 ? '\n다른 변경 감지, 서버 저장본을 다시 불러와 검토 필요 (현재 입력 유지)' : ''}`,true);
    } finally { busy=false; sync(); }
  }
  function renderProducts() {
    const list=$('product-list'); list.replaceChildren(); $('product-count').textContent=state.products.length;
    if (!state.products.length) list.append(node('p','등록 상품 없음, 상품 정보 입력 후 저장','empty'));
    for (const p of state.products) {
      const card=node('article',null,'record'); card.append(node('h3',p.name),node('p',`${p.provider==='toss'?'토스 쇼핑':'쿠팡 파트너스'} · ${p.price == null ? '가격 미입력' : Number(p.price).toLocaleString('ko-KR')+'원'}`,'meta'));
      const actions=node('div',null,'actions');
      actions.append(button('상품 수정',()=>{if (canLeaveProduct()) fillProduct(p);},'data-edit-product',()=>!productLocked(p.id)),button('초안 만들기',()=>{
        if (productDirty) { message('저장하지 않은 상품 정보 있음, 먼저 저장하거나 새 상품 버튼으로 취소 필요',true); return; }
        if (!canLeaveDraft()) return;
        run(async()=>{const d=await api('/api/drafts','POST',{product_id:p.id}); await refreshState(); await loadDraft(d.id); show('content');},'템플릿 초안 생성 완료, 사실과 경고 직접 검토 필요');
      },'data-generate',()=>!productLocked(p.id))); card.append(actions); list.append(card);
    }
  }
  function canLeaveDraft() { return !dirty() || window.confirm('저장하지 않은 콘텐츠 수정을 버리고 계속할까요?'); }
  function canLeaveProduct() { return !productDirty || window.confirm('저장하지 않은 상품 정보 수정을 버릴까요?'); }
  function fillProduct(p) {
    $('product-form').reset(); productId=p?.id ?? null; productVersion=p?.version ?? null;
    if (p) for (const name of names) { if(name==='ownership_confirmed') field(name).checked=p[name]===true; else if(name==='facts') field(name).value=(p[name]||[]).join('\n'); else if(name==='observed_at') field(name).value=localDate(p[name]); else field(name).value=p[name]??''; }
    productDirty=false; $('product-form-title').textContent=p?'상품 수정':'상품 등록'; $('save-product').textContent=p?'변경 저장':'상품 저장';
    $('product-edit-note').textContent=p?'상품 수정 시 연결된 콘텐츠 승인 해제':'저장 후 템플릿 초안 생성 가능';
    sync();
  }
  function renderDrafts() {
    $('draft-list').replaceChildren(); $('draft-count').textContent=state.drafts.length;
    if (!state.drafts.length) $('draft-list').append(node('p','저장된 콘텐츠 없음','muted'));
    for (const d of state.drafts) {
      const item=button(`${d.title} · ${states[d.status]||d.status}`,()=>{if(canLeaveDraft()) run(()=>loadDraft(d.id));},'data-select-draft');
      item.setAttribute('aria-pressed',String(detail?.draft.id===d.id)); $('draft-list').append(item);
    }
  }
  function renderJobs() {
    $('job-list').replaceChildren();
    $('worker-status').textContent=state.worker_running?'작업 실행기 동작 중 · 서버 실행 중에만 대기 작업 처리':'작업 실행기 중지 · 대기 작업 실행 안 됨, 서버 상태 확인 필요';
    if (!state.jobs.length) $('job-list').append(node('div','예약된 작업 없음, 승인한 콘텐츠에서 로컬 패키지 생성 예약 가능','card empty'));
    for (const j of state.jobs) {
      const card=node('article',null,'card job-card'), info=node('div'), actions=node('div',null,'actions');
      const draft=state.drafts.find(d=>d.id===j.draft_id);
      info.append(node('h3',draft?.title || `콘텐츠 #${j.draft_id}`),node('span',states[j.state]||j.state,`badge ${j.state}`),node('p',`${dateText(j.run_at)} · 버전 ${j.version}`,'meta'));
      if(j.error) info.append(node('p',j.error,'job-error'));
      if(j.state==='uncertain') info.append(node('p','결과 확인 후 조치 필요, 자동 재시도 안 함','job-error'));
      if(j.state==='pending') actions.append(button('예약 취소',()=>run(async()=>{await api(`/api/jobs/${idPath(j.id)}/cancel`,'POST',{});await refreshState();if(detail&&!dirty())await loadDraft(detail.draft.id);},'예약 취소 완료')));
      if(j.state==='completed') actions.append(button('ZIP 다운로드',()=>run(()=>download(`/api/jobs/${idPath(j.id)}/download`,`package-${j.id}.zip`),'패키지 다운로드 완료')));
      card.append(info,actions); $('job-list').append(card);
    }
  }
  async function refreshState() {
    const next=await api('/api/state');
    if (state.connections?.ai_model !== next.connections?.ai_model || state.connections?.ai !== next.connections?.ai) aiCheck=null;
    state=next; state.products ||= []; state.drafts ||= []; state.jobs ||= [];
    state.publications ||= [];
    if (detail && state.drafts.some(d => d.id === detail.draft.id && d.version !== detail.draft.version)) stale = true;
    if (detail) {
      const latest=await api(`/api/drafts/${idPath(detail.draft.id)}`);
      if(latest.draft.version !== detail.draft.version) stale=true;
      detail.publication_lock=latest.publication_lock; detail.checks=latest.checks;
      if(!stale) { detail.draft.status=latest.draft.status; detail.candidates=latest.candidates; }
      renderCandidates(); renderReview();
    }
    $('connection-status').textContent=`서버 연결됨 · 상품 ${state.products.length} · 콘텐츠 ${state.drafts.length}`;
    renderProducts();renderDrafts();renderJobs();renderPublications();sync();
  }
  async function loadDraft(id) {
    const next=await api(`/api/drafts/${idPath(id)}`);
    detail=next; stale=false; $('body-format').value=detail.draft.body_format || 'plain'; $('draft-title').value=detail.draft.title; $('draft-body').value=detail.draft.body; $('approval-confirm').checked=false;
    $('content-empty').hidden=true; $('draft-workspace').hidden=false;
    $('preview-frame').srcdoc=detail.preview?.document || detail.preview?.html || '';
    for(const name of ['text','telegram','prompt']) $('preview-'+name).textContent=detail.preview?.[name]||'';
    $('product-facts').replaceChildren();
    const p=detail.product;
    for (const [key,value] of [['상품',p.name],['가격',p.price == null?'미확인':Number(p.price).toLocaleString('ko-KR')+'원'],['정보 확인 시각',dateText(p.observed_at)],['사실',(p.facts||[]).join('\n')],['출처',p.source_url],['제휴 URL',p.affiliate_url],['주의사항',p.caveats||'입력 없음']]) $('product-facts').append(node('dt',key),node('dd',value));
    renderReview();
    $('confirmed-send').checked=false; $('publish-confirm').checked=false; renderCandidates(); renderDrafts();sync();
  }
  function renderReview() {
    $('draft-status').textContent=(stale ? '이전 저장본 · ' : '')+(states[detail.draft.status]||detail.draft.status);
    $('draft-status').className=`badge ${detail.draft.status}`;
    $('checks').replaceChildren();
    for(const [type,label] of [['errors','오류'],['warnings','주의']]) for(const check of detail.checks?.[type]||[]) $('checks').append(node('p',`${label}: ${typeof check==='string'?check:JSON.stringify(check)}`,type==='errors'?'check-error':'check-warning'));
    if(!hasErrors()) $('checks').append(node('p','차단 오류 없음 · 자동 검사와 별도로 사실 확인과 정책 검토 필요','muted'));
  }
  function renderPublications() {
    $('publication-list').replaceChildren();
    if (!state.publications.length) $('publication-list').append(node('p','WordPress 공개 예약 없음, 콘텐츠 승인과 원격 초안 전송 후 공개 예약 가능','card empty'));
    for (const p of state.publications) {
      const card=node('article',null,'card job-card'), info=node('div'), actions=node('div',null,'actions');
      const title=state.drafts.find(d=>d.id===p.draft_id)?.title || `콘텐츠 #${p.draft_id}`;
      info.append(node('h3',title),node('span',publicationStates[p.state] || '알 수 없는 상태 · 확인 필요',`badge ${Object.hasOwn(publicationStates,p.state)?p.state:'uncertain'}`),node('p',`사이트: ${safeURL(p.target) || '주소 확인 필요'} · 버전 ${p.version}`,'meta'),node('p',`예정: ${dateText(p.run_at)} · 마지막 원격 조회: ${dateText(p.checked_at)}`,'meta'));
      if(p.state==='future' && new Date(p.run_at).getTime() < Date.now())info.append(node('p','예정 시각 지남, 아직 공개 미확인 · 원격 상태 조회 필요','check-warning'));
      if(p.error)info.append(node('p',p.error,'job-error'));
      const href=p.checked_at && ['future','publish','cancelled'].includes(p.state) && safeURL(p.target) && safeURL(p.remote_url,p.target);
      if(href){const link=node('a','확인된 원격 글 열기');link.href=href;link.target='_blank';link.rel='noopener noreferrer';actions.append(link);}
      actions.append(button('원격 상태 조회',()=>run(async()=>{const result=await publicationRequest(p,'refresh',{});if(!['future','publish','cancelled'].includes(result.state))throw new Error(result.error || '원격 결과 미확정 · 자동 재시도 안 함');return result;},result=>`원격 조회: ${publicationStates[result.state]} · ${dateText(result.checked_at)}`),'data-refresh-publication'));
      if (['future','uncertain'].includes(p.state)) {
        const admin = adminURL(p);
        if (admin) { const link=node('a','WordPress에서 예약 관리'); link.href=admin; link.target='_blank'; link.rel='noopener noreferrer'; link.setAttribute('data-manage-publication',''); actions.append(link); }
        info.append(node('p','앱 직접 취소 미지원, WordPress 관리자에서 현재 상태 확인 후 예약 글을 초안으로 변경하고 앱의 원격 상태 조회로 확인 필요 (이미 공개된 글은 앱에서 취소·비공개 전환 불가)','field-help'));
      }
      if (p.state === 'cancelled') info.append(node('p','취소된 동일 버전은 재예약 불가: 취소 확인 후 본문 또는 제목을 수정·저장하고 재승인한 뒤 새 원격 초안 전송과 새 예약 진행','field-help'));
      card.append(info,actions);$('publication-list').append(card);
    }
  }
  async function publicationRequest(p,action,data) {
    const result=await api(`/api/publications/${idPath(p.id)}/${action}`,'POST',data);
    rememberPublication(result); await refreshState(); return result;
  }
  function rememberPublication(p) {
    state.publications=[...(state.publications || []).filter(old=>old.id!==p.id),p];
    renderPublications();sync();
  }
  function renderCandidates() {
    $('candidate-list').replaceChildren();
    if (!detail?.candidates?.length) $('candidate-list').append(node('p','생성된 후보 없음, 연결 확인과 전송 동의 후 생성 가능','muted'));
    for (const c of detail?.candidates || []) {
      const current = c.source_version === detail.draft.version && c.product_version === detail.product.version;
      const card=node('article',null,'candidate-card'), comparison=node('div',null,'candidate-comparison');
      card.append(node('h3',`후보 · ${c.model || '로컬 모델'}`),node('p',`원본 v${c.source_version} · ${dateText(c.created_at)}${current ? '' : ' · 오래된 후보: 적용 불가'}`,'meta'));
      for(const [label,title,body] of [['현재 저장본',detail.draft.title,detail.draft.body],['AI 후보',c.title,c.body]]) { const pane=node('section'); pane.append(node('h4',label),node('strong',title),node('pre',body)); comparison.append(pane); }
      card.append(comparison,node('p',`참조한 사실 번호: ${(c.evidence_refs || []).join(', ') || '없음'} · 직접 대조 필요`,'field-help'));
      for (const warning of c.warnings || []) card.append(node('p',warning,'check-warning'));
      card.append(button('이 후보 적용 · 승인 해제',()=>run(async()=>{await api(`/api/drafts/${idPath(detail.draft.id)}/candidates/${idPath(c.id)}/apply`,'POST',{version:detail.draft.version});await refreshDetailAndState();},'후보 적용 완료, 사실과 고지 검토 후 현재 버전 재승인 필요'),'data-apply-candidate',()=>current && canAI()));
      $('candidate-list').append(card);
    }
  }
  async function download(url, filename) {
    const res=await fetch(url,{credentials:'same-origin',cache:'no-store'});
    if(!res.ok) { const p=await res.json().catch(()=>({})); const error=new Error(p.error||`다운로드 실패 (${res.status})`);error.status=res.status;throw error; }
    const blob=await res.blob(), objectURL=URL.createObjectURL(blob), a=node('a'); a.href=objectURL; a.download=filename; document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(objectURL),1000);
  }
  async function refreshDetailAndState() { await refreshState(); if(detail) await loadDraft(detail.draft.id); }
  document.querySelectorAll('[data-view]').forEach(el=>el.addEventListener('click',()=>show(el.dataset.view)));
  $('refresh').addEventListener('click',()=>run(refreshState,'상태 갱신 완료, 콘텐츠 편집 내용 유지'));
  $('refresh-jobs').addEventListener('click',()=>run(refreshState,'예약 상태 갱신 완료'));
  $('new-product').addEventListener('click',()=>{if(canLeaveProduct()) fillProduct(null);});
  $('product-form').addEventListener('input',()=>{productDirty=true;sync();});
  $('product-form').addEventListener('change',()=>{productDirty=true;sync();});
  $('product-form').addEventListener('submit',event=>{
    event.preventDefault(); if(busy || productLocked(productId) || !$('product-form').reportValidity())return;
    if(detail && !canLeaveDraft())return;
    run(async()=>{
      const data={};for(const name of names) data[name]=field(name).value.trim();
      data.price=data.price===''?null:Number(data.price);
      if(data.price!==null && (!Number.isSafeInteger(data.price)||data.price<=0))throw new Error('가격은 양의 정수로 입력, 미확인 시 비워두기');
      data.observed_at=iso(data.observed_at);data.facts=data.facts.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);data.ownership_confirmed=field('ownership_confirmed').checked===true;
      if(!data.facts.length)throw new Error('확인한 사실 한 줄 이상 입력 필요');
      if(productId!==null)data.version=productVersion;
      const p=await api(productId===null?'/api/products':`/api/products/${idPath(productId)}`,productId===null?'POST':'PUT',data);
      fillProduct(p);await refreshDetailAndState();
    },'상품 저장 완료, 상품 목록에서 초안 생성 가능');
  });
  const onEdit=()=>{$('approval-confirm').checked=false;$('publish-confirm').checked=false;$('confirmed-send').checked=false;sync();};
  $('draft-title').addEventListener('input',onEdit);$('draft-body').addEventListener('input',onEdit);
  $('body-format').addEventListener('change',onEdit);
  document.querySelectorAll('[data-format]').forEach(button=>button.addEventListener('click',()=>{
    if(button.disabled || busy || !detail || stale || draftLocked() || $('body-format').value!=='markdown-v1')return;
    const input=$('draft-body'),value=input.value,type=button.dataset.format;
    let start=input.selectionStart,end=input.selectionEnd,selected=value.slice(start,end),insert;
    if(type==='bold')insert=`**${selected || '강조할 내용'}**`;
    else if(type==='table') {
      // Add a block after the selected line; never replace selected evidence.
      const edge=end>start && value[end-1]==='\n'?end-1:end;
      const next=value.indexOf('\n',edge);start=end=next<0?value.length:next;
      const cell=s=>String(s).replace(/\\/g,'\\\\').replace(/[|*#>]/g,'\\$&').replace(/\r?\n/g,' ');
      insert='\n\n| 항목 | 확인한 내용 |\n| --- | --- |\n'+detail.product.facts.map((f,i)=>`| 근거 ${i+1} | ${cell(f)} |`).join('\n')+'\n\n';
    } else {
      start=start===0?0:value.lastIndexOf('\n',start-1)+1;
      const edge=end>start && value[end-1]==='\n'?end-1:end;
      const next=value.indexOf('\n',edge);end=next<0?value.length:next;
      selected=value.slice(start,end);
      const prefix={h2:'## ',h3:'### ',list:'- ',quote:'> '};
      insert=selected.split('\n').map((line,i)=>{
        const content=type==='h2'||type==='h3'?line.replace(/^(?:#{2,3} )+/,''):line;
        return (type==='ordered'?`${i+1}. `:prefix[type])+content;
      }).join('\n');
    }
    if(insert===value.slice(start,end))return;
    if(value.length-(end-start)+insert.length>20000){message('본문은 서식 기호 포함 20,000자까지 입력 가능',true);return;}
    // Native insertion is needed for the browser's undo history. setRangeText
    // edits the value but can leave Ctrl+Z replaying unrelated old changes.
    const previousSelection=[input.selectionStart,input.selectionEnd,input.selectionDirection];
    input.focus();input.setSelectionRange(start,end);
    let inserted=false;
    try {inserted=typeof document.execCommand==='function' && document.execCommand('insertText',false,insert);}catch{}
    if(!inserted){input.setSelectionRange(...previousSelection);message('이 브라우저는 서식 버튼의 실행 취소 미지원, 본문에 서식 문법 직접 입력 필요',true);return;}
    input.setSelectionRange(start,start+insert.length);onEdit();
  }));
  $('approval-confirm').addEventListener('change',sync);
  $('reload-draft').addEventListener('click',()=>{if(canLeaveDraft())run(()=>loadDraft(detail.draft.id),'서버 저장본 불러오기 완료, 재검토 필요');});
  $('draft-form').addEventListener('submit',event=>{
    event.preventDefault();if(!detail||!dirty()||stale||draftLocked()||busy||!$('draft-form').reportValidity())return;
    run(async()=>{await api(`/api/drafts/${idPath(detail.draft.id)}`,'PUT',{version:detail.draft.version,title:$('draft-title').value,body:$('draft-body').value,body_format:$('body-format').value});await refreshDetailAndState();},'수정본 저장 완료, 기존 승인 해제로 재검토 필요');
  });
  $('approve').addEventListener('click',()=>{if($('approve').disabled)return;run(async()=>{await api(`/api/drafts/${idPath(detail.draft.id)}/approve`,'POST',{version:detail.draft.version,confirmed:true});await refreshDetailAndState();},'현재 저장본 승인 완료');});
  $('download').addEventListener('click',()=>{if($('download').disabled)return;run(async()=>{await download(`/api/drafts/${idPath(detail.draft.id)}/download?version=${detail.draft.version}`,`content-${detail.draft.id}.zip`);await refreshDetailAndState();},'승인본 패키지 다운로드 완료');});
  $('download-text').addEventListener('click',()=>{if($('download-text').disabled)return;run(()=>download(`/api/drafts/${idPath(detail.draft.id)}/text?version=${detail.draft.version}`,`content-${detail.draft.id}-v${detail.draft.version}.txt`),'광고 고지와 링크를 포함한 승인본문 다운로드 완료, 게시 채널 허용 조건 직접 확인 필요');});
  $('schedule-form').addEventListener('submit',event=>{
    event.preventDefault();if($('schedule').disabled||!$('schedule-form').reportValidity())return;
    run(async()=>{const run_at=iso($('run-at').value);if(new Date(run_at).getTime()<=Date.now())throw new Error('예약 시각은 현재 이후로 입력 필요');await api(`/api/drafts/${idPath(detail.draft.id)}/schedule`,'POST',{version:detail.draft.version,run_at});await refreshDetailAndState();show('jobs');},'로컬 패키지 생성 예약 완료, 외부 게시 아님');
  });
  $('wordpress').addEventListener('click',()=>{
    if($('wordpress').disabled || !window.confirm('승인본을 외부 WordPress 서버로 전송할까요? 원격 초안만 생성, 공개 게시 안 함'))return;
    run(async()=>{const result=await api(`/api/drafts/${idPath(detail.draft.id)}/wordpress`,'POST',{version:detail.draft.version,confirmed:true});if(result.state!=='completed')throw new Error(result.error||'전송 결과 미확정: WordPress 관리자에서 확인 필요, 자동 재전송 안 함');await refreshDetailAndState();},'WordPress 원격 초안 확인 완료, 공개 게시 아님 · WordPress에서 결과 확인 필요');
  });
  $('confirmed-send').addEventListener('change',sync);
  $('publish-confirm').addEventListener('change',sync);
  $('publish-run-at').addEventListener('input',()=>{$('publish-confirm').checked=false;sync();});
  $('publish-form').addEventListener('submit',event=>{
    event.preventDefault();if($('publish-schedule').disabled || !$('publish-form').reportValidity())return;
    let run_at;
    try {
      run_at=iso($('publish-run-at').value);const due=new Date(run_at).getTime();
      if(due < Date.now()+5*60000 || due > Date.now()+30*86400000)throw new Error('공개 예약은 현재부터 5분 이후, 30일 이내로 입력 필요');
      if(detail.product.price != null && due > new Date(detail.product.observed_at).getTime()+86400000)throw new Error('가격 정보 확인 시각부터 24시간 이내로 예약하거나 상품 가격 정보 재검토 필요');
    }catch(error){message(error.message,true);return;}
    if(!window.confirm(`실제 WordPress 공개 게시 예약\n콘텐츠: ${detail.draft.title}\n승인 버전: ${detail.draft.version}\n대상: ${targetLabel()}\n현지: ${dateText(run_at)} (${Intl.DateTimeFormat().resolvedOptions().timeZone})\nUTC: ${run_at}\n앱 종료 후에도 예약 유지, 계속할까요?`))return;
    run(async()=>{const result=await api(`/api/drafts/${idPath(detail.draft.id)}/publish-schedule`,'POST',{version:detail.draft.version,run_at,confirmed_publish:true});rememberPublication(result);$('publish-confirm').checked=false;await refreshState();show('jobs');if(!['future','publish'].includes(result.state))throw new Error(result.error || '공개 예약 결과 불명 · 자동 재시도 금지, 원격 상태 조회 필요');return result;},result=>`${publicationStates[result.state]} · WordPress 원격 조회 결과, 정시 발행은 WordPress 운영 상태에 따라 달라짐`);
  });
  $('ai-check').addEventListener('click',()=>{if($('ai-check').disabled)return;run(async()=>{aiCheck={available:false,error:'연결 확인 실패, 서버 상태 확인 후 다시 조회 필요'};aiCheck={...await api('/api/ai/check','POST',{}),checked_at:new Date().toISOString()};if(!aiCheck.available)throw new Error(aiCheck.error || '로컬 AI 연결 확인 실패');},'로컬 AI 모델 연결 확인 완료, 생성은 별도 동의 후 실행');});
  $('ai-generate').addEventListener('click',()=>{if($('ai-generate').disabled)return;run(async()=>{await api(`/api/drafts/${idPath(detail.draft.id)}/ai`,'POST',{version:detail.draft.version,style:$('ai-style').value,confirmed_send:true});await refreshDetailAndState();},'후보 생성 완료, 저장본 유지 · 비교 후 직접 적용');});
  window.addEventListener('beforeunload',event=>{if(dirty()||productDirty){event.preventDefault();event.returnValue='';}});
  run(refreshState);
})();
