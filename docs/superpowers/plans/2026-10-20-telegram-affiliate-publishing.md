# Telegram Affiliate Publishing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 본인 제휴 링크를 Telegram으로 보내면 상품 정보를 확보하고, 필요할 때 설명을 답장받아 상황별 글을 작성·검사·WordPress 예약 발행한 뒤 결과를 회신한다.

**Architecture:** 기존 FastAPI 단일 프로세스와 SQLite `records`를 사용한다. Telegram 수신·공개 페이지 조회·작성 정책·예약 실행·원격 발행·알림을 분리하고, 외부 요청 전 상태를 저장한다. 쿠팡·토스 API 없이 원문 제휴 링크를 게시하며, 자동 작업의 검사 상태는 기존 수동 승인과 구별한다.

**Tech Stack:** Python 3.11+, FastAPI, SQLite, 기존 httpx, 표준 라이브러리 HTMLParser/http.client/ssl/zoneinfo, 기존 Markdown 렌더러, vanilla JS, pytest, JSDOM, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-19-autonomous-wordpress-publishing-design.md` — 2026-10-20 승인된 Telegram 입력 기반 버전.

## Global Constraints

- 지원 입력은 본인 계정에서 발급한 쿠팡·토스 제휴 링크이며 메시지 하나당 상품 하나다. 정상 경로에 글별 승인 단계를 추가하지 않는다.
- 원문 제휴 URL의 쿼리·추적 값을 보존한다. AI 입력·로그에는 제휴 URL과 인증키를 보내지 않는다.
- 공개 페이지 조회: 전체 20초, 리다이렉트 최대 5회, 응답별 압축 해제 후 최대 2MiB, HTTPS 공개 목적지만 허용한다.
- 보충 자료는 텍스트이며 상품명과 구체적인 사실 1개 이상이 필요하다. 1~2개 사실은 300~700자 이내 짧은 글, 3개 이상은 목표 1,200~2,000자 이내 소개/가이드다. 근거를 늘려 분량을 채우지 않는다.
- 가격은 발행 예정 시점 기준 24시간 이내 자료만 사용한다. 오래된 가격은 재확인하거나 제외한다.
- 쿠팡·토스별 하루 편수, 같은 날짜 내 발행 시간 범위, 최소 1분 슬롯 간격을 사용한다. 배정 가능 시간은 발행 15분 전부터 5분 전까지다.
- 제안 시간대/시간 범위는 `Asia/Seoul`, `09:00`~`21:00`이다. 편수는 사용자가 설정하며 0은 해당 서비스의 대기열 보관이다.
- 일정 변경은 기존 운영 시간대의 다음 날부터 적용한다. 자동 운영 중지 후에도 입력 보관·이미 예약된 글 조회·알림 전달은 계속한다.
- AI 생성과 재작성 1회는 기존 수동 사용과 같은 UTC 날짜 기준 `AI_DAILY_LIMIT`에 합산한다. 실패한 시도도 사용량이다.
- Telegram은 등록된 개인 채팅·발신자만 수신한다. 상태 변경/알림 저장과 메시지 수신 오프셋은 DB 트랜잭션으로 보존한다.
- WordPress 생성·예약 결과 불명은 조회로 복구한다. 확인되지 않은 생성 요청을 자동 재전송하지 않는다.
- 새 런타임 패키지 없이 기존 의존성과 표준 라이브러리를 우선 사용한다. 필요한 추가 의존성이 드러나면 이유를 제시한 뒤 계획을 조정한다.
- UI 문구는 기존 `tests/ui-copy.cjs` 규칙을 따른다. 사용자 원문과 생성 본문은 문구 단축 규칙의 대상이 아니다.
- 현재 작업 트리에 AI/LAN/Docker/제휴 링크 관련 미커밋 변경이 있다. 이를 기반으로 계획했으므로 HEAD만 있는 작업 공간으로 옮겨 누락시키지 않는다. 실행 전 작업 공간 방식을 확인하고 기존 변경을 보존한다.
- 사용자 요청 없는 커밋·푸시·리베이스는 하지 않는다. 각 작업의 마지막 단계는 관련 diff 검토이며 커밋 단계로 대체하지 않는다. 실제 `.env` 값과 실제 사용자 링크를 fixture·문서에 넣지 않는다.

## Review Focus

1. 동시에 기다리는 두 상품에 역순으로 답하거나 질문 전송 응답이 유실돼도 설명은 해당 요청에만 연결되어야 한다 — Task 4.
2. 첫 호스트는 공개 IP지만 리다이렉트 또는 두 번째 DNS 조회가 내부망을 가리키는 입력에서 실제 연결도 검증된 공개 IP에 고정되어야 한다 — Task 2.
3. 다음 날로 이월된 기존 원격 초안의 가격이 만료되면 같은 글 ID에서 가격을 제거하며, 응답 유실이 새 글 생성으로 이어지면 안 된다 — Task 6.
4. 일 한도의 마지막 1회를 수동 작성과 자동 작성이 동시에 요청해도 실제 AI 요청은 한 번만 나가야 한다 — Task 5.
5. 편수·시간대 변경, DST 중복 시각, 앱 재시작이 겹쳐도 슬롯과 원격 글이 복제되지 않아야 한다 — Task 1, 7.

---

## 파일 구성과 작업 순서

| 파일 | 책임 | 작업 |
| --- | --- | --- |
| `studio/automation_schedule.py` 신규 | 설정 검증, 현지 날짜별 UTC 슬롯 계산 | 1 |
| `studio/automation_store.py` 신규 | 설정 버전, 작업 claim, 알림 저장, 공개 상태 조회 | 1 |
| `studio/product_info.py` 신규 | 링크 분류, 제한된 HTTP 조회, 근거 추출·보충 병합 | 2 |
| `studio/telegram.py` 신규 | Telegram HTTP 계약, 인증된 봇 식별, 알림 전달 | 3 |
| `studio/telegram_inbox.py` 신규 | 메시지 해석, 접수·답장 연결·오프셋 트랜잭션 | 4 |
| `studio/writing_policy.py` 신규 | 자동 글 템플릿, 검사, 렌더링 | 5 |
| `studio/ai.py`, `studio/core.py` 수정 | 수동·자동 공유 AI 사용량 예약 | 5 |
| `studio/automation_publish.py` 신규 | 자동 발행 스냅샷, 원격 요청 기록·조회 복구 | 6 |
| `studio/wordpress.py` 수정 | 조회 파라미터·raw 스냅샷 검증 재사용 | 6 |
| `studio/automation.py` 신규 | 단계 실행, 재시도·복구, 실행 수명주기 | 7 |
| `studio/automation_routes.py` 신규, `studio/app.py` 수정 | 설정/상태/상세 경로와 작업자 연결 | 7 |
| `studio/static/automation.js`, `automation.css` 신규 | 독립된 자동 운영 화면 | 8 |
| `studio/static/index.html` 수정 | 메뉴·폼·작업 상세·시작하기 연결 | 8 |
| `.env.example`, `README.md`, `compose.yaml` 수정 | 연결 안내와 긴 글용 로컬 문맥 환경설정 | 3, 7, 8 |

각 작업은 자체 테스트를 가진다. 1을 기반으로 2·3을 만들고 4에서 연결하며, 5·6을 연결한 7이 전체 실행 기능을 제공한다. 8에서 운영 화면과 실제 브라우저 흐름을 완성한다. 서로 다른 제품으로 분리하기보다는 같은 요청 ID·근거·발행 스냅샷 계약을 공유하는 한 기능으로 진행한다.

## 공통 데이터 계약

아래 이름은 JSON 직렬화 가능한 `dict`의 형태를 설명한다. 새 ORM이나 일반화된 작업 프레임워크를 만들지 않는다. datetime 인자는 시간대가 있어야 하며 저장 시 UTC ISO 문자열로 변환한다.

- **Settings**: `{version, enabled, writing:{topic,audience,tone,purpose,extra_instructions,category_instructions}, schedule:{timezone,weekdays,start,end,daily_limits:{coupang,toss}}, pending_schedule:null|{effective_at,schedule}}`. `weekdays`는 월요일=0, 일요일=6이다. 최초 값은 비활성, 편수 0/0, 매일이며 활성화 때 편수를 입력한다.
- **Evidence**: `{name, facts:[{text,source,observed_at}], price:null|{amount,currency,source,observed_at}, category,audience,source_url:null|string,product_key:null|string,conflicts:[]}`. `source`는 `page` 또는 `user`, currency는 `KRW`다. 이름 최대 160자, 사실 최대 20개·각 500자, 중복 사실 제외. 상품 URL 미확인 상태는 null로 유지한다.
- **Inspection**: `{provider,affiliate_url,evidence,outcome,reason}`. outcome은 `ready`, `needs_info`, `retry`; 원문 affiliate_url과 부분 추출한 상품 ID는 실패 응답에서도 보존한다.
- **Request**: `{id,bot_id,chat_id,user_id,message_id,provider,affiliate_url,evidence,state,version,created_at,updated_at,settings_version,slot_id,error,next_attempt_at,...}`. 외부 쓰기 전 `_claim`, `_pid`, `_owner`를 저장한다. 요청 ID는 봇·채팅·원본 메시지에서 결정적으로 생성한다.
- **Article**: `{id,request_id,style,title,body,body_format,evidence_refs,check_errors,policy_version,settings_version,evidence_snapshot,html,text}`. body_format은 `markdown-v1`; HTML·text는 서버가 만든다.
- **Slot**: `{id,target,provider,local_date,index,run_at,request_id,state}`. target은 WordPress 기본 주소이며 ID는 target·provider·local_date·index에 종속한다.
- **Publication**: `{id,request_id,target,slug,remote_id,remote_url,state,run_at,checked_at,snapshot,pending_snapshot,error,...}`. snapshot은 `{title,html,provider,affiliate_url}`; pending_snapshot은 가격 제거 등 동일 초안 업데이트의 응답 유실 판정용이다.
- **Notice**: `{id,request_id,event_key,chat_id,text,reply_to_message_id,force_reply,state,attempts,next_attempt_at,telegram_message_id,error,...}`. state는 pending/sending/sent/failed/cancelled, 외부 전송 claim 포함. cancelled는 답변 완료 후 아직 전송되지 않은 오래된 질문에 사용한다. `/start`·`/status` 등 상품 요청이 없는 응답은 request_id=null이고 event_key를 봇·입력 메시지에서 만든다.

키는 `<짧은 접두어>_<sha256의 앞 40자>`로 만들어 기존 `Store.get`의 ID 64자 제한과 전역 기본키를 지킨다. 클레임 토큰과 상태 버전을 검사한 완료 갱신만 허용한다. 네트워크 결과를 오래된 dict 전체로 덮어쓰지 않는다.

테스트 공통 데이터는 `tests/automation_fixtures.py`에 둔다: `make_settings(**schedule_overrides) -> dict`, `make_evidence(facts=None, **overrides) -> dict`, `message_update(update_id,text,*,message_id=None,chat_id=10,user_id=10,entities=None,reply_to=None) -> dict`. 합성 쿠팡/토스 URL만 사용하고, URL 엔티티 위치를 검증하는 테스트에서는 기대 오프셋을 숫자 리터럴로 직접 넣는다.

### Task 1: 영속 작업·설정과 서비스별 슬롯

**Files:** Create `studio/automation_schedule.py`, `studio/automation_store.py`, `tests/automation_fixtures.py`, `tests/test_automation_store.py`, `tests/test_automation_schedule.py`.

**Interfaces:**
- `validate_settings(body: dict) -> dict`; 양수/0 편수는 bool이 아닌 int, 각 편수 ≤ 시간 범위의 분 수, 시간은 `HH:MM`, start < end, 유효 IANA 시간대. writing 문자열은 각각 500자, extra/category 지침은 항목당 2,000자·최대 20개.
- `slots_for_day(settings: dict, day: date, target: str) -> list[dict]`; `claimable(slot: dict, at: datetime) -> bool`.
- `record_id(prefix: str, *parts: str) -> str`; `get_optional(store,c,kind,id) -> dict|None`.
- `get_settings(store,at) -> dict`; `save_settings(store,body,expected_version,at) -> dict`; 충돌 시 `VersionConflict`.
- `claim(store,c,kind,id,states,at) -> dict|None`; 반환 row에 새 `_claim` 포함. 살아 있는 소유자를 시간 만료만으로 빼앗지 않는다.
- `finish(store,c,kind,id,claim_token,changes) -> dict`; `enqueue_notice(store,c,request_id:str|None,event_key,text,*,chat_id,reply_to_message_id=None,force_reply=False) -> dict`.
- `public_state(store,at,*,target:str|None=None) -> dict`; settings, 서비스별 counts, requests, notifications, next_slots를 반환하되 내부 소유권 필드는 제외한다. next_slots는 target이 설정돼 있을 때 계산하며 호출자는 현재 WordPress 기본 주소를 전달한다. 네트워크·비밀값을 조회하지 않는다.

- [ ] **Step 1 — 실패 테스트 작성**
  - `test_service_limits_and_preparation_window`: 기본 시간 범위에서 쿠팡 3/토스 2 설정의 UTC 슬롯 목록과 배정 경계를 검사한다.
  ```python
  s = make_settings(daily_limits={'coupang': 3, 'toss': 2})
  rows = slots_for_day(s, date(2026, 10, 20), 'https://wp.example.test')
  assert [r['run_at'] for r in rows if r['provider']=='coupang'] == [
      '2026-10-20T00:00:00+00:00', '2026-10-20T04:00:00+00:00', '2026-10-20T08:00:00+00:00']
  assert len([r for r in rows if r['provider']=='toss']) == 2
  first = next(r for r in rows if r['provider']=='coupang')
  assert claimable(first, datetime.fromisoformat('2026-10-19T23:45:00+00:00'))
  assert claimable(first, datetime.fromisoformat('2026-10-19T23:55:00+00:00'))
  assert not claimable(first, datetime.fromisoformat('2026-10-19T23:55:01+00:00'))
  ```
  - `test_zero_invalid_counts_and_schedule`: 0이면 슬롯 없음; bool·음수·분 수 초과·역전 시간·잘못된 시간대는 ValueError.
  - `test_pending_schedule_uses_old_timezone_midnight`: 기존 Seoul 설정을 2026-10-20 14:50Z에 변경하면 15:00Z부터 적용, 같은 날짜 슬롯 ID 유지.
  - `test_dst_gap_and_fold_do_not_duplicate_slots`: New York의 2026-03-08 02:00은 건너뛰고, 2026-11-01 01:00은 fold=0 한 번만 사용한다. UTC round-trip으로 존재 여부 확인.
  - `test_claim_and_notice_are_durable_and_exclusive`: 두 Store 인스턴스 중 한 claim만 성공, 오래된 토큰 finish 거절, 같은 event_key 알림 한 개, 재시작 후 저장된 상태 유지.
- [ ] **Step 2 — 실패 확인**: `.venv/bin/python -m pytest tests/test_automation_store.py tests/test_automation_schedule.py -q --tb=short`; 신규 모듈/함수 부재 또는 계약 assertion 실패 확인.
- [ ] **Step 3 — 최소 구현**: 기존 `Store.connect/put/rows`와 `process_owner/owner_alive` 재사용. 슬롯은 start + floor(index × 범위 분 / 편수), 종료 시각 제외. 초기에 활성 일정은 즉시 저장하고 이후 일정 필드만 다음 날 적용한다. enabled는 즉시, writing은 다음 새 작업에 적용한다.
- [ ] **Step 4 — 통과 확인**: Step 2 명령과 `.venv/bin/python -m pytest tests/test_container_recovery.py tests/test_config.py -q`에서 0 failed.
- [ ] **Step 5 — diff 검토**: SQLite 전역 키·경쟁 claim·일정 변경 시점과 필드 이름을 공통 계약에 대조한다.

### Task 2: 원문 링크 보존과 공개 상품 근거

**Files:** Create `studio/product_info.py`, `tests/test_product_info.py`.

**Interfaces:**
- `classify_link(url: str) -> str`; 유효한 제휴 URL이면 coupang/toss, 지원하지 않는 주소는 ValueError.
- `fetch_page(url: str) -> dict`; `{final_url,status,headers,body:bytes}`, 부분 주소 확인 후 HTTP 차단도 반환한다.
- `parse_product(page: dict, at: datetime) -> dict` (Evidence); `parse_description(text: str, at: datetime) -> dict` (Evidence의 입력된 필드).
- `merge_evidence(existing: dict, supplied: dict) -> dict`; `inspect_link(url: str, at: datetime) -> dict` (Inspection).
- 허용 호스트는 입구 `link.coupang.com`, `coupa.ng`, `toss.im`, `toss.shopping`; 상품 조회 리다이렉트에 `www.coupang.com`, `m.coupang.com` 추가. 같은 서비스의 허용 호스트만 이동한다. 토스 상품 주소는 `/t/<양수 ID>`와 `k` 추적 값 또는 `referrer=affiliate`를 확인한다.

- [ ] **Step 1 — 실패 테스트 작성**
  - `test_blocked_landing_retains_original_link_and_partial_identity`: 합성 단축 링크가 302 뒤 상품 URL의 403으로 연결될 때 다음을 검사한다.
  ```python
  result = inspect_link('https://link.coupang.com/a/fixture?subId=case%2B1', at)
  assert result['outcome'] == 'needs_info'
  assert result['affiliate_url'] == 'https://link.coupang.com/a/fixture?subId=case%2B1'
  assert result['evidence']['product_key'] == 'coupang:123:456'
  assert result['evidence']['name'] == ''
  assert result['evidence']['price'] is None
  ```
  - `test_jsonld_and_og_use_only_matching_product`: URL의 상품·옵션과 다른 Product/Offer는 제외; 이름만 있는 OG는 needs_info; 상품별 사실이 있는 JSON-LD는 ready. 가격 구간·다중 옵션 가격·KRW 외 통화는 미확인으로 둔다.
  - `test_pinned_connection_blocks_redirect_and_dns_rebinding`: private/loopback/link-local/IPv4-mapped IPv6 결과는 연결 0회; 공개 주소 검증 후 실제 TCP 대상은 그 IP, TLS SNI/Host는 원래 호스트; 새 DNS 응답에 다시 연결하지 않는다.
  - `test_total_deadline_redirect_limit_and_decoded_size`: clock 진행으로 전체 20초 초과, 6번째 redirect, gzip 해제 후 2MiB+1, identity 2MiB+1 모두 bounded failure; 경계 2MiB 응답 허용.
  - `test_description_merges_only_supplied_fields`: 반복 `특징:`과 bullet을 사실로 수용, 동일 사실 중복 제거; 기존 이름 충돌은 conflicts에 기록; 가격은 nullable 양의 원 정수; 캡션의 제휴 URL은 사실에서 제외한다.
- [ ] **Step 2 — 실패 확인**: `.venv/bin/python -m pytest tests/test_product_info.py -q --tb=short`.
- [ ] **Step 3 — 최소 구현**: 기본 URL 검사는 `safe_url`을 재사용하고 추가 호스트·경로 검사를 한다. 내부 `_PinnedHTTPSConnection(host,ip,deadline)`을 표준 HTTPSConnection 기반으로 만들고 검증된 IP에 연결한 소켓을 원래 호스트의 SNI·인증서 검증으로 감싼다. DNS는 최대 2개 작업자의 전용 executor에서 `getaddrinfo`를 수행하며 남은 deadline으로 Future를 기다리고, 시간 초과 시 대기 작업을 취소한다. executor context manager의 종료 대기로 deadline을 넘기지 않는다. 늦게 끝난 DNS 결과가 실제 연결을 시작하지 않게 한다. 연결·읽기·리다이렉트도 같은 deadline을 공유한다. streaming decompression·HTMLParser로 필요한 필드만 추출한다. 제품 정보를 읽는 과정에서 HTML/스크립트를 실행하지 않는다. 이름·사실을 모두 확보하면 ready, 403/불충분 정보는 needs_info, 일시 오류는 retry다.
- [ ] **Step 4 — 통과 확인**: Step 2와 `.venv/bin/python -m pytest tests/test_affiliate.py tests/test_boundaries.py -q`에서 0 failed. 외부 I/O 경계만 대체하고 실제 파서·정책 함수는 실행한다.
- [ ] **Step 5 — diff 검토**: 리다이렉트 보존, 압축 해제 한도, 응답 내용·가격·추적 값의 AI 입력 전파 여부를 점검한다.

### Task 3: Telegram 전송 계약과 영속 알림

**Files:** Create `studio/telegram.py`, `tests/test_telegram.py`; modify `.env.example`의 연결 예시, `README.md`의 Telegram 초기 연결 절.

**Interfaces:**
- `Telegram.from_env(environ: Mapping[str,str]) -> Telegram|None`; 모두 비어 있으면 None, 부분 설정은 안전한 ValueError. token과 양수 개인 chat/user ID를 검증하고 인스턴스의 `chat_id`, `user_id`는 int로 둔다.
- `Telegram.connect() -> int` (getMe의 bot_id 확인·인스턴스 저장 및 webhook 충돌 확인); `Telegram.updates(offset: int) -> list[dict]`; `Telegram.send(text: str, *, reply_to_message_id=None, force_reply=False) -> int` (전송된 message_id).
- `TelegramError(ValueError)`의 `retryable:bool`, `retry_after:int|None`, `code:str`만 전달하고 원격 본문/URL은 포함하지 않는다.
- `deliver_notice(store, client: Telegram, notice_id: str, at: datetime) -> dict`; claim·결과 저장, 외부 호출은 트랜잭션 밖.

- [ ] **Step 1 — 실패 테스트 작성**
  - `test_poll_and_send_wire_contract`: 고정 `https://api.telegram.org/bot<token>/...`만 사용, getUpdates offset/timeout=25/allowed_updates=['message']; connect는 getMe/is_bot 및 webhook 빈 URL 검사; sendMessage는 평문 1~4096자와 필요 시 ForceReply를 보낸다.
  - `test_errors_do_not_expose_token_or_follow_redirects`: 302/401/403/409/429/500, JSON ok=false, malformed result, timeout의 출력에 token·요청 URL 없음; 429의 retry_after 보존, 무한·음수·잘못된 타입 거절.
  - `test_notice_retry_survives_restart_without_republishing`: 같은 이벤트 두 번 저장해도 한 행, 전송 실패 후 같은 행을 재시도; 성공 message_id 저장; 소유권 살아 있을 때 두 번째 전송 없음.
  ```python
  sent = deliver_notice(store, telegram, notice_id, at)
  assert sent['state'] == 'sent'
  assert sent['telegram_message_id'] == 501
  assert len(send_requests) == 1
  assert publication_requests == []
  ```
