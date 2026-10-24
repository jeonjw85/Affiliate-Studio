# 상품 대표 이미지와 AI 본문 이미지 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 자동 운영 글에 상품 페이지 대표 이미지와 AI 생성 삽화를 붙여 WordPress에 업로드하고, 실패해도 이미지 없이 글을 발행한다.

**Architecture:** 이미지 준비는 글 작성과 분리된 `studio/images.py`가 맡는다. 상품 페이지에서 허용 호스트의 대표 이미지만 추출하고, OpenAI 호환 이미지 API로 본문 삽화를 만든 뒤, `studio/wp_media.py`가 WordPress 미디어에 한 번만 업로드한다. 글 스냅샷은 미디어 ID만 참조하며, 업로드 실패는 발행을 막지 않는다.

**Tech Stack:** Python 3.11+, 표준 라이브러리(base64, hashlib, struct), 기존 httpx, FastAPI, SQLite `records`, vanilla JS, pytest/JSDOM/Playwright.

**Spec:** `docs/superpowers/specs/2026-10-24-product-image-design.md`

## Global Constraints

- 대표 이미지는 상품 페이지에서만 추출한다. JSON-LD `Product.image` → `og:image` → `twitter:image` 순서로 하나만 쓴다.
- 본문 이미지는 AI 생성 삽화 1장이며, 본문 상단 제목 다음에 `AI 생성 이미지 · 설명용 삽화` 캡션과 함께 넣는다.
- `IMAGE_MODE`가 비어 있으면 AI 이미지 생성은 비활성이다. 이때 대표 이미지만 쓴다.
- 이미지 설정: `IMAGE_MODE`, `IMAGE_BASE_URL`, `IMAGE_MODEL`, `IMAGE_API_KEY`, `IMAGE_DAILY_LIMIT`. API 방식은 HTTPS만 허용한다.
- 본문 이미지 호출은 `POST /images/generations`에 `response_format=b64_json`으로 보낸다. URL 응답·외부 핫링크는 받지 않는다.
- 허용 형식은 JPEG·PNG·WebP, 최대 8MiB. 대표 이미지는 최소 200×200, 본문 이미지는 512×512 이상. 크기는 파일 헤더만 읽어 확인한다.
- 이미지 URL은 `https` 절대 주소만, 상품 서비스의 이미지 호스트 허용 목록만 요청한다. 기존 공개 IP 고정·리다이렉트 제한·시간 제한을 그대로 쓴다.
- 이미지 저장 파일명은 서버가 만든 `<request_id>-<sha256 앞 16자>.<ext>`만 쓴다.
- 같은 요청·같은 종류의 이미지는 한 번만 다운로드·생성·업로드한다. 글 보완에서도 기존 미디어를 유지한다.
- 이미지 준비·업로드 실패는 글 발행을 막지 않는다. 사유를 기록하고 이미지 없이 발행한다.
- WordPress 미디어 업로드 응답 유실은 목록 조회로 복구한다. 확인 불가면 새 업로드 없이 이미지 없이 발행한다.
- 이미지 생성 사용량은 기존 `operation` 기록에 `type='image'`, `scope='automation'`으로 UTC 날짜 기준 저장하고 `IMAGE_DAILY_LIMIT`로 제한한다.
- 사용자 요청 없는 커밋·푸시는 하지 않는다. 각 작업 끝은 관련 diff 검토다.

## Review Focus

1. 허용 목록 밖 호스트·데이터 URI·상대 경로·`javascript:` 이미지 URL이 실제 요청으로 이어지지 않아야 한다 — Task 1.
2. 쿠팡 페이지가 403이어도 글은 발행되고, 대표 이미지 없음 사유가 남아야 한다 — Task 4.
3. 같은 요청을 재시작·보완해도 미디어가 두 번 업로드되지 않아야 한다 — Task 3, 4.
4. AI 이미지 응답이 URL·HTML·잘못된 형식·과대 용량일 때 저장·핫링크 없이 건너뛰어야 한다 — Task 2.
5. 기존 이미지 없는 글·수동 글·비교 글 스냅샷이 이미지 추가 후에도 그대로 동작해야 한다 — Task 3, 4.

