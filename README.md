# Affiliate Studio

제휴 콘텐츠 작성과 WordPress 예약 발행을 관리하는 개인용 PC·NAS 서비스

버전 0.4.1

Telegram으로 본인 제휴 링크를 보내면 글을 작성해 WordPress에 예약할 수 있음
상품을 직접 등록하고, 템플릿이나 AI 후보를 검토·승인한 뒤 전송하는 수동 경로도 유지

## 기능

- Telegram으로 본인 제휴 링크를 보내 글 작성, WordPress 예약, 결과 알림
- 상품 정보, 출처, 제휴 링크 수동 등록
- 쿠팡 파트너스·토스 쉐어링크 API 링크 발급 (선택, 키와 승인 필요)
- 템플릿 초안과 로컬 AI·외부 API 후보 작성, 비교, 적용
- 소제목, 강조, 목록, 표 편집
- 버전별 검토와 승인
- WordPress 비공개 초안 전송과 공개 예약
- 채널별 조회, 클릭, 주문, 수수료 수익, 비용 수동 기록

제휴 가입, 상품 목록 수집, 사이트 개설, 성과 수집은 자동화하지 않음
토스 등 각 제휴 서비스의 외부 게시 조건은 직접 확인 필요

수동 화면의 AI 후보는 자동 적용, 승인, 게시하지 않음
Telegram 자동 운영은 그 화면과 별개로, 설정한 작성 규칙 검사 후 WordPress에 예약함
자동 운영을 켜려면 쿠팡 또는 토스 하루 편수 중 하나 이상이 필요함
WordPress 공개 예약은 실제 외부 게시 예약이며, 로컬 ZIP 생성 예약과 별개

## 실행

Python 3.11 이상 필요

처음에는 `.env.example`을 `.env`로 복사하고 필요한 연결 정보 입력
이미 `.env`가 있다면 해당 파일을 수정

```sh
cp .env.example .env
```

프로젝트 폴더의 `.env`는 실행 시 자동 로드하며, 터미널에서 지정한 환경변수가 우선
공백·`#`·`$`가 있는 값은 작은따옴표로 감싸기, 예: `WP_APP_PASSWORD='abcd efgh ijkl'`
파일의 값은 셸 명령으로 실행하지 않으며 직접 실행 시 `${변수}` 치환도 하지 않음
설정 변경 후 앱 재시작 필요

```sh
sh start.sh
```

접속 주소: `http://127.0.0.1:8787`

- 첫 실행 시 가상환경과 의존성 설치를 위한 인터넷 연결 필요
- AI와 WordPress 미연결 상태에서도 상품 등록, 템플릿, 검토, ZIP 사용 가능
- Telegram·AI·이미지 생성은 각각 별도 설정이며, 이미지 설정을 비우면 대표 이미지만 사용
- 직접 실행은 기본적으로 로컬 접속, NAS 운영은 아래 Docker 설정 사용
- 로그인 기능이 없는 개인용 서비스로, 신뢰하는 개인 네트워크에서 운영
- Mac Safari와 사용자 WordPress 사이트는 별도 검증 필요

## Docker로 NAS·개인 서버 운영

Docker Engine과 Docker Compose 필요, Python·Node를 서버에 별도로 설치할 필요 없음

1. 프로젝트 폴더에서 `.env.example`을 `.env`로 복사
2. `.env`의 `STUDIO_ALLOWED_HOSTS`에 브라우저에서 사용할 실제 NAS IP 또는 호스트명 입력
3. 필요한 AI·WordPress 설정 후 실행

```dotenv
STUDIO_ALLOWED_HOSTS=192.168.1.20,nas.local
STUDIO_PORT=8787
```

`192.168.1.20`은 예시이므로 본인 NAS 주소로 변경, URL·포트·와일드카드는 입력하지 않음

```sh
docker compose up -d --build
```

접속: `http://192.168.1.20:8787` 또는 `http://nas.local:8787`
컨테이너는 내부에서 `0.0.0.0:8787`로 수신하며, 지정한 호스트의 요청만 허용
포트는 `STUDIO_PORT`로 변경 가능, NAS 방화벽에서도 해당 포트 접근 허용 필요
기존 Origin·CSRF 검증은 NAS에서도 적용