- [ ] **Step 2 — 실패 확인**: `.venv/bin/python -m pytest tests/test_telegram.py -q --tb=short`.
- [ ] **Step 3 — 최소 구현**: 기존 httpx 패턴 사용, redirects/trust_env 비활성, JSON 응답 최대 1MiB, long polling 읽기 timeout 35초. auth/conflict는 연결 오류, retryable은 최초 요청 뒤 최대 3회(60/300/900초), 429는 유효 retry_after에 따른다. 아직 보내지 않은 질문이 이미 답변 완료된 요청이면 그 질문 알림은 생략 처리한다. 문서에 BotFather 토큰·사용자/채팅 ID 확인과 전용 봇 개인 대화 설정을 설명하고 예시는 빈 값만 넣는다.
- [ ] **Step 4 — 통과 확인**: Step 2와 `.venv/bin/python -m pytest tests/test_config.py tests/test_packaging.py -q`에서 0 failed.
- [ ] **Step 5 — diff 검토**: 알림 실패가 발행 서비스 호출로 연결되지 않고 오류가 비밀정보를 포함하지 않는지 점검한다.

### Task 4: 링크 접수·설명 답장·오프셋을 원자적으로 연결

**Files:** Create `studio/telegram_inbox.py`, `tests/test_telegram_inbox.py`.

