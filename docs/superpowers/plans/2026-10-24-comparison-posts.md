# 제휴 비교 글 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 텔레그램 한 메시지의 제휴 링크 2~3개로 글 하나를 발행하고, 나중에 도착한 설명으로 같은 글(공개 포함)을 보완한다.

**Architecture:** 요청 하나에 `products` 목록을 두고 상품별 상태·근거·질문을 관리한다. 글 유형은 확보된 상품 수와 공통 항목으로 결정하며, 비교표는 서버가 근거에서 직접 만든다. 발행은 기존 단일 글 절차를 재사용하고, 보완은 직전 스냅샷과 원격 본문이 일치할 때만 같은 글 ID를 수정한다.

**Tech Stack:** Python 3.11+, FastAPI, SQLite `records`, 기존 httpx, vanilla JS, pytest, JSDOM, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-24-comparison-posts-design.md`

## Global Constraints

- 한 메시지에 링크 2~3개까지 허용한다. 4개 이상은 거절하고 나눠 보내도록 안내한다.
- 링크 1개는 기존 단일 상품 글로 처리한다. 비교 글과 단일 글의 렌더링은 분리한다.
- 요청 하나는 WordPress 글 하나다. 상품 수와 관계없이 새 글을 추가로 만들지 않는다.
- 보낸 제휴 URL은 쿼리·추적 값을 바꾸지 않고 게시한다.
- 비교표는 두 상품 이상에서 같은 이름으로 확인된 항목만 넣는다. 공통 항목이 없으면 표를 생략한다.
- 본문 최대 길이: 비교·묶음 3,000자, 단일 글은 기존 제한 유지.
- 쿠팡·토스가 섞이면 고지 두 개를 모두 상품 소개 앞에 넣는다.
- 발행 예정 시점 기준 24시간이 지난 가격은 그 상품에서만 제외한다.
- 편수는 최초 발행에서 포함된 서비스마다 1편씩만 차감한다. 보완 수정은 차감하지 않는다.
- 공개 글 수정은 직전 스냅샷과 원격 제목·본문이 일치할 때만 한다. 불일치·응답 유실 판정 불가는 `update_blocked`로 남기고 자동 반복하지 않는다.
- 수정은 글 상태(초안·예약·공개)와 예약 시각을 바꾸지 않는다.
- AI 호출은 수동·자동 공용 `AI_DAILY_LIMIT`를 사용한다. AI 입력에 제휴 URL·비밀키를 넣지 않는다.
- 기존 단일 상품 요청·수동 기능·`/api/automation` 응답 계약을 깨지 않는다.
- 사용자 요청 없는 커밋·푸시는 하지 않는다. 각 작업 끝은 관련 diff 검토다.

## Review Focus

1. 한 상품만 403이고 다른 상품은 정상일 때, 발행은 진행되고 질문은 막힌 상품에만 가야 한다 — Task 3, 5.
2. 공개 후 늦은 설명이 도착하면 같은 글 ID가 수정되고 새 글이 생기지 않아야 한다 — Task 4, 5.
3. 외부에서 본문을 고친 공개 글은 자동 수정이 차단되고 `update_blocked`로 남아야 한다 — Task 4, 5.
4. 단일 상품 요청과 기존 스냅샷(products 없음)이 발행·조회·보완에서 계속 동작해야 한다 — Task 1, 4.
5. 두 상품이 같은 서비스이거나 서로 다른 서비스일 때 고지·링크·편수 집계가 정확해야 한다 — Task 2, 5.

---

## 공통 데이터 계약

`Product`:
`{index, provider, affiliate_url, evidence, state, revision, question_message_id, reason}`
- `index`는 1부터, `state`는 `ready`·`awaiting_info`·`failed`.
- `evidence`는 기존 Evidence와 같다. `facts`의 `라벨: 값` 형식만 비교표 후보로 쓴다.

`Request`는 기존 필드에 더해 `products`, `update_state`(`''`·`updating`·`updated`·`update_blocked`), `update_error`를 가진다. 기존 단일 요청은 `products`가 없을 수 있다.

`Snapshot`:
`{title, html, disclosures:[str], products:[{index,provider,affiliate_url,evidence}]}`.
기존 스냅샷은 `provider`, `affiliate_url`만 있어도 읽혀야 한다.

새 파일 `studio/automation_products.py`가 위 변환을 전담한다. 다른 모듈은 요청의 최상위 `provider`/`affiliate_url`을 직접 읽지 않고 이 모듈을 거친다.

---

### Task 1: 상품 목록 모델과 기존 요청 호환

**Files:**
- Create: `studio/automation_products.py`
- Test: `tests/test_automation_products.py`

**Interfaces:**
- `PRODUCT_LIMIT = 3`
- `products_of(request: dict) -> list[dict]`; 기존 단일 요청은 상품 1개로 변환한다.
- `with_products(request: dict, products: list[dict]) -> dict`; `products`와 호환용 최상위 `provider`/`affiliate_url`/`evidence`를 함께 채운 복사본을 반환한다.
- `derive_state(products: list[dict]) -> str`; ready가 하나라도 있으면 `ready`, 없고 awaiting이 있으면 `awaiting_info`, 아니면 `failed`.
- `ready_products(products) -> list[dict]`; `distinct_providers(products) -> list[str]`; `request_providers(request) -> list[str]`.
- `next_question(products) -> dict|None`; `question_message_id`가 없는 첫 awaiting 상품.

- [ ] **Step 1: 실패 테스트 작성**

```python
def test_legacy_single_request_reads_as_one_product():
    row = {'id': 'req', 'provider': 'coupang',
           'affiliate_url': 'https://link.coupang.com/a/fixture',
           'evidence': EVIDENCE, 'state': 'ready'}
    products = products_of(row)
    assert len(products) == 1
    assert products[0]['index'] == 1 and products[0]['state'] == 'ready'
    assert with_products(row, products)['affiliate_url'] == row['affiliate_url']