### 설정 반영·상태 확인·종료

```sh
# .env 변경 반영 (환경변수 변경은 restart 대신 up 사용)
docker compose up -d
# 상태와 실행 로그
docker compose ps
docker compose logs --tail=100 studio
# 종료, 데이터 볼륨 유지
docker compose down
```

Docker 서비스가 켜지면 `unless-stopped` 정책으로 자동 재시작
수동으로 중지한 컨테이너는 `docker compose up -d`로 다시 실행

### 데이터 보존

SQLite와 ZIP은 `studio_data` 볼륨의 `/app/data`에 저장
하나의 데이터 볼륨에는 앱 컨테이너 한 개만 연결해 운영
컨테이너 재생성·이미지 업데이트·일반 `down` 후에도 유지되며, 호스트의 기존 `data/`와는 별도 저장소
`docker compose down -v`는 데이터 볼륨도 삭제하므로 데이터 유지 시 사용하지 않음
프로젝트 폴더 이름이나 Compose 프로젝트 이름을 바꾸면 다른 볼륨을 사용할 수 있음

백업은 앱을 잠시 중지한 뒤 복사:

```sh
docker compose stop studio
docker compose cp studio:/app/data ./data-backup
docker compose start studio
```

`.env`는 이미지·배포 ZIP·Git에 포함되지 않으므로 별도 보관

### Docker에서 로컬 AI 함께 실행

`.env`에 아래 값 입력:

```dotenv
AI_MODE=local
AI_BASE_URL=http://ollama:11434/v1
AI_MODEL=qwen3:4b-instruct-2507-q4_K_M
AI_LOCAL_HOSTS=ollama,host.docker.internal
```

```sh
docker compose --profile local-ai up -d --build
docker compose exec ollama ollama pull qwen3:4b-instruct-2507-q4_K_M
```

모델은 `ollama_data` 볼륨에 저장, Ollama 포트는 Docker 내부에서만 사용
기본 구성은 CPU 실행이며 NAS의 메모리와 CPU에 따라 생성 속도가 달라짐
외부 API 모드는 Ollama 컨테이너나 모델 다운로드 없이 사용 가능

이미 실행 중인 모델 서버에 연결하려면 `AI_BASE_URL`을 해당 서버 주소로 지정하고
호스트명을 `AI_LOCAL_HOSTS`에 추가 (예: `192.168.1.30`)
호스트의 Ollama는 `http://host.docker.internal:11434/v1` 사용 가능하나, 모델 서버도 Docker에서 접근 가능한 주소로 수신해야 함

## 제휴 링크 자동 발급

상품 화면에서 서비스 선택 → 원본 상품 URL 입력 → `제휴 링크 생성` → 링크 확인 후 상품 저장
저장한 링크는 콘텐츠 미리보기와 내보내기, WordPress 본문에 반영
API 설정 전에도 기존 제휴 링크를 직접 입력해 사용 가능

### API 설정

앱을 실행할 터미널에서 설정한 뒤 앱 재시작
아래 `read` 명령은 Bash/Zsh 기준이며, 각 명령 실행 후 해당 값을 붙여넣고 Enter 입력 (화면에 표시되지 않음)

**쿠팡 파트너스** — API 사용 권한과 Access Key / Secret Key 필요

```sh
read -r -s COUPANG_ACCESS_KEY
read -r -s COUPANG_SECRET_KEY
export COUPANG_ACCESS_KEY COUPANG_SECRET_KEY
```

**토스 쉐어링크** — 쉐어링크 Open API 승인 후 크리에이터 어드민의 `연동 → API 키 발급`에서 확인
셀러용 키와 다른 쉐어링크용 Access Key / Secret Key 및 **회원 연동 ID**(`publisherId`, UUID) 필요

```sh
read -r -s TOSS_ACCESS_KEY
read -r -s TOSS_SECRET_KEY
read -r -s TOSS_PUBLISHER_ID
export TOSS_ACCESS_KEY TOSS_SECRET_KEY TOSS_PUBLISHER_ID
sh start.sh
```