**Interfaces:**
- Consumes Task 1 저장 함수, Task 2 분류/설명 병합, Task 3의 bot_id/chat_id/user_id.
- `extract_links(message: dict) -> list[str]`; URL 엔티티가 있으면 UTF-16 범위를 검증해 추출하고 text_link는 url 필드 사용. 엔티티가 없는 텍스트는 명시적인 https URL만 추출한다.
- `receive_update(store, client: Telegram, update: dict, at: datetime) -> str|None`; 결과는 관련 request_id 또는 None. 외부 네트워크 호출 없이 수신 기록·알림·cursor를 커밋한다.
- `question_text(request: dict) -> str`; `enqueue_info_question(store,c,request:dict) -> dict` (Notice). 누락 필드와 요청 ID, 답장 양식 포함.

- [ ] **Step 1 — 실패 테스트 작성**
  - `test_unicode_entities_preserve_exact_url`: text=`'😀 링크 https://link.coupang.com/a/fixture'`, entity offset=6과 URL의 리터럴 UTF-16 length로 원문 URL 일치; 잘못된 surrogate 경계는 안전하게 거절한다.
  - `test_authorization_precedes_lookup`: 다른 chat/user, bot 발신, 채널·그룹 입력은 요청·조회·알림 0개이며 cursor만 진행한다.
  - `test_replay_and_duplicate_link_reuse_one_request`: 같은 update 반복 및 같은 링크의 다른 message는 요청 1개; 재전송 알림은 원래 요청 ID를 가리킨다. 다른 지원 링크 2개는 요청을 만들지 않고 분리 전송 안내.
  - `test_reverse_order_replies_and_lost_question_ack`: A/B 질문에 B/A 순서로 답하고, B 질문의 send 응답은 유실돼 message_id를 모르는 상태에서도 질문의 발신 봇·chat·request ID를 검증해 연결한다.
  ```python
  assert request_a['evidence']['name'] == '합성 상품 A'
  assert request_b['evidence']['name'] == '합성 상품 B'
  assert request_a['state'] == request_b['state'] == 'ready'
  assert cursor['next_offset'] == last_update_id + 1
  ```
  - `test_crash_rolls_back_cursor_with_request`: 알림 저장 중 예외를 일으켜 요청·cursor 둘 다 롤백; 같은 update를 다시 처리하면 한 번 접수. malformed/미지원 update도 확정된 무시 처리 후 다음 update 수신.
  - `test_late_reply_does_not_change_scheduled_snapshot`: 준비/정보대기일 때만 보충; 이미 작성 중·예약·발행이면 새 본문을 덮어쓰지 않고 현재 상태 안내. 사진만 있으면 텍스트 요청, caption 텍스트는 처리.