def test_partial_ready_derives_ready_and_keeps_awaiting_product():
    products = [make_product(1, 'ready'), make_product(2, 'awaiting_info')]
    assert derive_state(products) == 'ready'
    assert [p['index'] for p in ready_products(products)] == [1]
    assert next_question(products)['index'] == 2

def test_providers_are_distinct_and_ordered():
    products = [make_product(1, 'ready', provider='toss'),
                make_product(2, 'ready', provider='coupang'),
                make_product(3, 'ready', provider='toss')]
    assert distinct_providers(products) == ['toss', 'coupang']
```

추가: `test_with_products_rejects_more_than_three`, `test_all_awaiting_is_awaiting_info`, `test_all_failed_is_failed`.

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_automation_products.py -q`
Expected: `ModuleNotFoundError: studio.automation_products`

- [ ] **Step 3: 최소 구현**

`products_of`는 `products`가 있으면 각 항목의 누락 키만 기본값으로 채우고, 없으면 최상위 필드로 1개를 만든다. `with_products`는 3개 초과를 `ValueError`로 거절한다.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_automation_products.py tests/test_automation.py -q`
Expected: PASS

- [ ] **Step 5: diff 검토**

기존 요청 필드를 지우지 않았는지, 인덱스가 1부터 연속인지 확인한다.

---

### Task 2: 다상품 글 작성·검사·렌더링

**Files:**
- Modify: `studio/writing_policy.py`
- Test: `tests/test_writing_policy_multi.py`

**Interfaces:**
- `choose_style(products, settings) -> str`: `comparison`·`bundle`·`intro`·`guide`·`concise`.
- `comparison_rows(products) -> dict|None`: `{headers:[...], rows:[[...]]}`.
- `compose(client, products, settings, *, feedback=()) -> dict`; material은 `{products:[{index,name,facts:[{index,text}]}]}`이고 `evidence_refs`는 전체 사실의 연속 번호다.
- `check_article(article, products) -> list[str]`; dict 또는 list를 받는다.
- `prepare_products(products, run_at) -> list[dict]`.
- `render_article(article, products, *, settings) -> {html,text}`.

- [ ] **Step 1: 실패 테스트 작성**

```python
def test_comparison_table_uses_only_common_labelled_fields():
    a = product(1, facts=['용량: 120매', '색상: 흰색'])
    b = product(2, facts=['용량: 100매', '구성: 10개'])
    rows = comparison_rows([a, b])
    assert [row[0] for row in rows['rows']] == ['용량']
    assert rows['rows'][0][1:] == ['120매', '100매']