---

## 파일 구성

| 파일 | 책임 | 작업 |
| --- | --- | --- |
| `studio/images.py` 신규 | 허용 호스트 검사, 헤더 파싱, 다운로드, AI 생성, 파일 저장 | 1, 2 |
| `studio/wp_media.py` 신규 | WordPress 미디어 업로드·조회·중복 복구 | 3 |
| `studio/automation_media.py` 신규 | 미디어 기록·상태·재사용 결정 | 3 |
| `studio/product_info.py` 수정 | 페이지 파서에서 이미지 후보 추출 | 1 |
| `studio/automation_publish.py` 수정 | 스냅샷 `featured_media`·`body_media` 연결 | 4 |
| `studio/automation.py`, `studio/writing_policy.py` 수정 | 생성 프롬프트·본문 삽입·실패 처리 | 2, 4 |
| `studio/automation_routes.py`, `studio/static/automation.js` 수정 | 이미지 상태·썸네일·사용량 표시 | 5 |
| `studio/app.py`, `.env.example`, `README.md` 수정 | 이미지 설정·안내 | 2, 5 |

---

### Task 1: 페이지 대표 이미지 후보와 안전한 다운로드

**Files:**
- Create: `studio/images.py`
- Modify: `studio/product_info.py`
- Test: `tests/test_images.py`

**Interfaces:**
- `image_url(page: dict) -> str|None`: JSON-LD `Product.image` → `og:image` → `twitter:image` 순서로 첫 허용 URL.
- `allowed_image_url(url: str) -> str`: `https`, 이미지 호스트 허용 목록, 자격증명·쿼리 금지 규칙을 적용하고 정규화 URL 반환.
- `download_image(url: str) -> dict`: `{url, body, content_type, width, height, sha256}`. 헤더 파싱 실패·형식·용량·크기 위반은 `ValueError`.
- `parse_product`가 반환하는 Evidence에 `image_url: str|None`을 추가한다.
- `IMAGE_HOSTS = ('coupangcdn.com', 'coupang.com', 'toss.im', 'toss.shopping')`의 하위 호스트 허용. 실제 조사에서 확인되지 않은 호스트는 추가하지 않는다.

- [ ] **Step 1: 실패 테스트 작성**

```python
def test_candidate_order_and_single_choice():
    page = page_with(jsonld={'@type': 'Product', 'name': 'A', 'image': 'https://image9.coupangcdn.com/a.jpg'},
                     og='https://image9.coupangcdn.com/og.jpg')
    assert image_url(page) == 'https://image9.coupangcdn.com/a.jpg'
    assert image_url(page_with(og='https://image9.coupangcdn.com/og.jpg')) == 'https://image9.coupangcdn.com/og.jpg'
    assert image_url(page_with()) is None

@pytest.mark.parametrize('url', [
    'http://image9.coupangcdn.com/a.jpg',
    'https://evil.test/a.jpg',
    'https://user:pass@image9.coupangcdn.com/a.jpg',
    'data:image/png;base64,AAAA',
    'javascript:alert(1)',
    '/relative/a.jpg',
])
def test_rejects_unsafe_image_urls(url):
    with pytest.raises(ValueError):
        allowed_image_url(url)

def test_download_checks_type_size_and_header_dimensions(monkeypatch):
    png = png_bytes(width=300, height=300)
    result = download_image('https://image9.coupangcdn.com/a.png', opener=lambda url: Reply(
        200, png, {'content-type': 'image/png'}))
    assert (result['width'], result['height']) == (300, 300)
    assert result['sha256'] == hashlib.sha256(png).hexdigest()
    for reply in (Reply(200, png, {'content-type': 'text/html'}),
                  Reply(200, b'x' * (8 * 1024 * 1024 + 1), {'content-type': 'image/png'}),
                  Reply(200, png_bytes(100, 100), {'content-type': 'image/png'}),
                  Reply(302, headers={'location': 'https://evil.test/a.png'})):
        with pytest.raises(ValueError):
            download_image('https://image9.coupangcdn.com/a.png', opener=lambda url, r=reply: r)
```