- [ ] **Step 2 — 실패 확인**: `.venv/bin/python -m pytest tests/test_telegram_inbox.py -q --tb=short`.
- [ ] **Step 3 — 최소 구현**: 원본 message에서 결정적인 request ID 생성, 원문 링크 중복은 기존 행 찾기. update 정렬 후 각 수신 작업과 next_offset 갱신을 같은 트랜잭션에서 수행한다. 원문 설명을 저장하고 상품 조회 단계에서 병합한다. `/start`와 `/status`도 DB 알림으로 응답한다. 질문은 사용자가 보충한 근거 revision마다 한 번 만들며, conflict 필드는 같은 질문에 대한 답장으로 해당 필드를 명시하면 해소한다.
- [ ] **Step 4 — 통과 확인**: `.venv/bin/python -m pytest tests/test_telegram_inbox.py tests/test_telegram.py tests/test_automation_store.py -q`에서 0 failed.
- [ ] **Step 5 — diff 검토**: 질문 문구에서 request ID를 찾는 것만으로 연결하지 않고 Telegram 발신 봇과 chat·등록 user를 모두 검사하는지 확인한다.

### Task 5: 상황별 글 작성·렌더링과 공유 AI 한도

**Files:** Create `studio/writing_policy.py`, `tests/test_writing_policy.py`, `tests/test_ai_budget.py`; modify `studio/ai.py`의 사용량 helper, `studio/core.py:275-309`의 AI 작업 예약.