def test_no_common_fields_renders_bundle_without_table():
    a = product(1, facts=['색상: 흰색'])
    b = product(2, facts=['구성: 10개'])
    assert comparison_rows([a, b]) is None
    assert choose_style([a, b], SETTINGS) == 'bundle'

def test_mixed_disclosures_and_links_are_all_present():
    a, b = product(1, provider='coupang'), product(2, provider='toss')
    rendered = render_article(article(), [a, b], settings=SETTINGS)
    assert rendered['html'].index('쿠팡 파트너스') < rendered['html'].index(a['evidence']['name'])
    assert '토스쇼핑' in rendered['html']
    assert a['affiliate_url'] in rendered['html'] and b['affiliate_url'] in rendered['html']

def test_single_product_render_keeps_existing_layout():
    rendered = render_article(article(), [product(1)], settings=SETTINGS)
    assert rendered['text'].rstrip().endswith(product(1)['affiliate_url'])
    assert '정보 출처' in rendered['text']

def test_comparison_length_and_ungrounded_numbers():
    too_long = article(body='확인 ' * 1600)
    assert check_article(too_long, [product(1), product(2)])
    assert check_article(article(body='무게 777kg'), [product(1), product(2)])
    assert check_article(article(body='무게 999kg'), [product(1, facts=['무게: 999kg']), product(2)]) == []
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_writing_policy_multi.py -q`
Expected: `comparison` 관련 실패 또는 `TypeError`

- [ ] **Step 3: 최소 구현**

`comparison_rows`는 `라벨: 값` 사실을 라벨 기준으로 모아 2개 이상 상품에 있는 라벨만 남긴다. 두 상품 이상 가격이 있으면 `가격` 행을 `19,900원` 형식으로 추가한다. 비교표는 서버가 만들고 AI에는 표를 만들지 말라고 지시한다. `render_article`은 상품 1개면 기존 출력 구조를 유지하고, 2개 이상이면 고지들 → 제목 → 본문 → 비교표 → 상품별 소개(이름·사실·가격·링크) → 출처 순으로 만든다. 링크와 고지는 모두 escape한다.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_writing_policy_multi.py tests/test_writing_policy.py tests/test_ai_budget.py -q`
Expected: PASS

- [ ] **Step 5: diff 검토**

단일 글 출력이 기존 테스트와 동일한지, AI 입력에 URL·가격이 없는지 확인한다.

---

### Task 3: 다중 링크 접수와 상품별 답장

**Files:**
- Modify: `studio/telegram_inbox.py`
- Test: `tests/test_telegram_inbox_multi.py`

**Interfaces:**
- `extract_links`는 그대로 사용하고 결과의 중복을 제거한다.
- `question_text(request: dict, product: dict) -> str`; `N번 상품`과 요청 ID를 포함한다.
- `receive_update`는 2~3개 링크를 요청 하나로 접수하고, 상품별 질문을 `question:{index}:{revision}` 키로 만든다.

- [ ] **Step 1: 실패 테스트 작성**

```python
def test_two_links_make_one_request_with_two_products(tmp_path):
    request_id = receive(store, client, message_update(1, f'{LINK_A} {LINK_B}'), opener=ready)
    with store.connect() as c:
        row = store.rows(c, 'automation_request')[0]
    assert len(row['products']) == 2
    assert row['state'] == 'ready'
    assert len([n for n in store.rows(c, 'notice') if n['request_id'] == request_id]) >= 1

def test_one_blocked_product_publishes_ready_others_and_asks_only_blocked(tmp_path):
    request_id = receive(store, client, message_update(1, f'{LINK_A} {LINK_B}'), opener=blocked_second)
    row = request(store, request_id)
    assert row['state'] == 'ready'
    assert row['products'][1]['state'] == 'awaiting_info'
    questions = [n for n in notices(store) if n['request_id'] == request_id and n['force_reply']]
    assert len(questions) == 1 and '2번 상품' in questions[0]['text']

def test_reply_updates_only_its_product_by_message_id(tmp_path):
    request_id = receive(...)
    deliver_question(...)
    reply = message_update(2, '상품명: B\n특징: B 사실', reply_to=bot_question)
    receive(store, client, reply, opener=blocked_second)
    row = request(store, request_id)
    assert row['products'][1]['evidence']['name'] == 'B'
    assert row['products'][0]['revision'] == 0

def test_four_links_are_rejected_without_a_request(tmp_path):
    receive(store, client, message_update(1, ' '.join([A, B, C, D])), opener=ready)
    assert requests(store) == []
    assert any('2~3개' in n['text'] for n in notices(store))
```