`png_bytes`/`jpeg_bytes` helper는 실제 헤더를 만든다. `parse_product`가 `image_url`을 포함하는 테스트도 추가한다.

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_images.py -q`
Expected: `ModuleNotFoundError: studio.images`

- [ ] **Step 3: 최소 구현**

`image_url`은 기존 `_Meta`/`_products` 결과를 재사용해 후보를 얻는다. `allowed_image_url`은 `safe_url`과 호스트 접미사 검사를 함께 쓴다. `download_image`는 `product_info._https_get` 스타일의 고정 IP 연결을 재사용하되 응답 `content-type`과 8MiB 한도를 확인한다. JPEG(SOI + SOF), PNG(IHDR), WebP(VP8/VP8L/VP8X) 헤더만 읽어 크기를 구한다. 리다이렉트는 같은 허용 호스트로 최대 3회만 따른다.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_images.py tests/test_product_info.py -q`
Expected: PASS

- [ ] **Step 5: diff 검토와 커밋**

```bash
git add studio/images.py studio/product_info.py tests/test_images.py
git commit -m "feat: 상품 페이지 대표 이미지 후보 추출과 안전한 다운로드 추가"
```

---

### Task 2: AI 본문 이미지 생성과 저장

**Files:**
- Modify: `studio/images.py`, `studio/ai.py`, `studio/app.py`, `.env.example`
- Test: `tests/test_images.py`, `tests/test_image_generation.py`

**Interfaces:**
- `ImageClient.from_env(environ) -> ImageClient|None`; `IMAGE_MODE`가 비면 None, `api`·`local`만 허용.
- `ImageClient.generate(prompt: str, at: datetime) -> dict`: `{body, content_type, width, height, sha256}`. b64_json만 허용.
- `image_prompt(products: list[dict], settings: dict) -> str`: 상품명·카테고리·대상 독자만 사용. `제품 실물 사진 아님, 설명용 삽화, 텍스트·로고·얼굴 없음`을 포함.
- `save_image(data_dir: Path, request_id: str, kind: str, image: dict) -> dict`: 해시 파일명으로 저장하고 `{path, bytes, sha256}` 반환. 같은 파일은 다시 쓰지 않는다.
- `reserve_image_operation(store, c, subject_id, limit, at) -> dict`: `type='image'` 예약. 기존 `reserve_ai_operation`과 같은 방식.

- [ ] **Step 1: 실패 테스트 작성**

```python
def test_image_client_needs_complete_config():
    assert ImageClient.from_env({}) is None
    for env in ({'IMAGE_MODE': 'api'}, {'IMAGE_MODE': 'api', 'IMAGE_BASE_URL': 'http://x/v1',
                                        'IMAGE_MODEL': 'm', 'IMAGE_API_KEY': 'k'}):
        with pytest.raises(ValueError):
            ImageClient.from_env(env)

def test_generation_accepts_only_b64_json(remote):
    client = ImageClient.from_env(IMAGE_ENV)
    remote.reply({'data': [{'b64_json': base64.b64encode(png_bytes(600, 600)).decode()}]})
    result = client.generate('prompt', AT)
    assert result['width'] == 600
    assert json.loads(remote.requests[0].content) == {
        'model': 'fixture-image', 'prompt': 'prompt', 'size': '1024x1024',
        'n': 1, 'response_format': 'b64_json'}

def test_generation_rejects_urls_and_bad_images(remote):
    client = ImageClient.from_env(IMAGE_ENV)
    for payload in ({'data': [{'url': 'https://evil.test/a.png'}]},
                    {'data': [{'b64_json': base64.b64encode(b'<html>').decode()}]},
                    {'data': []}, {'error': {'message': 'fixture-key'}}):
        remote.reply(payload)
        with pytest.raises(ValueError):
            client.generate('prompt', AT)
    assert 'fixture-key' not in str(...)

def test_prompt_has_no_urls_prices_or_names_of_people():
    prompt = image_prompt([product(1, name='합성 상품', facts=['가격: 19,900원'])],
                          {'writing': {'audience': '정리하는 독자'}})
    assert '합성 상품' in prompt and '설명용 삽화' in prompt
    assert '19,900' not in prompt and 'http' not in prompt

def test_daily_image_limit_is_separate(tmp_path):
    # IMAGE_DAILY_LIMIT=1: second reservation fails; a new UTC day succeeds.
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_image_generation.py -q`
Expected: `ImageClient` 없음