**Interfaces:**
- `reserve_ai_operation(store,c,subject_id:str,limit:int,at:datetime,*,scope:str) -> dict` in `studio/ai.py`; `operation`에 type='ai', scope, subject_id, state, created_at, process owner를 저장한다. manual scope의 기존 draft/product version 필드는 호출자가 같은 트랜잭션 안에서 붙인다.
- `choose_style(evidence:dict,settings:dict) -> str` (intro/guide/concise); `compose(client:OpenAIClient,evidence:dict,settings:dict,*,feedback:tuple[str,...]=()) -> dict`.
- `check_article(article:dict,evidence:dict) -> list[str]`; `render_article(article:dict,evidence:dict,provider:str,affiliate_url:str) -> dict` returning html/text.
- `prepare_evidence(evidence:dict,run_at:datetime) -> dict`; 미래 발행 시점에 만료될 가격 제거, 원본 Evidence는 보존한다.

- [ ] **Step 1 — 실패 테스트 작성**
  - `test_style_and_prompt_use_evidence_and_config`: 사실 1/2개는 concise, 3개는 intro, 3개+purpose는 guide; 명시 category만 지침 매칭하고 unknown은 일반. 실제 HTTP request를 캡처해 system/user 분리, JSON Schema의 title/body/evidence_refs, 원문 링크·가격 본문 배제 지침 확인.
  - `test_generated_claims_and_refs_are_checked`: 허용되지 않은 수치, 없는 evidence_ref, HTML/본문 링크, 꾸민 사용 후기, 중복/빈 근거 거절; 상품명 안의 근거 수치는 허용한다. 긴 글은 최대 2,000자, 짧은 글 최대 700자이며 목표 분량보다 짧아도 근거가 충분하면 통과한다.
  - `test_render_uses_original_link_and_source_type`: HTML 구매 href를 파싱해 원문 query와 일치, 고지는 소개보다 앞, page와 user 출처 표시 구분, `<script>` 사용자 설명은 실행 가능한 HTML이 되지 않음.
  - `test_manual_and_automation_share_last_daily_credit`: ThreadPoolExecutor와 서로 다른 Store 연결로 limit=1 동시 예약; 성공 1/한도 예외 1, 외부 AI 요청 1. 자동 operation에 draft_id가 없어도 수동 경로 KeyError 없음. 실패 작업도 UTC 날짜별 합계에 포함.
  ```python
  assert successful_reservations == 1
  assert len(ai_requests) == 1
  assert prepare_evidence(evidence, at + timedelta(hours=24, seconds=1))['price'] is None
  ```