쿠팡만 설정했다면 쿠팡 환경변수 설정 후 `sh start.sh`로 실행
인증정보는 서버 환경변수로만 사용하며 브라우저나 상품 DB에 저장하지 않음
토스 토큰은 앱 프로세스 메모리에서 만료 전까지 재사용, 재시작 후 첫 발급에서 다시 요청
화면의 `API 설정됨`은 인증 성공을 뜻하지 않으며 실제 발급 결과로 확인

토스에는 **앱 서버의 고정 출발지 공인 IP**를 등록해야 함
PC에서 앱을 실행한다면 그 PC의 외부 통신에 사용되는 공인 IP 기준이며 `127.0.0.1`이나 내부 IP가 아님
2026-10-19 확인한 공식 안내 기준으로 토스 신규 API 신청은 일시 중단 상태, 기존 승인 계정은 이용 가능

### 지원하는 원본 URL

| 서비스 | 자동 발급 입력 | 처리 |
| --- | --- | --- |
| 쿠팡 | `https://www.coupang.com/vp/products/상품ID` | `itemId`, `vendorItemId` 등 쿼리를 유지해 딥링크 API에 전달 |
| 토스 | `https://toss.shopping/t/상품ID` | 경로의 상품 그룹 ID(`tacaId`)로 대표 옵션의 쉐어링크 발급 |

단축 공유 링크나 다른 주소 형식은 원본 상품 상세 주소로 입력
토스 URL 쿼리는 옵션 선택에 사용하지 않으며, 대표 옵션은 발급 시점에 따라 달라질 수 있음
발급된 링크는 상품에 저장해 재사용, 생성 버튼을 누를 때만 외부 API 호출
생성하거나 저장한 상품의 URL·서비스를 바꾸면 연결된 이전 링크와 확인 체크를 초기화, 새 링크 입력 후 다시 저장
인증·IP·한도·통신 오류는 화면에 표시하며 실패 시 입력 중인 제휴 URL 유지