- [ ] **Step 3: 최소 구현**

`ImageClient`는 기존 `OpenAIClient`와 같은 주소·키 검증을 재사용하되 `/images/generations`만 호출한다. 응답 최대 16MiB, `b64_json` 디코딩 후 8MiB·형식·크기를 검사한다. 오류 메시지에 키·URL을 넣지 않는다. `.env.example`에 `IMAGE_*` 빈 값을 추가하고 `app.py`에서 `ImageClient.from_env`로 생성해 `Automation`에 전달한다. 설정 오류는 앱 시작을 막지 않고 `connections.image_error`로 표시한다.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_images.py tests/test_image_generation.py tests/test_config.py -q`
Expected: PASS

- [ ] **Step 5: diff 검토와 커밋**

```bash
git add studio/images.py studio/ai.py studio/app.py .env.example tests/test_images.py tests/test_image_generation.py
git commit -m "feat: OpenAI 호환 이미지 생성과 로컬 저장 추가"
```

---

### Task 3: WordPress 미디어 업로드와 재사용

**Files:**
- Create: `studio/wp_media.py`, `studio/automation_media.py`
- Test: `tests/test_wp_media.py`

**Interfaces:**
- `WordPress.upload_media(filename, content: bytes, content_type) -> dict`: `{id, source_url}`. `posts`가 아닌 `/wp-json/wp/v2/media`로 보낸다. 기존 인증·리다이렉트 금지 재사용.
- `WordPress.find_media(filename: str) -> list[dict]`: `search` 또는 `per_page=100` 목록에서 파일명이 일치하는 항목을 반환한다.
- `media_record(store, request_id, kind) -> dict|None`, `save_media(store, record) -> dict`
- `prepare_media(store, request_id, kind, image, client, data_dir, at) -> dict`: `unavailable`·`uploaded` 상태를 결정하고 중복 업로드를 막는다.
- `media_bytes(record) -> bytes`: 로컬 파일을 읽는다. 파일이 없으면 `ValueError`.

- [ ] **Step 1: 실패 테스트 작성**

```python
def test_media_upload_uses_media_endpoint_and_records_id(tmp_path):
    result = prepare_media(store, 'req', 'featured', image, wp, tmp_path, AT)
    assert result['state'] == 'uploaded' and result['remote_id'] == 7
    assert remote.paths == ['/wp-json/wp/v2/media']

def test_upload_happens_once_across_restarts(tmp_path):
    prepare_media(...)
    again = prepare_media(...)
    assert again['remote_id'] == 7
    assert len([p for p in remote.paths if p.endswith('/media')]) == 1

def test_lost_upload_response_reuses_matching_media(tmp_path):
    wp.lose_next = True
    with pytest.raises(ValueError):
        prepare_media(...)
    record = prepare_media(...)
    assert record['remote_id'] == 7
    assert len([p for p in remote.paths if p.endswith('/media')]) == 1