- [ ] **Step 2 — 실패 확인**: `.venv/bin/python -m pytest tests/test_writing_policy.py tests/test_ai_budget.py -q --tb=short`.
- [ ] **Step 3 — 최소 구현**: 자동 작성은 기존 client.request를 통해 호출하되 수동 generate의 220/450자 계약은 유지한다. 자동 출력 max_tokens는 긴 글 4096/짧은 글 1600, JSON 입력은 UTF-8 12,000바이트 이내로 제한한다. 이름·facts의 원문 링크를 제거한 allowlist 자료만 넣고 초과 입력은 설명을 줄이도록 알린다. 기존 `quantities`, `render_body`, disclosure 상수, article document 생성기를 재사용한다. `Store.generate_ai`의 사용량 예약을 helper로 바꾸고 running 중복 검사는 `x.get('draft_id')`로 수행한다. 자동 검사는 generated title/body 대상으로 수행해 근거 안의 경고 문구 때문에 자동 실패시키지 않는다.
- [ ] **Step 4 — 통과 확인**: `.venv/bin/python -m pytest tests/test_writing_policy.py tests/test_ai_budget.py tests/test_ai.py tests/test_ai_modes.py tests/test_factual_quantities.py tests/test_formatting.py -q`에서 0 failed.
- [ ] **Step 5 — diff 검토**: 수동 승인/적용 흐름에 변화가 없고, 자동 작성이 자료에서 지시를 실행하거나 가격·제휴 링크를 생성하지 않는지 확인한다.

### Task 6: WordPress 단일 글 생성·예약·응답 유실 복구

**Files:** Create `studio/automation_publish.py`, `tests/test_automation_publish.py`; modify `studio/wordpress.py:27-53`의 요청/검증 확장.

**Interfaces:**
- `WordPress.request(method,suffix,payload=None,*,params=None) -> dict|list`; GET의 context=edit와 호출자 params를 병합하며 기존 호출은 동일 동작.
- `WordPress.verify_snapshot(data:dict,snapshot:dict) -> dict`; title/content raw와 disclosure·보존된 링크를 비교한다.
- `ensure_draft(store,request_id:str,client:WordPress,at:datetime) -> dict` (Publication); 기존 row가 있으면 조회/재사용, 새 생성은 raw 응답의 id를 검증 즉시 저장 후 readback.
- `schedule_post(store,request_id:str,client:WordPress,run_at:datetime,at:datetime) -> dict`; `reconcile_post(store,request_id:str,client:WordPress,at:datetime) -> dict`.
- `replace_draft_snapshot(store,request_id:str,client:WordPress,new_snapshot:dict,at:datetime) -> dict`; 이미 확인한 비공개 draft의 가격 만료 시 같은 remote_id만 갱신한다.

- [ ] **Step 1 — 실패 테스트 작성**
  - `test_one_post_for_repeated_ensure_and_schedule`: 동일 request와 재시작에서 create POST 1회, 예약 POST 1회, 원문 query 보존; future는 scheduled, publish+안전한 사이트 게시 URL 확인 후 published.
  - `test_create_response_loss_reconciles_by_slug`: 생성 성공 후 연결 유실, 다음 호출은 GET slug 조회만 수행; status[]=draft/future/publish/pending/private/trash를 요청하고 exact slug·raw 비교. 0개/여러 개/수정된 본문은 uncertain, 새 create 0회.
  - `test_id_is_persisted_before_readback`: create 성공 뒤 read GET 실패/프로세스 중단에도 DB에 remote_id 있음; 다음 조회는 ID 사용.
  - `test_rollover_removes_expired_price_from_same_draft`: 확인된 원격 draft가 다음 날로 이월되면 price 제외 snapshot으로 같은 id 갱신; 그 POST 응답 유실 후 readback은 old/new snapshot 중 어느 상태인지 판정, 다른 본문이면 uncertain.
  ```python
  assert first['remote_id'] == after_restart['remote_id'] == 42
  assert len(create_requests) == 1
  assert '19,900원' not in final_remote['content']['raw']
  assert all(r.path.endswith('/42') for r in content_update_requests)
  ```
  - `test_site_change_or_public_post_prevents_mutation`: target 변경·raw 불일치·이미 publish인 글은 draft 갱신/재예약 0회. 검증된 기존 요청의 공개 상태만 기록.
- [ ] **Step 2 — 실패 확인**: `.venv/bin/python -m pytest tests/test_automation_publish.py -q --tb=short`.
- [ ] **Step 3 — 최소 구현**: 기존 `normalize`와 인증 transport 재사용. 요청 ID 기반 slug와 원격 쓰기 단계를 먼저 기록한다. slug 조회에 query가 GET context 파라미터로 유실되지 않게 한다. publication_snapshot을 이전/예정 둘 다 저장한 다음 동일 초안 갱신; readback으로 어느 snapshot인지 확정한다. 어떤 외부 변경도 일반적인 자동 retry로 덮어쓰지 않는다. 예약 시각은 UTC 초 단위로 저장하고 최소 현재+5분; 원격 link는 WP target과 같은 origin의 안전한 URL만 받는다.
- [ ] **Step 4 — 통과 확인**: `.venv/bin/python -m pytest tests/test_automation_publish.py tests/test_wordpress.py tests/test_publication.py tests/test_review_regressions.py -q`에서 0 failed.
- [ ] **Step 5 — diff 검토**: 응답 유실·재시작 분기마다 가능한 요청 순서를 확인하고 새 글 생성 또는 공개 글 비공개 전환이 섞이지 않는지 검토한다.

