'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const form = $('automation-form');
  const field = name => form.elements.namedItem(name);
  const node = (tag, text, cls) => {
    const el = document.createElement(tag);
    if (text != null) el.textContent = String(text);
    if (cls) el.className = cls;
    return el;
  };
  const stateLabels = {awaiting_info: '설명 대기', ready: '발행 대기', composing: '작성 중',
    checking: '검사 중', draft: '초안 확인', draft_sending: '전송 중', scheduled: '예약 확인',
    published: '발행 확인', failed: '실패', uncertain: '결과 불명'};
  const providerLabels = {coupang: '쿠팡', toss: '토스'};
  const countOrder = ['ready', 'awaiting_info', 'composing', 'checking', 'scheduled', 'published', 'failed', 'uncertain'];
  let csrf = '', state = null, detail = null, busy = false, dirty = false, version = null;

  function notice(text, error = false) {
    const el = $('automation-notice');
    el.textContent = text; el.hidden = !text;
    el.className = error ? 'notice error' : 'notice';
  }
  async function api(url, method = 'GET', data) {
    const options = {method, credentials: 'same-origin', cache: 'no-store', headers: {}};
    if (method !== 'GET') {
      if (!csrf) throw new Error('서버 연결 확인 후 재시도 필요');
      options.headers = {'Content-Type': 'application/json', 'X-CSRF-Token': csrf};
      options.body = JSON.stringify(data);
    }
    let response;
    try { response = await fetch(url, options); }
    catch (_) { throw new Error('서버 연결 실패, 서버 상태와 네트워크 확인 필요'); }
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(payload.error || `요청 실패 (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return payload;
  }
  function safeURL(value) {
    try {
      const url = new URL(value);
      return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
    } catch (_) { return null; }
  }
  function dateText(value) {
    if (!value) return '미확인';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('ko-KR');
  }
  function daysText(days) {
    const names = ['월', '화', '수', '목', '금', '토', '일'];
    return days.map(day => names[day]).join('·');
  }

  function fill(settings) {
    if (!settings) return;
    version = settings.version;
    field('automation_enabled').checked = settings.enabled === true;
    for (const [name, key] of [['automation_topic', 'topic'], ['automation_audience', 'audience'], ['automation_tone', 'tone'], ['automation_purpose', 'purpose']]) field(name).value = settings.writing[key] || '';
    field('automation_extra').value = (settings.writing.extra_instructions || []).join('\n');
    field('coupang_limit').value = settings.schedule.daily_limits.coupang;
    field('toss_limit').value = settings.schedule.daily_limits.toss;
    field('automation_start').value = settings.schedule.start;
    field('automation_end').value = settings.schedule.end;
    field('automation_timezone').value = settings.schedule.timezone;
    for (const option of field('automation_weekdays').options) option.selected = settings.schedule.weekdays.includes(Number(option.value));
    $('automation-applied').textContent = settings.pending_schedule
      ? `일정 변경 적용: ${dateText(settings.pending_schedule.effective_at)}`
      : '';
    dirty = false;
    sync();
  }
  function collect() {
    return {
      version,
      enabled: field('automation_enabled').checked === true,
      writing: {
        topic: field('automation_topic').value.trim(),
        audience: field('automation_audience').value.trim(),
        tone: field('automation_tone').value.trim(),
        purpose: field('automation_purpose').value.trim(),
        extra_instructions: field('automation_extra').value.split(/\r?\n/).map(s => s.trim()).filter(Boolean),
        category_instructions: version != null && state?.settings?.writing?.category_instructions || {},
      },
      schedule: {
        timezone: field('automation_timezone').value.trim(),
        weekdays: [...field('automation_weekdays').options].filter(option => option.selected).map(option => Number(option.value)),
        start: field('automation_start').value,
        end: field('automation_end').value,
        daily_limits: {coupang: Number(field('coupang_limit').value), toss: Number(field('toss_limit').value)},
      },
    };
  }
  function sync() {
    const connected = state?.connections || {};
    $('automation-fields').disabled = busy || !csrf;
    $('automation-save').disabled = busy || !csrf || !dirty;
    $('automation-refresh').disabled = busy;
    const all = connected.telegram && connected.ai && connected.wordpress;
    $('automation-status').textContent = busy ? '처리 중' : all
      ? `연결됨 · Telegram·AI·WordPress · ${connected.running ? '작업자 동작 중' : '작업자 중지'} · 제휴 링크 생성 API 키는 필요 없음${connected.last_error ? ' · 최근 오류: ' + connected.last_error : ''}`
      : `연결 필요: ${[['telegram', 'Telegram'], ['ai', 'AI'], ['wordpress', 'WordPress']].filter(([key]) => !connected[key]).map(([, label]) => label).join(', ')} · 제휴 링크 생성 API 키는 필요 없음${connected.last_error ? ' · ' + connected.last_error : ''}`;
    const slots = state?.next_slots || [];
    $('automation-next').textContent = slots.length ? `다음 발행: ${dateText(slots[0].run_at)} (${providerLabels[slots[0].provider]}) · 남은 슬롯 ${slots.length}` : '오늘 남은 발행 슬롯 없음';
  }
  function renderCounts() {
    const counts = state?.counts || {};
    const box = $('automation-counts'); box.replaceChildren();
    for (const provider of ['coupang', 'toss']) {
      const card = node('div', null, 'automation-count-card');
      card.append(node('h3', providerLabels[provider]));
      const values = counts[provider] || {};
      card.append(node('p', countOrder.map(key => `${stateLabels[key]} ${values[key] || 0}`).join(' · '), 'meta'));
      box.append(card);
    }
  }
  function renderRequests() {
    const list = $('automation-requests'); list.replaceChildren();
    const rows = state?.requests || [];
    $('automation-count').textContent = rows.length;
    if (!rows.length) list.append(node('p', '접수된 링크 없음, Telegram으로 본인 제휴 링크 전송', 'card empty'));
    for (const row of rows) {
      const card = node('button', null, 'automation-request');
      card.type = 'button';
      card.setAttribute('data-automation-request', row.id);
      card.setAttribute('aria-pressed', String(detail?.request?.id === row.id));
      card.append(node('strong', row.evidence?.name || '상품 정보 확인 필요'),
                  node('span', `${providerLabels[row.provider] || row.provider} · ${stateLabels[row.state] || row.state}`, 'badge ' + row.state),
                  node('small', `${dateText(row.created_at)}${row.error ? ' · ' + row.error : ''}`));
      card.addEventListener('click', () => select(row.id));
      list.append(card);
    }
  }
  function renderDetail() {
    const box = $('automation-detail'); box.replaceChildren();
    const preview = $('automation-preview');
    preview.hidden = !detail;
    if (!detail) { box.append(node('p', '요청을 선택하면 근거와 발행 결과 표시', 'empty')); preview.srcdoc = ''; return; }
    const request = detail.request, publication = detail.publications?.[0];
    const products = request.products || (request.affiliate_url ? [{
      index: 1, provider: request.provider, state: request.state,
      affiliate_url: request.affiliate_url, evidence: request.evidence || {}}] : []);
    box.append(node('h3', products.map(item => item.evidence?.name || '상품 정보 확인 필요').join(' · ')));
    box.append(node('p', `쿠팡·토스 구분: ${providerLabels[request.provider] || request.provider} · 상태: ${stateLabels[request.state] || request.state}`, 'meta'));
    const link = safeURL(request.affiliate_url);
    if (link) {
      const anchor = node('a', '보낸 제휴 링크 열기');
      anchor.href = link; anchor.target = '_blank'; anchor.rel = 'noopener noreferrer';
      box.append(anchor);
    }
    if (products.length) {
      const list = node('div', null, 'automation-products');
      list.id = 'automation-products';
      products.forEach(item => {
        const card = node('article', null, 'automation-product');
        card.setAttribute('data-product-index', String(item.index));
        card.append(node('strong', item.evidence?.name || '상품 정보 확인 필요'),
                    node('span', `${providerLabels[item.provider] || item.provider} · ${stateLabels[item.state] || item.state}`, 'badge ' + item.state));
        if (item.evidence?.facts?.length) {
          const facts = node('ul');
          for (const fact of item.evidence.facts) facts.append(node('li', `${fact.text} (${fact.source === 'user' ? '사용자 제공' : '페이지 확인'})`));
          card.append(facts);
        }
        const itemLink = safeURL(item.affiliate_url);
        if (itemLink) {
          const anchor = node('a', '제휴 링크 열기');
          anchor.href = itemLink; anchor.target = '_blank'; anchor.rel = 'noopener noreferrer';
          card.append(anchor);
        }
        list.append(card);
      });
      box.append(list);
    }
    if (request.reason) box.append(node('p', `안내: ${request.reason}`, 'field-help'));
    if (detail.images) {
      const boxImages = node('div', null, 'automation-images');
      boxImages.id = 'automation-images';
      const labels = {featured: '대표 이미지', body: '본문 이미지'};
      for (const kind of ['featured', 'body']) {
        const item = detail.images[kind] || {state: 'unavailable', error: '없음'};
        const card = node('div', null, 'automation-image');
        card.setAttribute('data-image-kind', kind);
        card.append(node('strong', `${labels[kind]}: ${item.state === 'uploaded' ? '포함' : '없음'}`));
        if (item.state === 'uploaded') {
          const img = node('img');
          img.alt = labels[kind];
          img.loading = 'lazy';
          img.src = `/api/automation/requests/${encodeURIComponent(request.id)}/media/${kind}`;
          card.append(img);
        } else if (item.error) {
          card.append(node('small', item.error, 'field-help'));
        }
        boxImages.append(card);
      }
      if (detail.media_usage) {
        const kb = Math.round(detail.media_usage.bytes / 1024);
        boxImages.append(node('p', `저장 이미지 ${detail.media_usage.files}개 · ${kb.toLocaleString('ko-KR')} KB`, 'field-help'));
      }
      box.append(boxImages);
    }
    if (request.update_state === 'update_blocked') {
      box.append(node('p', `수정 차단: ${request.update_error || '원격 본문이 마지막 확인본과 다름'}`, 'job-error'));
      const retry = node('button', '수정 재시도', 'small');
      retry.type = 'button';
      retry.id = 'automation-update-retry';
      retry.addEventListener('click', () => run(async () => {
        await api(`/api/automation/requests/${encodeURIComponent(request.id)}/update`, 'POST', {});
        detail = await api(`/api/automation/requests/${encodeURIComponent(request.id)}`);
        state = await api('/api/automation');
        renderCounts(); renderRequests(); renderDetail();
      }, '수정 재시도 시작'));
      box.append(retry);
    }
    if (request.error) box.append(node('p', request.error, 'job-error'));
    const facts = request.evidence?.facts || [];
    if (facts.length) {
      const list = node('ul');
      for (const fact of facts) list.append(node('li', `${fact.text} (${fact.source === 'user' ? '사용자 제공' : '페이지 확인'})`));
      box.append(node('h4', '사용한 근거'), list);
    }
    if (request.evidence?.price) box.append(node('p', `확인 가격: ${Number(request.evidence.price.amount).toLocaleString('ko-KR')}원 (${dateText(request.evidence.price.observed_at)})`, 'meta'));
    if (detail.article) {
      box.append(node('h4', detail.article.title));
      preview.srcdoc = detail.article.html || '';
    }
    if (publication) {
      box.append(node('p', `예약: ${publication.state === 'published' ? '공개 확인' : '예약 확인'} · ${dateText(publication.run_at)} · 마지막 확인 ${dateText(publication.checked_at)}`, 'meta'));
      if (publication.state === 'published' && publication.remote_url && safeURL(publication.remote_url)) {
        const anchor = node('a', '게시 글 확인');
        anchor.href = publication.remote_url; anchor.target = '_blank'; anchor.rel = 'noopener noreferrer';
        box.append(anchor);
      }
    }
    for (const item of detail.notifications || []) box.append(node('p', `알림(${item.state}): ${item.text}`, 'field-help'));
  }
  async function load() {
    const base = await api('/api/state');
    csrf = base.csrf;
    state = await api('/api/automation');
    if (dirty) { sync(); return; }
    fill(state.settings);
    renderCounts(); renderRequests(); renderDetail(); sync();
  }
  async function select(id) {
    if (busy) return;
    busy = true; sync();
    try { detail = await api(`/api/automation/requests/${encodeURIComponent(id)}`); renderRequests(); renderDetail(); notice(''); }
    catch (error) { notice(error.message, true); }
    finally { busy = false; sync(); }
  }
  async function run(task, success) {
    if (busy) return;
    busy = true; notice('처리 중'); sync();
    try { await task(); notice(success); }
    catch (error) {
      notice(error.message + (error.status === 409 ? '\n서버 저장본을 다시 불러온 뒤 수정 필요' : ''), true);
    } finally { busy = false; sync(); }
  }

  form.addEventListener('input', () => { dirty = true; sync(); });
  form.addEventListener('change', () => { dirty = true; sync(); });
  form.addEventListener('submit', event => {
    event.preventDefault();
    if (busy || !csrf || !form.reportValidity()) return;
    const body = collect();
    if (!body.schedule.weekdays.length) { notice('발행 요일 하나 이상 선택 필요', true); return; }
    run(async () => {
      await api('/api/automation/settings', 'PUT', body);
      state = await api('/api/automation');
      fill(state.settings);
      renderCounts(); renderRequests(); renderDetail();
    }, '자동 운영 설정 저장 완료');
  });
  $('automation-refresh').addEventListener('click', () => run(load, '운영 상태 갱신 완료'));
  window.addEventListener('beforeunload', event => {
    if (dirty) { event.preventDefault(); event.returnValue = ''; }
  });
  run(load);
})();