def test_missing_local_file_is_unavailable(tmp_path):
    (tmp_path / 'media' / record['local_path']).unlink()
    assert prepare_media(...)['state'] == 'unavailable'
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_wp_media.py -q`
Expected: `ModuleNotFoundError: studio.wp_media`

- [ ] **Step 3: 최소 구현**

`WordPress.request`는 현재 글 경로가 고정이므로 미디어용 `request_media`를 별도로 둔다. 업로드는 `Content-Disposition`과 바이트 본문을 보내고 응답 `id`·`source_url`을 검증한다. 응답 유실 시 `find_media`로 같은 파일명을 찾고, 정확히 1개면 재사용한다. `prepare_media`는 저장 기록이 `uploaded`면 그대로 반환하고, `fetched`/`generated`면 업로드한다. 업로드 실패는 `unavailable`로 저장한다.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_wp_media.py tests/test_wordpress.py -q`
Expected: PASS

- [ ] **Step 5: diff 검토와 커밋**

```bash
git add studio/wp_media.py studio/automation_media.py studio/wordpress.py tests/test_wp_media.py
git commit -m "feat: WordPress 미디어 업로드와 중복 없는 재사용 추가"
```

---

### Task 4: 글 스냅샷에 대표·본문 이미지 연결

**Files:**
- Modify: `studio/automation.py`, `studio/writing_policy.py`, `studio/automation_publish.py`
- Test: `tests/test_automation_images.py`

**Interfaces:**
- `render_article(article, products, *, settings, body_image=None)`: `body_image`가 있으면 제목 다음에 `<figure>`를 넣는다.
- Snapshot: `featured_media`, `body_media`, `body_image_html` 추가. 기존 스냅샷은 `None`으로 읽는다.
- `Automation._prepare_images(request, products, at) -> dict`: `{featured, body, reasons:[...]}`. 실패는 빈 값과 사유로 반환하고 예외를 올리지 않는다.
- `ensure_draft`·`update_snapshot`은 스냅샷의 미디어 ID를 `featured_media`로 보내고, 본문 삽입 HTML이 있으면 본문에 포함한다.

- [ ] **Step 1: 실패 테스트 작성**

```python
def test_published_post_has_featured_media_and_figure(tmp_path):
    automation, store, remote, receive = build_images(tmp_path)
    receive(...)
    tick(...)
    post = remote.posts[42]
    assert post['featured_media'] == 7
    assert 'AI 생성 이미지 · 설명용 삽화' in post['content']['raw']
    assert post['content']['raw'].index('<figure>') < post['content']['raw'].index('<h2>')

def test_image_failures_do_not_block_publish(tmp_path):
    remote.image_fail = 'rejected'
    automation, ...
    tick(...)
    assert automation.public(due)['requests'][0]['state'] == 'scheduled'
    assert remote.posts[42]['featured_media'] is None
    assert '이미지 없이 발행' in ...  # notice

def test_update_keeps_existing_media(tmp_path):
    publish(...)
    update(...)
    assert len([p for p in remote.paths if p.endswith('/media')]) == 1
    assert remote.posts[42]['featured_media'] == 7

def test_legacy_snapshot_without_media_still_verifies(tmp_path):
    ...  # snapshot에 미디어 키가 없어도 verify_snapshot 통과
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_automation_images.py -q`
Expected: `featured_media` 없음

- [ ] **Step 3: 최소 구현**