### Task 7: 자동 실행기·HTTP 경로·종단 흐름 연결

**Files:** Create `studio/automation.py`, `studio/automation_routes.py`, `tests/test_automation.py`, `tests/test_automation_http.py`; modify `studio/app.py:61-80,124-128,222-224`, `README.md`, `.env.example`, `compose.yaml`의 로컬 문맥 크기 설정.

**Interfaces:**
- `Automation(store,telegram,ai,wordpress,*,clock=None)`; `.tick(at=None) -> int`는 처리한 단계 수, `.poll_once() -> int`는 저장한 업데이트 수, `.deliver_once(at=None) -> int`는 시도한 notice 수.
- `async .start() -> None`/`async .stop() -> None`는 수신/처리/전달 작업자의 수명 관리; `.status() -> dict`는 configured/running/last_error/last_success_at만 공개한다. FastAPI lifespan에서 각각 await 호출한다.
- `install_routes(app,store,automation)` in `automation_routes.py`.
- `GET /api/automation` → `{settings,counts,requests,notifications,next_slots,connections,worker}`; `PUT /api/automation/settings` → 검증한 versioned Settings; `GET /api/automation/requests/{id}` → 근거·Article·Publication·Notice 상세; `POST /api/automation/requests/{id}/refresh` → 읽기 전용 원격 결과 재조회. 기존 middleware의 CSRF/host/origin/body 제한 적용.

- [ ] **Step 1 — 실패 테스트 작성**
  - `test_link_only_and_blocked_reply_publish_without_affiliate_keys`: 테스트 HTTP transport에만 합성 Telegram/상품/AI/WP 응답을 주고 실제 수신·파서·DB·정책·실행기를 실행한다. 정상 쿠팡 1건과 403 토스 1건의 텍스트 보충을 통과시킨다.
  ```python
  assert sorted(r['state'] for r in state['requests']) == ['published', 'published']
  assert state['counts']['coupang']['published'] == 1
  assert state['counts']['toss']['published'] == 1
  assert len(wp_creates) == 2
  assert all('affiliate_open_api' not in str(r.url) and 'sharelink.toss.im/openapi' not in str(r.url)
             for r in external_requests)
  assert manual_store_state['drafts'] == []  # 수동 승인 기록을 만들지 않음
  ```
  - `test_stop_resume_quota_and_service_failure`: enabled=false에서도 입력 저장·예약 조회·알림 가능, 새 compose/POST 0회; quota 소진은 다음 UTC 날짜의 유효 슬롯으로 이월; 쿠팡 오류가 토스 ready 처리를 막지 않음; 잘못된 본문은 최대 2번 compose 후 failed.
  - `test_restart_after_each_write_boundary`: 접수/AI 사용량 예약/create POST/schedule POST/notice POST 경계에 저장·중단·재시작을 넣어 같은 요청·슬롯·원격 ID를 유지한다. 살아 있는 claim은 가져오지 않는다.
  - `test_schedule_changes_and_late_rollover_preserve_request`: 날짜·DST·일정 변경·이월 후 같은 요청의 slug 유지, 새 슬롯 배정은 이전 미확정 발행이 없을 때만 허용.
  - `test_http_settings_validation_and_no_secret_leak`: CSRF 없음 403, bool count/잘못된 JSON 400, 오래된 version 409, 부분 Telegram 설정도 앱 시작 성공·연결 오류 표시, GET은 원격 mutation 0회, 비밀 토큰이 모든 응답에 없음.
  - `test_workers_shutdown_and_contain_errors`: 장시간 poll과 실패한 한 작업이 나머지 작업자·기존 ZIP 실행기를 중지시키지 않으며 stop 후 새 외부 mutation을 시작하지 않음.
- [ ] **Step 2 — 실패 확인**: `.venv/bin/python -m pytest tests/test_automation.py tests/test_automation_http.py -q --tb=short`.
- [ ] **Step 3 — 최소 구현**: 독립 asyncio 작업에서 blocking I/O를 제한된 스레드 실행으로 처리한다. 상태 전이마다 claim을 검증하고 가장 오래된 eligible 요청을 처리한다. read 재시도는 60/300/900초, 한도는 다음 UTC reset, 정보 부족은 질문으로 전환한다. 재시작 시 죽은 소유자의 compose는 이미 예약한 usage를 유지하며 저장 결과가 없으면 남은 작성 시도 예산 내에서 재개한다. 실제 AI 요청을 다시 보내는 시도마다 새 usage를 예약하며 이전 호출을 무료로 재사용하지 않는다. 원격 쓰기는 GET부터 복구한다. 예약 1분 전까지 최대 5분 간격, 그 이후 완료 전까지 1분 간격 조회; 15분 지연 이벤트는 한 번. 설정 오류는 공개 status로만 보여주고 수동 기능 시작을 막지 않는다. 로컬 긴 글 운영에는 문서에서 16,384 문맥 설정을 안내하고 Compose의 기존 4,096 기본값은 `${OLLAMA_CONTEXT_LENGTH:-4096}`로 환경설정 가능하게 한다.
- [ ] **Step 4 — 통과 확인**: `.venv/bin/python -m pytest tests/test_automation.py tests/test_automation_http.py tests/test_http.py tests/test_startup.py tests/test_container_recovery.py tests/test_config.py -q`에서 0 failed.
- [ ] **Step 5 — diff 검토**: 한 공개 발행의 식별자가 입력 메시지에 종속하는지, 편수·시간대·슬롯·재시작이 그것을 변경하지 않는지 확인한다.

### Task 8: 자동 운영 화면·사용 안내·브라우저 검증

**Files:** Create `studio/static/automation.js`, `studio/static/automation.css`, `tests/automation-dom.cjs`, `tests/serve_automation_fixture.py`, `tests/e2e_automation.mjs`; modify `studio/static/index.html`, `tests/ui-copy.cjs`, `README.md`.