검증은 공식 명세 기반 모의 HTTP 응답과 화면 동작 테스트 기준, 실제 계정 발급은 키와 승인 설정 후 별도 확인 필요
공식 문서: [쿠팡 파트너스 API](https://partners.coupang.com/#help/open-api) · [토스 연동 시작](https://sharelink-docs.toss.im/developers/open-api/auth) · [토스 링크 발급](https://sharelink-docs.toss.im/developers/open-api/api/link)

## AI 모드 선택

`.env`의 `AI_MODE`로 `local` 또는 `api` 선택, `AI_MODEL`이 비어 있으면 AI 없이 기본 기능 사용
두 모드 모두 콘텐츠 화면의 `AI 연결 확인` 후 표시된 대상에 저장본 전송을 동의하고 후보 생성

### 외부 OpenAI 호환 API

`.env` 설정 예시:

```dotenv
AI_MODE=api
AI_BASE_URL=https://api.openai.com/v1
AI_MODEL=gpt-4.1-mini
AI_API_KEY='본인-API-키'
```

다른 OpenAI 호환 서비스는 해당 서비스의 기본 URL·모델·키로 변경
HTTPS와 Bearer 인증, `/models`, `/chat/completions`, `response_format=json_schema` 지원 필요
`max_tokens`와 `temperature`를 지원하는 Chat Completions 모델 사용
API 키는 서버 요청의 인증 헤더에만 사용하며 브라우저·상품 DB·프롬프트로 전달하지 않음
자료는 설정한 외부 서비스로 전송되고 서비스 이용 요금이 적용
실제 API 접근 권한·모델 호환성·요금은 사용 서비스에서 확인

잘못된 모드나 연결 실패 시 다른 서버·모드로 자동 대체하지 않음
직접 실행은 앱 재시작, Docker는 `docker compose up -d`로 설정 반영

### 로컬 AI

외부 유료 AI API와 API 키 없이 컴퓨터에서 모델 실행
모델 다운로드, 메모리, 전력은 별도 필요

#### Ollama 설정

[Ollama](https://ollama.com/download) 설치 후 모델 다운로드

```sh
ollama pull qwen3:4b-instruct-2507-q4_K_M
```

Ollama를 직접 실행할 경우 아래 설정 사용
이미 실행 중이면 중복 실행하지 않고 기존 실행 환경을 수정한 뒤 재시작

```sh
OLLAMA_NO_CLOUD=1 OLLAMA_CONTEXT_LENGTH=4096 OLLAMA_NUM_PARALLEL=1 ollama serve
```

`OLLAMA_NO_CLOUD=1`은 이미 실행 중인 프로세스에 소급 적용되지 않음
[공식 FAQ](https://docs.ollama.com/faq)에 따라 설정 후 로그의 `Ollama cloud disabled: true` 확인

로컬 모드에서는 알려진 `:cloud`, `-cloud` 모델 태그를 거부
임의의 로컬 프록시가 외부로 자료를 전달하는지까지 검증하지는 않음

`.env`에 연결 설정

```dotenv
AI_MODE=local
AI_BASE_URL=http://127.0.0.1:11434/v1
AI_MODEL=qwen3:4b-instruct-2507-q4_K_M
AI_TIMEOUT_SECONDS=180
```

콘텐츠 화면의 `AI 연결 확인` 후 저장본 전송에 동의하고 후보 생성
모델 설정 변경 시 앱 재시작 필요

서버 요구사항
- 루프백 또는 `AI_LOCAL_HOSTS`에 지정한 서버의 OpenAI 호환 `/v1/models`, `/v1/chat/completions`
- JSON Schema 응답 형식 지원
- 외부 유료 API로 자동 대체하지 않음

### 실행 기록과 한도

0.2.0 당시 실제 실행 환경은 Linux, llama.cpp b11552, Qwen3 4B Instruct 2507 Q4_K_M
Ollama 설정은 공식 API와 모델 배포에 근거한 안내이며 Ollama 실행 자체와 Mac은 미검증
`llama-server` 사용 시 `AI_BASE_URL`을 해당 서버의 루프백 주소로 변경

- 모델 파일 약 2.5GB, 실행용 메모리 추가 필요
- CPU 작업 서버에서 앱을 통한 후보 생성 약 82초 기록
- 기기, 입력, 모델에 따라 소요 시간 차이 있음
- 기본 한도 UTC 기준 하루 10회, 실패한 요청도 포함
- `AI_DAILY_LIMIT`으로 하루 1~100회 설정 가능

### 전송 자료

상품명, 확인한 사실, 대상 독자, 주의사항, 현재 제목과 본문, 작성 유형을 선택한 로컬 모델 또는 외부 API로 전송
구조화된 제휴 URL, WordPress 비밀번호, 환경 변수는 전송 대상에서 제외
자유 입력란에 적은 내용은 전송될 수 있으므로 비밀번호 입력 금지

입력 자료의 지시문을 따르지 않도록 모델에 요청하지만 완전한 차단은 보장하지 않음
AI의 근거 번호는 사실 확인의 증거가 아니며 결과 검토 필요

## WordPress 연결

본인 WordPress에서 애플리케이션 비밀번호 발급 필요
연결 계정에 글 생성, 편집, 발행 권한과 `context=edit` raw 제목 및 본문 조회 권한 필요

검증 가능한 HTTPS 사이트 기본 주소만 허용하며 리다이렉트는 따르지 않음

`.env`에 설정

```dotenv
WP_URL=https://본인-사이트.example
WP_USERNAME=본인-사용자명
WP_APP_PASSWORD='발급받은 애플리케이션 비밀번호'
```

비밀번호를 채팅, Git, 스크린샷에 남기지 않도록 주의
설정 변경 후 앱 재시작 또는 `docker compose up -d` 필요
화면의 `설정됨`은 연결 성공을 뜻하지 않으며 실제 초안 전송 결과 확인 필요

## Telegram 자동 운영

Telegram으로 본인 제휴 링크를 보내면 상품 정보를 확인하고, 필요한 설명을 답장으로 받아
글 작성·검사·WordPress 예약 발행까지 처리하는 자동 운영 기능

### 준비

1. Telegram에서 `@BotFather`로 개인 봇을 만들고 봇 토큰 확인
2. 만든 봇과 개인 대화를 시작한 뒤 `/start` 전송
3. `.env`에 봇 토큰과 내 개인 채팅 ID, 사용자 ID 입력

```dotenv
TELEGRAM_BOT_TOKEN=123456789:발급받은-봇-토큰
TELEGRAM_CHAT_ID=내-개인-채팅-ID
TELEGRAM_ALLOWED_USER_ID=내-사용자-ID
```

등록한 사용자와 개인 대화만 처리하며 다른 사람의 링크는 작업을 만들지 않음
봇은 외부 수신 주소 없이 앱이 직접 새 메시지를 가져오는 방식을 사용
같은 봇 토큰에 webhook이 설정돼 있으면 연결 오류로 표시
설정 변경 후 앱 재시작 또는 `docker compose up -d` 필요

### 사용

- 개인 봇 대화에 본인 계정으로 발급한 쿠팡·토스 제휴 링크 전송
- 한 메시지에 링크 2~3개를 보내면 상품 비교 글 하나로 작성, 4개 이상은 나눠 전송
- 일부 상품 정보를 읽지 못해도 확보된 상품으로 먼저 발행, 막힌 상품만 따로 질문
- 상품 정보를 읽을 수 없거나 부족하면 봇이 상품명·특징 설명을 요청
- 봇 질문에 **답장**으로 `상품명:`, `특징:`, `가격:`, `대상:` 형식 입력
- `/start`는 사용 방법, `/status`는 대기·정보 요청·예약·발행 현황 안내

쿠팡·토스 링크 발급 API 없이 동작하며, 게시글에는 보낸 제휴 링크를 그대로 사용
비교표는 두 상품 이상에서 같은 이름으로 확인된 항목만 넣고, 없으면 상품별 소개로 작성
늦게 도착한 설명은 같은 글을 보완하며 새 글을 만들지 않음 (편수 추가 차감 없음)
WordPress에서 본문을 직접 수정한 글은 자동으로 덮어쓰지 않고 수정 차단으로 표시
수정 차단된 글은 자동 운영 화면의 '수정 재시도'로 다시 보완
자동 운영 화면에서 쿠팡·토스의 하루 편수와 발행 시간 범위를 각각 설정
링크가 부족하면 받은 만큼만 처리하며, 남은 링크는 다음 발행일로 넘어감

접수, 정보 요청, 예약 확인, 실제 공개 확인, 실패·지연을 같은 봇 대화로 알림
예약 성공은 발행 완료가 아니며, WordPress에서 실제 공개를 확인한 뒤 완료로 표시
알림 전송 실패는 글을 다시 발행하지 않고 별도로 재시도
WordPress 연결이 끊긴 채 전송되면 결과 불명으로 남고 글을 다시 만들지 않음
실패한 요청은 같은 제휴 링크를 다시 보내 재접수 가능

자동 글은 상품 페이지 대표 이미지를 WordPress 대표 이미지로 한 장 넣고,
AI가 만든 설명용 삽화를 본문 상단에 한 장 넣음 (AI 생성 이미지 표시)

### 이미지 설정

```dotenv
IMAGE_MODE=api
IMAGE_BASE_URL=https://api.openai.com/v1
IMAGE_MODEL=gpt-image-1
IMAGE_API_KEY=
IMAGE_DAILY_LIMIT=3
IMAGE_TIMEOUT_SECONDS=120
```

`IMAGE_MODE`를 비우면 AI 삽화 없이 상품 대표 이미지만 사용
이미지 생성은 OpenAI 호환 `/images/generations`의 b64 결과만 받음
상품 페이지가 차단되거나 이미지가 허용 형식·크기가 아니면 **이미지 없이 발행**
글 보완 시 기존 이미지를 그대로 쓰며 새로 만들지 않음

이미지 저작권·사용 허용 여부와 개인정보 포함 여부는 앱이 보증하지 않음
게시 전에 이미지 사용 권한과 내용을 직접 확인 필요
자동 글은 단일 상품 최대 2,000자, 비교·묶음 글 최대 3,000자 (짧은 글 700자)
로컬 AI는 `OLLAMA_CONTEXT_LENGTH=16384` 권장
자동 작성은 기존 수동 AI와 같은 `AI_DAILY_LIMIT`를 함께 사용하며, 한도가 없으면 링크는 대기 상태로 남음
Docker는 `.env`의 `OLLAMA_CONTEXT_LENGTH` 값으로 조정, 메모리 여유 필요
실제 계정 발행 전에는 테스트 WordPress에서 먼저 확인 필요

## 작성과 발행

1. 상품명, 확인한 사실, 출처, 본인 제휴 URL, 대상 독자, 주의사항 입력
2. `초안 만들기`로 템플릿 생성, 필요하면 선택한 AI로 후보 작성
3. 저장본과 후보를 비교하고 원하는 후보만 적용
4. 사실, 표현, 광고 고지, 제휴 조건 검토 후 `현재 버전 승인`
5. `WordPress 초안으로 전송` 후 원격 제목, 본문, 고지, `draft` 상태 확인
6. 사이트, 승인 버전, 현지 시각, UTC 확인 후 별도로 공개 예약에 동의
7. 예약 화면의 `원격 상태 조회`로 결과 확인

가격을 모르면 빈칸 유지
후보 적용이나 본문 수정 시 버전 증가, 기존 승인 해제, 대기 중인 로컬 예약 취소
일부 근거 없는 수치는 차단하지만 전체 사실 검증이나 법률 검토를 대신하지 않음

초안 전송은 비공개 상태
공개 예약은 같은 원격 초안을 `future` 상태로 변경한 뒤 재조회하는 별도 작업
예약 시각이 지났다는 이유만으로 게시 완료 처리하지 않음

### 예약 범위

- 현재부터 5분 이후~30일 이내
- 가격 포함 시 정보 확인 시각부터 24시간 이내
- 예약 이후 가격과 재고를 감시하는 기능 없음

가격이 오래됐다면 가격을 빼거나 정보를 다시 확인한 뒤 재승인 필요

### 취소와 수정

예약 화면의 `WordPress에서 예약 관리`로 관리자 화면을 열어 직접 예약 해제
WordPress에서 글을 초안으로 변경한 뒤 앱의 `원격 상태 조회`로 확인하면 편집 잠금 해제

앱은 원격 글을 `draft`로 되돌리는 취소 요청을 보내지 않음
WordPress 기본 REST API로는 상태 조회와 변경을 원자적으로 처리할 수 없어, 조회 직후 발행된 글을 잘못 비공개로 바꿀 위험이 있음
기존 `/cancel` API도 직접 취소 요청 거부

- 공개 예약, 전송 중, 결과 불명 상태에서는 관련 글과 상품 편집 잠금
- 결과 불명 시 자동 재전송하지 않고 WordPress 관리자에서 확인 후 상태 조회
- 종료된 소유 프로세스를 확인할 수 있는 전송 기록은 재시작 시 결과 불명으로 복구
- 소유 프로세스 정보가 없는 이전 실험 버전 기록은 자동 회수하지 않음
- 자동 게시나 자동 재시도 없음

취소한 동일 승인 버전의 재예약은 미지원
수정 저장, 재검토와 승인, 새 원격 초안 전송 후 새 예약 필요
기존 원격 초안은 자동 삭제하지 않음

예약 설정 중 같은 원격 글을 다른 화면, 계정, 플러그인에서 동시에 편집하거나 발행하지 않도록 주의
예약 설정도 원격 조회와 변경 사이의 완전한 원자성을 보장하지 않음
서버가 본문을 바꾸거나 재조회에 실패하면 성공으로 단정하지 않고 결과 불명으로 유지

## 본문 서식

새 템플릿과 AI 후보의 형식은 `markdown-v1`
AI에는 내용에 맞는 소제목, 강조, 목록을 사용하고 실제 비교 근거가 있을 때만 표를 만들도록 요청
AI가 형식과 사실성을 항상 지키는 것은 아니므로 적용 전 검토 필요

| 도구 | 문법 |
| --- | --- |
| 소제목 | `## 제목` |
| 작은 소제목 | `### 제목` |
| 굵게 | `**강조**` |
| 목록 | `- 항목` |
| 순서 | `1. 항목` |
| 주의 | `> 내용` |

편집할 글자를 선택한 뒤 도구 사용 또는 문법 직접 입력
근거표는 등록한 사실만 사용하며 다른 상품이나 수치를 만들어 넣지 않음

표 예시, 실제 상품 자료 아님

```text
| 항목 | 확인한 내용 |
| --- | --- |
| 구조 | 접이식 |
| 조절 | 각도 조절 가능 |
```

- 표는 바깥쪽 `|`, 구분선, 동일한 열 수 필요
- 표 범위 2~8열, 본문 1~40행
- 문법 각 줄 앞에 공백 없이 입력
- `\|`, `\*`로 기호를 그대로 표시 가능
- 잘못된 표는 값이나 행을 버리지 않고 원문 표시
- 표 정렬 구분선 `:---`, 중첩 목록, 이미지, 직접 HTML, 본문 링크, 임의 CSS는 미지원
- 출처와 제휴 링크는 앱이 별도로 추가

저장본 미리보기, HTML 파일, WordPress 본문에 같은 렌더러 사용
TXT는 서식 기호를 제거하고 표 셀을 탭으로 구분하며 광고 고지, 출처, 링크 유지

앱과 다운로드 HTML은 기본 스타일 사용
WordPress의 글꼴, 간격, 색상, 표 테두리는 사이트 테마와 플러그인에 따라 달라질 수 있음
워드프로세서식 편집기는 아님

이전 글과 후보는 일반 텍스트 유지
기존 승인본이나 예약본을 자동으로 재해석하지 않음
서식으로 전환하려면 형식 변경 후 저장, 검토, 재승인 필요
전송 결과 불명이나 원격 예약 잠금은 우회하지 않음

서식 변경도 새 버전으로 처리하며 승인 해제와 대기 로컬 예약 취소 적용
서식으로 감싼 수치나 단정 표현은 표시되는 본문을 기준으로 추가 검사하되 전체 사실 검증은 보장하지 않음

## 성과 기록

WordPress, 네이버, 티스토리 등 채널별 성과를 수동으로 기록하는 기능
외부 사이트에 접속하거나 수치를 자동 수집하지 않음

기간, 보고서 출처, 시간대, 조회, 클릭, 주문, 수수료 수익, 배분 비용 저장과 수정, 삭제, CSV 내보내기 지원
승인본문 TXT는 광고 고지, 출처, 본인 제휴 링크를 유지하며 다른 채널에 자동 게시하지 않음
각 매체의 제휴 활동 허용 조건과 광고 표시 기준 확인 필요

### 입력 기준

- 원본 보고서와 같은 집계 기간, 시간대, 주문 귀속 기준 사용
- 같은 글을 여러 채널에 올렸다면 채널과 게시 URL별로 기록
- 기록 당시 콘텐츠 제목과 버전 보관
- 모르는 값은 빈칸, 확인한 결과가 0인 경우만 0 입력
- 금액은 KRW, 소수 두 자리까지 허용
- 수수료 수익과 상품 판매액 구분 필요

클릭/조회, 주문/클릭은 입력값의 산술 비율
재클릭, 지연 주문, 집계 기준 차이로 100%를 넘으면 경고 표시
분모가 0이거나 필요한 값이 없으면 `계산 불가`
클릭당 수익은 수수료를 클릭 수로 나눈 값

수익에서 입력 비용을 뺀 값은 최종 순이익이 아님
세금, 환불, 취소, 누락 비용 미반영
공통 호스팅 비용 등을 여러 기록에 중복 배분하지 않도록 주의

### 중복과 내보내기

- 같은 콘텐츠, 채널, URL, 기간의 중복 기록 거부
- 기간 겹침은 경고만 표시하고 합산하지 않음
- 서로 다른 조건의 기록으로 플랫폼 순위나 수익 가능성을 자동 판정하지 않음
- 수정과 삭제 시 기록 버전 확인, 충돌 시 입력 보존
- 충돌 후 목록을 새로고침하고 기존 기록을 다시 선택해 검토 필요
- 성과 기록 삭제 시 콘텐츠와 외부 게시글은 유지

`전체 CSV`는 화면 필터와 무관하게 모든 수동 기록 포함
엑셀 수식 실행 위험이 있는 문자 값 앞에는 `'` 추가 가능
CSV 자동 가져오기와 복원은 미지원
제목, 메모, URL이 포함되므로 공개 공유 전 확인 필요

성과 기록은 실험 결과 비교용이며 수익 발생 인증이나 플랫폼 추천 기능이 아님

## 앱 종료 후 동작

| 작업 | 실행 조건 |
| --- | --- |
| AI 후보 작성 | 앱 실행과 선택한 모델 서버/API 연결 필요 |
| 로컬 ZIP 생성 예약 | 앱 실행 필요 |
| WordPress에서 확인된 공개 예약 | 앱 종료 후에도 WordPress에 유지 |

WordPress 정시 발행은 WP-Cron과 호스팅 상태에 따름
예약 시각, 서버 시간대, 호스팅 cron 설정 확인 필요
Affiliate Studio는 WordPress cron을 설치하거나 운영하지 않음

## 데이터 보관과 업데이트

1. 기존 앱 종료
2. `data/` 폴더 전체 백업
3. 새 압축을 별도 폴더에 해제
4. 이전 `data/`를 새 폴더로 복사한 뒤 실행

또는 `STUDIO_DATA_DIR`에 기존 데이터 폴더의 절대 경로 지정
같은 DB를 구버전과 신버전에서 동시에 열지 않도록 주의

기존 SQLite records 테이블을 유지하며 후보, 전송, 공개 예약, 수동 성과 기록을 별도 종류로 저장
배포 ZIP에는 사용자 DB, 비밀번호, 모델 파일, 가상환경 미포함
데이터와 환경 설정은 별도 백업 필요

## 개발 검증

기존 릴리스 검증 환경: Linux, Python 3.11, Chromium

```sh
.venv/bin/python -m pytest -q
npm ci
node --test tests/v02-actions.cjs tests/v02-dom.cjs tests/v03-metrics.cjs tests/ui-copy.cjs tests/affiliate-dom.cjs tests/automation-dom.cjs
# Playwright 브라우저 설치 또는 CHROMIUM_PATH 지정 후 실행
npm run test:e2e
CHROMIUM_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' node tests/e2e_automation.mjs
node tests/e2e_v03.mjs
node tests/e2e_v04.mjs
node tests/e2e_copy.mjs
# Docker가 실행 중일 때: 임시 프로젝트·볼륨과 합성 AI 서버로 검증
.venv/bin/python tests/docker_smoke.py
```

Docker 검증은 이미지 빌드, NAS 호스트 제한, 내부 AI 통신, ZIP 다운로드,
컨테이너 재생성 후 데이터 유지와 중단 작업 복구를 확인한 뒤 시험 리소스 정리
외부 API는 모의 HTTP 응답으로 인증 헤더·생성·오류 처리를 검증하며, 실제 유료 호출은 별도 연결 설정 후 확인

선택적 실모델 연동 검증: `tests/live_v02_probe.py`
브라우저 v0.2 연동 검증: `tests/serve_v02_fixture.py`, `tests/e2e_v02.mjs`

두 경로 모두 일회용 `127.0.0.1` WordPress fixture 전용, 실제 사용자 사이트 실행 금지
브라우저 v0.2 시험은 AI HTTP fixture 사용
실제 모델 생성은 별도 live probe로 검증

## 라이선스

GNU General Public License v3.0 (GPL-3.0)

Copyright (C) 2026 Jeongwan Jeon

이 프로그램은 자유 소프트웨어입니다. 자유 소프트웨어 재단이 발표한 GNU 일반 공중 사용 허가서 버전 3 또는 이후 버전의 조건에 따라 재배포하거나 수정할 수 있습니다.

자세한 내용은 [LICENSE](LICENSE) 파일을 참고하세요.
