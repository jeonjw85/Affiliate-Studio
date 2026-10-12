'use strict';
(() => {
  const $ = id => document.getElementById(id), form = $('metrics-form');
  const fields = ['draft_id','channel','page_url','period_start','period_end','source','views','clicks','orders','revenue_won','cost_won','note'];
  const channels = {wordpress:'WordPress',naver:'네이버',tistory:'티스토리',other:'기타'};
  let items = [], csrf = '', busy = false, dirty = false, editing = null;
  const field = name => form.elements.namedItem(name);
  const node = (tag, value, cls) => { const el=document.createElement(tag); if(value!=null)el.textContent=String(value); if(cls)el.className=cls; return el; };
  function notice(text, error=false) { const el=$('metrics-notice');el.textContent=text;el.hidden=!text;el.className=error?'notice error':'notice'; }
  function validateMoney() {
    for(const name of ['revenue_won','cost_won']) {
      const input=field(name), value=input.value.trim();
      input.setCustomValidity(!value || /^[0-9]+(?:\.[0-9]{1,2})?$/.test(value)?'':'금액은 숫자와 소수점으로, 소수 둘째 자리까지 입력하세요. 예: 1000 또는 1000.50 (지수표기 불가)');
    }
  }
  function sync() {
    validateMoney();
    $('metrics-fields').disabled=busy || !csrf || !$('metrics-draft').options.length;
    $('metrics-draft').disabled=Boolean(editing);
    $('metrics-form-title').textContent=editing?'성과 기록 수정':'성과 기록 추가';
    $('metrics-save').textContent=editing?'변경 저장':'기록 저장';
    $('metrics-edit-note').textContent=editing?`기록 버전 ${editing.version} · 연결 콘텐츠는 변경하지 않습니다.`:($('metrics-draft').options.length?'자동 검증되지 않은 수동 기록입니다.':'상품 탭에서 콘텐츠를 먼저 만드세요.');
    $('metrics-add').disabled=busy;
    $('metrics-refresh').disabled=busy; $('metrics-new').disabled=busy;
    $('metrics-export').disabled=busy || !csrf || !items.length;
    for(const b of $('metrics-list').querySelectorAll('button')) b.disabled=busy;
  }
  async function api(url,method='GET',data) {
    const opts={method,credentials:'same-origin',cache:'no-store',headers:{}};
    if(method!=='GET'){
      if(!csrf)throw new Error('서버 연결을 먼저 확인하세요.');
      opts.headers={'Content-Type':'application/json','X-CSRF-Token':csrf}; opts.body=JSON.stringify(data);
    }
    const response=await fetch(url,opts);const payload=await response.json();
    if(!response.ok){const e=new Error(payload.error||'요청 형식을 확인하세요.');e.status=response.status;throw e;}
    return payload;
  }
  async function run(task,success='') {
    if(busy)return;busy=true;sync();notice('처리 중입니다.');
    try {await task();notice(success);}
    catch(e){notice((e.message||'서버에 연결하지 못했습니다.')+(e.status===409?' 현재 입력은 유지됩니다. 기록 새로고침 후 기존 기록의 수정을 다시 선택하세요.':''),true);}
    finally{busy=false;sync();}
  }
  function reset() {
    form.reset();dirty=false;editing=null;
    const d=new Date();const day=new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,10);
    field('period_start').value=day;field('period_end').value=day;
  }
  async function load() {
    const state=await api('/api/state');csrf=state.csrf;
    const selected=field('draft_id').value;
    $('metrics-draft').replaceChildren();
    for(const d of state.drafts||[]){const option=node('option',`${d.title} · v${d.version}`);option.value=d.id;$('metrics-draft').append(option);}
    if(selected && [...$('metrics-draft').options].some(o=>o.value===selected))field('draft_id').value=selected;
    const result=await api('/api/measurements');items=result.items;render();
    if(!state.drafts?.length)notice('상품에서 콘텐츠를 먼저 만드세요. 성과 기록을 추가해도 게시되지는 않습니다.');
  }
  const display = (value,unit='',unknown='미입력') => value==null ? unknown : `${Number(value).toLocaleString('ko-KR',{maximumFractionDigits:2})}${unit}`;
  function render() {
    const list=$('metrics-list');list.replaceChildren();
    const filtered=items.filter(r=>!$('metrics-filter').value || r.channel===$('metrics-filter').value);
    $('metrics-count').textContent=filtered.length;
    $('metrics-status').textContent=`표시 ${filtered.length} / 전체 ${items.length}건 · 직접 입력한 값이며 외부 서비스에서 검증하지 않았습니다.`;
    if(!filtered.length)list.append(node('p','이 조건의 기록이 없습니다. 실제 보고서를 확인한 뒤 기록을 추가하세요.','card empty'));
    for(const r of filtered){
      const card=node('article',null,'card measurement-card');
      card.append(node('h3',r.draft_title),node('p',`${channels[r.channel]||r.channel} · 원본 v${r.draft_version} · ${r.period_start} ~ ${r.period_end} · 수동 기록`,'meta'));
      const grid=node('dl',null,'metrics-grid');
      for(const [label,value] of [['조회수',display(r.views)],['클릭수',display(r.clicks)],['주문수',display(r.orders)],['수수료 수익',display(r.revenue_won,'원')],['입력 비용',display(r.cost_won,'원')],['클릭/조회 비율',display(r.rates.click_rate,'%', '계산 불가')],['주문/클릭 비율',display(r.rates.order_rate,'%', '계산 불가')],['클릭당 수익',display(r.rates.epc_won,'원','계산 불가')],['수익 − 입력 비용',display(r.rates.balance_won,'원','계산 불가')]]){
        const cell=node('div');cell.append(node('dt',label),node('dd',value));grid.append(cell);
      }
      card.append(grid,node('p',`출처·기준: ${r.source}`,'field-help'));
      if(r.page_url)card.append(node('p',`게시글: ${r.page_url}`,'field-help'));
      if(r.note)card.append(node('p',r.note));
      for(const warning of r.warnings||[])card.append(node('p',warning,'check-warning'));
      const actions=node('div',null,'actions');
      const edit=node('button','기록 수정','small');edit.type='button';edit.setAttribute('data-edit-measurement','');
      edit.addEventListener('click',()=>{
        if(busy || (dirty && !window.confirm('저장하지 않은 입력을 버리고 이 기록을 수정할까요?')))return;
        editing=r;for(const name of fields)field(name).value=r[name]??'';dirty=false;sync();
        form.scrollIntoView?.({block:'start',behavior:'smooth'});
      });
      const del=node('button','기록 삭제','small');del.type='button';del.setAttribute('data-delete-measurement','');
      del.addEventListener('click',()=>{
        if(busy || !window.confirm(`“${r.draft_title}”의 ${r.period_start}~${r.period_end} 성과 기록만 삭제할까요? 원본 콘텐츠와 외부 게시글은 삭제하지 않습니다.`))return;
        run(async()=>{await api(`/api/measurements/${encodeURIComponent(r.id)}`,'DELETE',{version:r.version});if(editing?.id===r.id)reset();await load();},'성과 기록만 삭제했습니다. 원본 콘텐츠는 유지됩니다.');
      });
      actions.append(edit,del);card.append(actions);list.append(card);
    }
  }
  form.addEventListener('input',()=>{dirty=true;validateMoney();});
  form.addEventListener('change',()=>{dirty=true;validateMoney();});
  form.addEventListener('submit',event=>{
    event.preventDefault();validateMoney();if(busy || !form.reportValidity())return;
    run(async()=>{
      const data={};for(const name of fields)data[name]=field(name).value.trim();
      for(const name of ['views','clicks','orders'])data[name]=data[name]===''?null:Number(data[name]);
      for(const name of ['revenue_won','cost_won'])data[name]=data[name]===''?null:data[name];
      if(editing)data.version=editing.version;
      await api(editing?`/api/measurements/${encodeURIComponent(editing.id)}`:'/api/measurements',editing?'PUT':'POST',data);reset();await load();
    },'수동 성과 기록을 저장했습니다. 실제 서비스의 검증·정산을 대신하지 않습니다.');
  });
  $('metrics-export').addEventListener('click',()=>{
    if($('metrics-export').disabled)return;
    run(async()=>{
      const response=await fetch('/api/measurements/export',{credentials:'same-origin',cache:'no-store'});
      if(!response.ok)throw new Error('CSV를 가져오지 못했습니다. 서버 상태를 확인하세요.');
      const url=URL.createObjectURL(await response.blob()),a=node('a');a.href=url;a.download='measurements.csv';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    },'전체 기록 CSV를 내려받았습니다. 화면 필터와 관계없이 전체 기록이며 수동 입력 자료입니다.');
  });
  $('metrics-filter').addEventListener('change' ,()=>{render();sync();});
  $('metrics-refresh').addEventListener('click',()=>run(load,'기록을 갱신했습니다. 입력 중인 내용은 유지됩니다.'));
  $('metrics-add').addEventListener('click',()=>{if(!busy){$('metrics-new').click();form.scrollIntoView?.({block:'start',behavior:'smooth'});}});
  $('metrics-new').addEventListener('click',()=>{if(!busy && (!dirty || window.confirm('저장하지 않은 성과 입력을 버리고 새 기록을 만들까요?'))){reset();sync();}});
  document.querySelectorAll('[data-view="metrics"]').forEach(b=>b.addEventListener('click',()=>run(load)));
  window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
  reset();sync();
})();