추가: `test_late_reply_marks_update_pending`, `test_duplicate_link_is_not_added_twice`, `test_all_blocked_is_awaiting_info`, `test_reply_without_question_id_uses_product_number`.

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_telegram_inbox_multi.py -q`
Expected: 다중 링크가 현재 거절되어 실패

- [ ] **Step 3: 최소 구현**

링크를 중복 제거한 뒤 2~3개면 요청 하나를 만든다. 각 링크를 기존 `inspect_link`로 확인하고 상품 항목을 만든다. ready 상품이 하나라도 있으면 요청은 `ready`이고, awaiting 상품마다 질문을 만든다. 답장은 질문 메시지 ID → 요청 ID+`N번 상품` 순으로 연결한다. 발행·예약된 요청의 상품 설명이 바뀌면 `update_state='updating'`을 설정하고 새 요청을 만들지 않는다. 기존 단일 링크 동작은 유지한다.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_telegram_inbox_multi.py tests/test_telegram_inbox.py tests/test_automation_products.py -q`
Expected: PASS

- [ ] **Step 5: diff 검토**

질문이 상품 수만큼만 생성되는지, 같은 링크가 중복 저장되지 않는지 확인한다.

---

### Task 4: 공개 글 스냅샷 수정과 외부 변경 차단

**Files:**
- Modify: `studio/automation_publish.py`, `studio/wordpress.py`
- Test: `tests/test_automation_update.py`

**Interfaces:**
- `class SnapshotConflict(ValueError)`
- `snapshot_disclosures(snapshot: dict) -> list[str]`; 기존 스냅샷은 `provider`로 보완한다.
- `update_snapshot(store, request_id, client, target_snapshot, at) -> dict`; 초안·예약·공개 글의 제목·본문만 같은 ID에 갱신한다. 이미 목표 스냅샷이면 요청을 보내지 않는다.
- `WordPress.verify_snapshot`은 `snapshot['disclosures']`가 있으면 전부 확인한다.

- [ ] **Step 1: 실패 테스트 작성**

```python
def test_update_public_post_in_place(tmp_path):
    publish(store, wp)
    updated = update_snapshot(store, 'req', wp, new_snapshot(), AT)
    assert updated['remote_id'] == 42
    assert wp.posts[42]['status'] == 'publish'
    assert wp.creates == 1
    assert wp.posts[42]['content']['raw'] == new_snapshot()['html']

def test_external_edit_blocks_update(tmp_path):
    publish(store, wp)
    wp.posts[42]['content']['raw'] += '<p>외부 수정</p>'
    with pytest.raises(SnapshotConflict):
        update_snapshot(store, 'req', wp, new_snapshot(), AT)
    assert len(wp.updates) == 0

def test_lost_update_response_is_idempotent(tmp_path):
    publish(store, wp)
    wp.lose_next_update = True
    with pytest.raises(ValueError):
        update_snapshot(store, 'req', wp, new_snapshot(), AT)
    again = update_snapshot(store, 'req', wp, new_snapshot(), AT)
    assert again['snapshot'] == new_snapshot()
    assert len(wp.updates) == 1

def test_update_keeps_scheduled_run_at(tmp_path):
    schedule(store, wp, AT + timedelta(hours=1))
    updated = update_snapshot(store, 'req', wp, new_snapshot(), AT)
    assert updated['state'] == 'scheduled'
    assert updated['run_at'] == (AT + timedelta(hours=1)).isoformat()
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_automation_update.py -q`
Expected: `cannot import name 'update_snapshot'`

- [ ] **Step 3: 최소 구현**