**Interfaces:**
- `automation.js`는 `measurements.js`처럼 독립 IIFE이며 `nav [data-view="automation"]`에서 상태를 읽는다. 기존 `app.js`의 공통 view 전환을 사용한다.
- DOM: `#view-automation`, `#automation-form`, `#automation-fields`, `#automation-notice`, `#automation-requests`, `#automation-detail`, `#automation-refresh`, `#automation-preview` (sandbox iframe).
- 폼은 Settings의 writing/schedule/enabled를 편집하며 저장할 때 version을 전송한다. 연결 상태에는 비밀키 입력란을 만들지 않는다. 자동 상태 갱신은 편집 중인 값·버전을 덮어쓰지 않는다.
- `/refresh`는 해당 작업의 원격 조회만 요청한다. 완료 URL은 서버에서 검증한 WordPress origin만 링크로 표시한다. 발행 미리보기는 확인된 Publication.snapshot을 사용하며 원격 전송 전에는 Article.html을 사용한다. 과거 근거의 가격과 실제 게시 스냅샷의 가격 제외를 구별한다.

- [ ] **Step 1 — 실패 DOM/브라우저 테스트 작성**
  - `configures independent counts and retains edits on refresh`: 10/5 저장 payload, 0 서비스 대기, 다음 적용일 표시; 편집 중 refresh와 409 뒤에도 원래 입력 유지; 더블 클릭 PUT 1회; 저장 없이 UI 열기는 POST 0회.
  - `shows awaiting information separately from scheduled and published`: 요청별 질문·근거·확인 시각·글·알림 실패 표시; 예약을 발행 완료로 세지 않음; 사용자 텍스트 HTML 실행 0회.
  - `requires configured automation connections but not affiliate API keys`: Telegram/AI/WP 설정만으로 활성화 가능하며 쿠팡·토스 configured=false 때문에 막지 않음.
  - `e2e_automation.mjs`: 실제 앱에 연결한 브라우저에서 설정 → 합성 Telegram update → 403 정보 요청 → 답장 → 예약 → 가상 시각 전진 후 publish → 게시 URL·알림 확인. 390px 화면에서 가로 넘침과 pageerror 0 확인.
  - 테스트 서버만 `/__fixture/updates`, `/__fixture/clock` 제어 경로를 등록하며 기존 loopback·CSRF 경계를 적용한다. 실제 외부 호출은 transport에서 대체하고 운영 앱에는 제어 경로를 추가하지 않는다.
- [ ] **Step 2 — 실패 확인**: `node --test tests/automation-dom.cjs`; 이후 `CHROMIUM_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' node tests/e2e_automation.mjs`에서 신규 화면/동작 부재로 실패 확인.
- [ ] **Step 3 — 최소 구현과 안내**: 기존 카드·폼·notice·dirty/busy 패턴을 사용한다. 6개 메뉴가 모바일 화면에 들어가도록 자동 운영 CSS에서 nav의 padding/폭을 조절한다. 데이터는 textContent로 표시하고 생성 미리보기는 sandbox srcdoc로 표시한다. README·시작하기에는 Telegram 링크/설명 답장, 별도 편수, 서버 상시 실행, 긴 글용 로컬 문맥 설정, 설정됨/실제 발행 구분을 반영한다. 수동 화면의 승인 설명은 해당 수동 기능 범위로 유지한다.
- [ ] **Step 4 — 최종 검증** (하단 명령). 실패하면 원인을 좁혀 수정한 뒤 영향받은 검증만 반복한다. 모의 발급/발행과 실제 외부 계정 검증을 구별해 보고한다.
- [ ] **Step 5 — 최종 diff와 리뷰**: spec의 11번 검증 기준을 작업별 결과에 대조한다. 실행 방식에 따른 코드 리뷰를 받고 중요한 지적을 재현·수정한다. 사용자 변경과 무관한 파일이 포함되지 않았는지 확인한다.

## 최종 검증 명령

```sh
.venv/bin/python -m pytest -q --tb=short
node --test tests/v02-actions.cjs tests/v02-dom.cjs tests/v03-metrics.cjs tests/ui-copy.cjs tests/affiliate-dom.cjs tests/automation-dom.cjs
CHROMIUM_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' node tests/e2e_automation.mjs
CHROMIUM_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' npm run test:e2e
.venv/bin/python scripts/package.py
git diff --check
```

macOS에서 임시 산출물은 승인된 `/private/var/folders/20/nd89ms59071c56yt0jf3tvkm0000gn/T/opencode` 아래에 둔다. 다른 환경에서는 설치된 Chromium 경로를 지정한다. 이미 있는 의존성을 우선 사용하며 브라우저가 없으면 그 사실을 보고하고 실제 브라우저 검증을 통과했다고 쓰지 않는다. 포장 ZIP에는 새 Python/JS/CSS가 현재 allowlist로 들어가는지 확인한다.

## 계획 자체 검토와 실행 인계

- [x] Spec coverage: 수신·보충(3/4), 조회(2), 작성(5), 일정(1), 복구·발행(6/7), 화면(8), 전체 검증(7/8)을 대조함.
- [x] Step scan: 8개 작업 모두 파일/인터페이스와 5개 단계 존재, 테스트 입력·기대 결과와 구현 함수 결정. Markdown 구조·공백 검사 완료.
- [x] Type consistency: Settings.enabled, nullable Notice.request_id, Telegram.bot_id, public_state의 target, async start/stop 계약을 통일함.
- [x] Review Focus: 5개 항목을 각각 Task 4, 2, 6, 5, 1/7의 명명된 회귀 테스트에 연결함.
- [x] Proportion: 설계 216행 대비 계획 약 325행, 단일 상품 처리와 필요한 기존 코드 연결에 한정함.
- [ ] 사용자가 이 계획을 검토하고 실행 방식을 선택한 후 구현을 시작한다.

권장 실행 방식은 **현재 세션에서 순서대로 직접 구현**이다. 작업들이 같은 영속 데이터와 상태 전이 계약을 공유하고 현재 작업 트리에 병행 변경이 많아, 문맥을 유지하는 구현 후 전체 변경에 대한 독립 리뷰가 적합하다. 작업별 독립 구현·리뷰 방식도 선택할 수 있다.