`_prepare_images`는 대표 이미지 추출·다운로드·저장·업로드와 본문 이미지 생성·저장·업로드를 각각 독립적으로 시도한다. 결과 ID를 스냅샷에 넣고 `render_article`에 `body_image` HTML을 넘긴다. `write_policy`는 figure를 escape된 caption과 함께 만든다. 발행 알림에 포함 여부를 넣는다. `verify_snapshot`은 `featured_media`가 응답에 있을 때만 비교한다.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_automation_images.py tests/test_automation.py tests/test_automation_publish.py tests/test_automation_update.py -q`
Expected: PASS

- [ ] **Step 5: diff 검토와 커밋**

```bash
git add studio/automation.py studio/writing_policy.py studio/automation_publish.py tests/test_automation_images.py
git commit -m "feat: 자동 글에 대표 이미지와 AI 본문 이미지 연결"
```

---

### Task 5: 화면·알림·문서와 전체 검증

**Files:**
- Modify: `studio/automation_routes.py`, `studio/static/automation.js`, `studio/static/automation.css`, `studio/static/index.html`, `README.md`, `tests/automation-dom.cjs`
- Test: `tests/automation-dom.cjs`, `tests/test_automation_http.py`

**Interfaces:**
- 상세 응답에 `images: {featured: record|None, body: record|None}`와 `media_usage: {files, bytes}` 추가.
- `GET /api/automation/requests/{id}/media/{kind}`: 저장된 로컬 파일만 반환. 없으면 404.
- DOM: `#automation-images`, `[data-image-kind]`, `#automation-media-usage`.

- [ ] **Step 1: 실패 테스트 작성**

```javascript
test('detail shows featured and body image states with local thumbnails', async () => {
  // images.featured = uploaded, images.body = unavailable
  // 썸네일 src가 /api/automation/requests/req_one/media/featured 인지 확인
  // '이미지 없이 발행' 사유 표시
});
test('media endpoint rejects traversal and missing files', ...);
```

Python: 미디어 경로가 요청의 저장 기록에 있는 파일만 반환하고, `..`·절대 경로·다른 요청 파일은 400/404인지 확인한다.

- [ ] **Step 2: 실패 확인**

Run: `node --test tests/automation-dom.cjs`; `.venv/bin/python -m pytest tests/test_automation_http.py -q`
Expected: 이미지 영역 없음

- [ ] **Step 3: 최소 구현**

상세에 두 이미지의 상태·출처·사유와 로컬 썸네일을 표시한다. `media_usage`는 `media/` 파일 수와 바이트 합계다. 미디어 경로는 요청의 `automation_media.local_path`만 허용하고 `Path.resolve()`가 `data/media` 안인지 확인한다. README·시작하기에 이미지 설정, AI 삽화 표시, 실패 시 이미지 없이 발행, 저작권·개인정보 책임 경계를 적는다.

- [ ] **Step 4: 최종 검증**

```sh
.venv/bin/python -m pytest -q --tb=short
node --test tests/v02-actions.cjs tests/v02-dom.cjs tests/v03-metrics.cjs tests/ui-copy.cjs tests/affiliate-dom.cjs tests/automation-dom.cjs
CHROMIUM_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' node tests/e2e_automation.mjs
CHROMIUM_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' npm run test:e2e
.venv/bin/python scripts/package.py
git diff --check
```

- [ ] **Step 5: diff 검토와 커밋**

```bash
git add studio/automation_routes.py studio/static/automation.js studio/static/automation.css studio/static/index.html README.md tests/automation-dom.cjs tests/test_automation_http.py
git commit -m "feat: 자동 운영 화면에 이미지 상태와 사용량 표시"
```

---

## 계획 자체 검토

- [x] Spec coverage: 대표 추출·다운로드(1), AI 생성(2), 업로드·중복(3), 글 연결·실패(4), 화면·문서·검증(5).
- [x] Step scan: 각 작업에 파일·인터페이스·5단계, 명명된 테스트와 기대값이 있다.
- [x] Type consistency: `image_url`/`allowed_image_url`/`download_image`/`ImageClient.generate`/`prepare_media`/`_prepare_images`/`featured_media`/`body_media` 이름을 작업 간 동일하게 쓴다.
- [x] Review Focus 5개를 Task 1·4·3·2·3의 명명된 테스트에 연결했다.
- [x] Proportion: 설계 173행 대비 계획 약 300행, 대표·본문 이미지 두 장으로 범위를 제한했다.