읽기 → 직전 스냅샷 비교 → 이미 목표면 종료 → 제목·본문만 POST → 읽기 → 목표 스냅샷 비교. 상태·예약 시각은 보내지 않는다. 직전·목표 어느 쪽과도 다르면 `SnapshotConflict`. 응답 유실 뒤 다시 호출하면 목표 스냅샷을 읽어 성공 처리한다. 기존 `replace_draft_snapshot` 호출·테스트는 이 함수로 교체한다.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_automation_update.py tests/test_automation_publish.py tests/test_wordpress.py -q`
Expected: PASS

- [ ] **Step 5: diff 검토**

POST 본문에 `status`·`date_gmt`가 없는지, 공개 글이 비공개로 바뀌지 않는지 확인한다.

---

### Task 5: 실행기 연결 — 편수·보완·차단

**Files:**
- Modify: `studio/automation.py`
- Test: `tests/test_automation_multi.py`

**Interfaces:**
- `_compose`는 `ready_products(products_of(request))`로 글을 만든다.
- `_used(rows, provider, day)`는 요청의 `products`에 provider가 포함된 경우만 센다.
- `retry_update(request_id) -> dict`; `update_blocked`를 `updating`으로 되돌린다.
- `tick`은 `update_state == 'updating'`인 요청을 새 글 없이 보완한다.

- [ ] **Step 1: 실패 테스트 작성**

```python
def test_mixed_providers_consume_one_slot_each(tmp_path):
    automation, store, remote, receive = build(tmp_path, limits={'coupang': 1, 'toss': 1})
    receive(store, automation.telegram, message_update(1, f'{COUPANG} {TOSS}'), opener=ready)
    due, _ = prepare(automation)
    automation.tick(due)
    state = automation.public(due)
    request = state['requests'][0]
    assert request['state'] == 'scheduled'
    assert state['counts']['coupang']['scheduled'] == 1
    assert state['counts']['toss']['scheduled'] == 1

def test_one_service_without_quota_defers_whole_post(tmp_path):
    ...  # toss limit 0, two links
    automation.tick(due)
    assert automation.public(due)['requests'][0]['state'] == 'ready'
    assert remote.posts == {}

def test_late_description_updates_published_post(tmp_path):
    ...  # publish one ready + one blocked
    deliver question; receive reply
    automation.tick(later)
    assert len(remote.posts) == 1
    assert 'B 사실' in remote.posts[42]['content']['raw']
    assert remote.posts[42]['status'] == 'publish'

def test_blocked_update_is_not_retried_automatically(tmp_path):
    ...  # external edit then tick
    assert request['update_state'] == 'update_blocked'
    ticks = len(wp.updates)
    automation.tick(later + timedelta(minutes=30))
    assert len(wp.updates) == ticks

def test_single_link_still_publishes_one_product(tmp_path):
    ...
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_automation_multi.py -q`
Expected: 다중 링크가 단일 글/실패로 처리되어 실패

- [ ] **Step 3: 최소 구현**

첫 발행은 ready 상품 전체로 스냅샷을 만들고 포함된 서비스별 잔여 편수를 확인한다. 하나라도 부족하면 발행하지 않고 대기한다. `update_state='updating'`은 ready 상품 전체로 새 스냅샷을 만들어 `update_snapshot`을 호출한다. `SnapshotConflict`는 `update_blocked`, 연결 오류는 다음 tick 재시도다. 성공 시 Article·Publication 스냅샷을 함께 갱신하고 텔레그램으로 알린다. 보완은 편수를 차감하지 않는다.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_automation_multi.py tests/test_automation.py tests/test_automation_recovery.py -q`
Expected: PASS

- [ ] **Step 5: diff 검토**

보완 경로가 `ensure_draft`나 새 글 생성에 도달하지 않는지 확인한다.

---

### Task 6: 화면 — 상품별 상세와 수정 재시도

**Files:**
- Modify: `studio/automation_routes.py`, `studio/app.py`, `studio/static/automation.js`, `studio/static/automation.css`
- Test: `tests/automation-dom.cjs`, `tests/test_automation_http.py`

**Interfaces:**
- `POST /api/automation/requests/{id}/update`; `retry_update` 호출, CSRF 적용.
- 상세 응답은 `request.products`를 그대로 포함한다.
- DOM: `#automation-products`, `[data-update-request]`, 상품별 `data-product-index`.

- [ ] **Step 1: 실패 테스트 작성**

```javascript
test('detail lists each product and retries a blocked update', async () => {
  // detail.request.products = [A ready, B ready], update_state = 'update_blocked'
  // 클릭 → POST /api/automation/requests/req_one/update, 버튼 1회만
  // 재시도 후 update_state='updating' 표시
});

test('external edit reason is shown without hiding product links', ...);
```

Python: `test_update_retry_requires_connection_and_csrf`, `test_update_retry_rejects_non_blocked`.

- [ ] **Step 2: 실패 확인**

Run: `node --test tests/automation-dom.cjs`; `.venv/bin/python -m pytest tests/test_automation_http.py -q`
Expected: 버튼·경로 없음으로 실패

- [ ] **Step 3: 최소 구현**

상세에서 상품 목록(순번, 이름, 상태, 링크, 사실, 가격)을 표시한다. `update_blocked`면 사유와 `수정 재시도` 버튼을 보여주고, 누르면 POST 후 상세·상태를 다시 읽는다. 비교표·본문은 기존 sandbox 미리보기를 사용한다. 미연결 시 기존처럼 400으로 실패한다.

- [ ] **Step 4: 통과 확인**

Run: `node --test tests/automation-dom.cjs tests/ui-copy.cjs`; `.venv/bin/python -m pytest tests/test_automation_http.py -q`
Expected: PASS

- [ ] **Step 5: diff 검토**

원격 URL은 검증된 값만 링크로 쓰고, 상품 텍스트는 textContent로 넣는지 확인한다.

---

### Task 7: 문서와 전체 검증

**Files:**
- Modify: `README.md`, `studio/static/index.html` (시작하기)
- Test: `tests/test_ui_messages.py` (문구 계약이 있으면)

- [ ] **Step 1: 실패 테스트 작성**

README 문구 계약 테스트가 없으면 생략하고, `tests/ui-copy.cjs`에 비교 글 안내가 `2~3개`·`공개 글 수정`을 포함하는지 검사를 추가한다.

- [ ] **Step 2: 실패 확인**

Run: `node --test tests/ui-copy.cjs`
Expected: 문구 없음으로 실패

- [ ] **Step 3: 문서 갱신**

README Telegram 절과 시작하기 카드에 다음을 적는다: 링크 2~3개는 한 글, 일부 정보가 없어도 먼저 발행, 늦은 설명은 같은 글 수정, 외부 수정 시 자동 수정 차단과 웹 재시도, 보완은 편수 미차감.

- [ ] **Step 4: 최종 검증**

```sh
.venv/bin/python -m pytest -q --tb=short
node --test tests/v02-actions.cjs tests/v02-dom.cjs tests/v03-metrics.cjs tests/ui-copy.cjs tests/affiliate-dom.cjs tests/automation-dom.cjs
CHROMIUM_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' node tests/e2e_automation.mjs
CHROMIUM_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' npm run test:e2e
.venv/bin/python scripts/package.py
git diff --check
```

- [ ] **Step 5: 최종 검토**

Spec 9번 검증 기준을 작업별 테스트에 대조하고, 실제 계정 발행은 하지 않았음을 보고한다.

---

## 계획 자체 검토

- [x] Spec coverage: 입력·부분 발행(3), 유형·비교표·고지(2), 공개 수정·차단(4), 편수·보완(5), 화면(6), 문서·검증(7).
- [x] Step scan: 각 작업에 파일·인터페이스·5단계가 있고, 테스트 기대값이 결정되어 있다.
- [x] Type consistency: `products_of`/`with_products`/`ready_products`/`update_snapshot`/`SnapshotConflict`/`retry_update` 이름을 작업 간 동일하게 사용한다.
- [x] Review Focus 5개를 Task 3·5, 4·5, 4·5, 1·4, 2·5의 명명된 테스트에 연결했다.
- [x] Proportion: 설계 197행 대비 계획 약 330행, 기능 범위를 단일 요청·한 글로 제한했다.
