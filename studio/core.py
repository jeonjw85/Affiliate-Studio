"""Local evidence and content store. No shopping-site requests."""
import json
import os
import sqlite3
import uuid
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit
import ipaddress
import re
from decimal import Decimal
from contextlib import contextmanager


# A deliberately limited, deterministic guard. It cannot verify meaning or
# qualitative claims. Normalize simple units/spaces and compare source quantities.
QUANTITY = re.compile(r'(\d+(?:[,.]\d+)*)\s*(mAh|GHz|MHz|kHz|mm|cm|kg|mg|GB|TB|MB|Hz|W|V|g|m|원|%|시간|개월|년|일|배)(?![A-Za-z])', re.I)


def quantities(value):
    result = {}
    for match in QUANTITY.finditer(value):
        try:
            key = (Decimal(match[1].replace(',', '')), match[2].lower())
            result[key] = match[0]
        except Exception:
            continue
    return result


def text(value, limit=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError('빈 값 또는 너무 긴 텍스트입니다.')
    return value.strip()


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise ValueError('시간대가 포함된 올바른 날짜가 필요합니다.') from None


def safe_url(value):
    value = text(value, 2048)
    try:
        u = urlsplit(value)
        if u.scheme != 'https' or not u.hostname or '.' not in u.hostname or u.username or u.password or u.port not in (None, 443) or any(c.isspace() for c in value) or '\\' in value:
            raise ValueError()
        if u.hostname.endswith(('.local', '.localhost')):
            raise ValueError()
        try:
            ipaddress.ip_address(u.hostname)
        except ValueError:
            pass
        else:
            raise ValueError()
    except ValueError:
        raise ValueError('로그인 정보 없는 HTTPS 공개 도메인 링크가 필요합니다.') from None
    return value


def validate_product(data):
    if not isinstance(data, dict) or data.get('provider') not in ('coupang', 'toss'):
        raise ValueError('쿠팡 또는 토스를 선택하세요.')
    if data.get('ownership_confirmed') is not True:
        raise ValueError('본인 제휴 링크와 허용된 게시 매체를 직접 확인하세요.')
    price = data.get('price')
    if price is not None and (type(price) is not int or not 0 < price <= 100000000):
        raise ValueError('가격은 양의 정수 원 단위로 입력하거나 비워두세요.')
    observed = timestamp(data.get('observed_at'))
    if (observed - now()).total_seconds() > 60:
        raise ValueError('미래 시각을 확인 시점으로 기록할 수 없습니다.')
    facts = data.get('facts')
    if not isinstance(facts, list) or not 1 <= len(facts) <= 20:
        raise ValueError('출처에서 확인한 사실을 1~20개 기록하세요.')
    return dict(provider=data['provider'], name=text(data.get('name'), 160), source_url=safe_url(data.get('source_url')), affiliate_url=safe_url(data.get('affiliate_url')), price=price, observed_at=observed.isoformat(), facts=[text(f, 500) for f in facts], audience=text(data.get('audience'), 500), caveats=text(data.get('caveats'), 1000), ownership_confirmed=True)


def now():
    return datetime.now(timezone.utc)


class VersionConflict(ValueError):
    pass


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.execute("CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL)")
            from .publication import owner_alive
            for kind, active, interrupted in (('operation', 'running', 'failed'), ('delivery', 'sending', 'uncertain'), ('publication', 'sending', 'uncertain')):
                for record in self.rows(c, kind):
                    if record['state'] == active and not owner_alive(record):
                        record.update(state=interrupted, error='이전 실행 프로세스가 종료됐습니다. 자동 재전송하지 않습니다. 원격 초안은 WordPress에서 확인하세요.')
                        self.put(c, kind, record)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=10)
        try:
            with c:
                c.execute("BEGIN IMMEDIATE")
                yield c
        finally:
            c.close()

    def put(self, c, kind, data):
        c.execute("INSERT OR REPLACE INTO records VALUES (?,?,?)", (data['id'], kind, json.dumps(data, ensure_ascii=False)))
        return data

    def get(self, c, id, kind):
        id = text(id, 64)
        row = c.execute("SELECT data FROM records WHERE id=? AND kind=?", (id, kind)).fetchone()
        if not row:
            raise ValueError("항목을 찾을 수 없습니다.")
        return json.loads(row[0])

    def rows(self, c, kind):
        return [json.loads(r[0]) for r in c.execute("SELECT data FROM records WHERE kind=? ORDER BY rowid DESC", (kind,))]

    def state(self):
        from .publication import public
        with self.connect() as c:
            return dict({k: self.rows(c, k[:-1]) for k in ('products', 'drafts', 'jobs')},
                        publications=[public(x) for x in self.rows(c, 'publication')])

    def publish_schedule(self, id, version, run_at, confirmed, client):
        from .publication import schedule
        return schedule(self, id, version, run_at, confirmed, client)

    def refresh_publication(self, id, client):
        from .publication import reconcile
        return reconcile(self, id, client)

    def cancel_publication(self, id, confirmed, client):
        from .publication import reconcile
        return reconcile(self, id, client, cancel=True, confirmed=confirmed)

    def wordpress_draft(self, id, version, confirmed, client):
        if confirmed is not True or client is None:
            raise ValueError('WordPress 연결 설정과 외부 초안 전송 확인이 필요합니다.')
        with self.connect() as c:
            d, p = self.approved_pair(c, id, version)
            self.assert_version(d, version)
            if any(x['draft_id'] == id and x['state'] in ('sending', 'future', 'uncertain') for x in self.rows(c, 'publication')):
                raise VersionConflict('공개 예약을 먼저 취소하거나 조회하세요.')
            old = next((x for x in self.rows(c, 'delivery') if x['draft_id'] == id and x['version'] == version and x['target'] == client.url), None)
            if old:
                return old
            result = dict(id=uuid.uuid4().hex, draft_id=id, version=version, target=client.url, state='sending', error='', created_at=now().isoformat(), _pid=os.getpid())
            self.put(c, 'delivery', result)
        try:
            result.update(client.create(d, p), state='completed')
        except Exception:
            # POST may have succeeded even if a response/readback was lost. No retry.
            result.update(state='uncertain', error='전송 결과를 확정할 수 없습니다. WordPress 관리자에서 확인하세요. 중복 방지를 위해 자동 재전송하지 않습니다.')
        with self.connect() as c:
            return self.put(c, 'delivery', result)

    def schedule(self, id, version, run_at):
        due = timestamp(run_at)
        if not 0 < (due - now()).total_seconds() <= 30*86400:
            raise ValueError('예약 시각은 현재 이후 30일 이내로 지정하세요.')
        with self.connect() as c:
            d, _ = self.approved_pair(c, id, version)
            self.assert_version(d, version)
            old = next((j for j in self.rows(c, 'job') if j['draft_id'] == id and j['version'] == version and j['state'] in ('pending', 'completed')), None)
            if old:
                return old
            j = dict(id=uuid.uuid4().hex, draft_id=id, version=version, run_at=due.isoformat(), state='pending', error='', artifact=None, created_at=now().isoformat())
            return self.put(c, 'job', j)

    def cancel_job(self, id):
        with self.connect() as c:
            j = self.get(c, id, 'job')
            if j['state'] != 'pending':
                raise ValueError('대기 중인 예약만 취소할 수 있습니다.')
            j.update(state='cancelled', error='사용자가 취소함')
            return self.put(c, 'job', j)

    def run_due(self, output, at=None):
        from .exporting import bundle
        output = Path(output); output.mkdir(parents=True, exist_ok=True)
        count = 0
        with self.connect() as c:
            for j in self.rows(c, 'job'):
                if j['state'] != 'pending' or timestamp(j['run_at']) > (at or now()):
                    continue
                try:
                    d, p = self.approved_pair(c, j['draft_id'], j['version'])
                    artifact = j['id'] + '.zip'
                    temp = output / (artifact + '.tmp')
                    temp.write_bytes(bundle(d, p))
                    temp.replace(output / artifact)
                    j.update(state='completed', artifact=artifact, completed_at=now().isoformat())
                except ValueError as e:
                    j.update(state='failed', error=str(e))
                except OSError:
                    j.update(state='failed', error='파일 저장 실패: 저장 공간과 폴더 권한을 확인하세요.')
                self.put(c, 'job', j)
                count += 1
        return count

    def approved_pair(self, c, id, version=None):
        d = self.get(c, id, 'draft')
        if version is not None:
            self.assert_version(d, version)
        p = self.get(c, d['product_id'], 'product')
        if d['status'] != 'approved' or not d['approved_at']:
            raise ValueError('현재 버전의 검토 승인이 필요합니다.')
        errors = self.checks(d, p)['errors']
        if errors:
            raise ValueError(' / '.join(errors))
        return d, p

    def package(self, id, version=None):
        from .exporting import bundle
        with self.connect() as c:
            return bundle(*self.approved_pair(c, id, version))

    def assert_version(self, d, version):
        if type(version) is not int or d['version'] != version:
            raise VersionConflict('버전이 변경됐습니다. 새로고침 후 다시 검토하세요.')

    def checks(self, d, p):
        errors = []
        if p['price'] is not None and (now() - timestamp(p['observed_at'])).total_seconds() > 86400:
            errors.append('가격 정보가 24시간 이상 지났습니다. 상품을 수정해 실제 정보를 재확인하거나 가격을 비우세요.')
        checked_body = d['body']
        if d.get('body_format') == 'markdown-v1':
            from .formatting import render_body
            checked_body += '\n' + render_body(d['body'])['text']
        combined = '\n'.join([d['title'], checked_body, p['name'], p['audience'], p['caveats'], *p['facts']])
        risky = [word for word in ('써보니', '사용해보니', '직접 사용', '무조건', '최저가', '100%', '수익 보장', '완치') if word in combined]
        if risky:
            errors.append('근거 확인이 필요한 후기/단정 표현을 제거하세요: ' + ', '.join(risky))
        supplied = '\n'.join([p['name'], *p['facts']])
        if p['price'] is not None:
            supplied += f"\n{p['price']}원"
        allowed = quantities(supplied)
        ungrounded = [label for key, label in quantities(d['title'] + '\n' + checked_body).items() if key not in allowed]
        if ungrounded:
            errors.append('등록된 근거에 없는 수치를 제거하거나 상품 근거에 출처를 확인해 기록하세요: ' + ', '.join(ungrounded))
        warnings = ['이 점검은 문구·일부 수치 휴리스틱이며 모든 허위 주장, 법률·약관 준수나 상품 정보의 진실성을 검증하지 않습니다.', '링크 소유권·수익 귀속·가격·이미지 사용 권리와 게시 매체 등록은 직접 확인하세요.']
        if p['provider'] == 'toss':
            warnings.append('토스 링크의 블로그 게시 허용 여부와 최신 대가성 표시 문구를 앱의 이용조건에서 확인하세요.')
        return dict(errors=errors, warnings=warnings)

    def detail(self, id):
        from .exporting import render
        with self.connect() as c:
            d = self.get(c, id, 'draft')
            p = self.get(c, d['product_id'], 'product')
            return dict(draft=d, product=p, preview=render(d, p), checks=self.checks(d, p),
                        candidates=[x for x in self.rows(c, 'ai_candidate') if x['draft_id'] == id],
                        publication_lock=self.locked(c, id))

    def locked(self, c, id):
        return any(x['draft_id'] == id and x['state'] in ('sending', 'future', 'uncertain')
                   for x in self.rows(c, 'publication')) or any(
                   x['draft_id'] == id and x['state'] == 'sending' for x in self.rows(c, 'delivery'))

    def assert_unlocked(self, c, id):
        if self.locked(c, id):
            raise VersionConflict('원격 전송 또는 공개 예약이 잠겨 있습니다. 먼저 취소하거나 상태를 확인하세요.')

    def generate_ai(self, id, version, style, confirmed, client):
        if confirmed is not True or client is None:
            raise ValueError('로컬 AI 연결 설정과 자료 전송 확인이 필요합니다.')
        with self.connect() as c:
            d = self.get(c, id, 'draft'); self.assert_version(d, version)
            self.assert_unlocked(c, id)
            p = self.get(c, d['product_id'], 'product')
            source = client.source(d, p, style)
            operations = [x for x in self.rows(c, 'operation') if x.get('type') == 'ai']
            if any(x['draft_id'] == id and x['state'] == 'running' for x in operations):
                raise VersionConflict('AI 생성이 이미 진행 중입니다.')
            if sum(x['created_at'][:10] == now().date().isoformat() for x in operations) >= client.daily_limit:
                raise ValueError('오늘의 AI 실행 한도를 사용했습니다. 실패한 시도도 포함됩니다.')
            op = dict(id=uuid.uuid4().hex, type='ai', draft_id=id, source_version=version,
                      product_version=p['version'], state='running', created_at=now().isoformat(), _pid=os.getpid())
            self.put(c, 'operation', op)
        try:
            generated = client.generate(source, len(p['facts']))
            with self.connect() as c:
                current = self.get(c, id, 'draft'); self.assert_version(current, version)
                self.assert_version(self.get(c, p['id'], 'product'), p['version'])
                self.assert_unlocked(c, id)
                candidate = dict(generated, id=uuid.uuid4().hex, draft_id=id, source_version=version,
                                 product_version=p['version'], model=client.model, created_at=now().isoformat(),
                                 warnings=['AI 근거 번호는 사실 검증이 아닙니다. 원문과 대조해 검토하세요.'])
                self.put(c, 'ai_candidate', candidate)
                op.update(state='completed', candidate_id=candidate['id'])
                self.put(c, 'operation', op)
                return candidate
        except Exception as error:
            with self.connect() as c:
                op.update(state='failed'); self.put(c, 'operation', op)
            if isinstance(error, ValueError): raise
            raise ValueError('AI 생성에 실패했습니다. 자동 재시도하지 않습니다.') from None

    def apply_candidate(self, id, candidate_id, version):
        with self.connect() as c:
            d = self.get(c, id, 'draft'); self.assert_version(d, version)
            self.assert_unlocked(c, id)
            candidate = self.get(c, candidate_id, 'ai_candidate')
            p = self.get(c, d['product_id'], 'product')
            if candidate['draft_id'] != id or candidate['source_version'] != version or candidate['product_version'] != p['version']:
                raise VersionConflict('후보의 원본 버전 또는 상품이 변경됐습니다. 다시 생성하세요.')
            mode = candidate.get('body_format', 'plain')
            if mode not in ('plain', 'markdown-v1'):
                raise ValueError('지원하지 않는 후보 서식입니다.')
            d.update(title=candidate['title'], body=candidate['body'], body_format=mode)
            return self.invalidate(c, d)

    def invalidate(self, c, d):
        d.update(version=d['version']+1, status='review', approved_at=None)
        for j in self.rows(c, 'job'):
            if j['draft_id'] == d['id'] and j['state'] == 'pending':
                j.update(state='cancelled', error='콘텐츠 또는 근거 수정으로 승인이 무효화됨')
                self.put(c, 'job', j)
        return self.put(c, 'draft', d)

    def edit_draft(self, id, version, title, body, body_format=None):
        with self.connect() as c:
            d = self.get(c, id, 'draft'); self.assert_version(d, version)
            self.assert_unlocked(c, id)
            mode = d.get('body_format', 'plain') if body_format is None else body_format
            if mode not in ('plain', 'markdown-v1'):
                raise ValueError('지원하지 않는 본문 서식입니다.')
            d.update(title=text(title, 200), body=text(body, 20000), body_format=mode)
            return self.invalidate(c, d)

    def approve(self, id, version, confirmed):
        with self.connect() as c:
            d = self.get(c, id, 'draft'); self.assert_version(d, version)
            p = self.get(c, d['product_id'], 'product')
            if confirmed is not True:
                raise ValueError('출처·내용·제휴 링크·광고 표시를 직접 검토했다는 확인이 필요합니다.')
            errors = self.checks(d, p)['errors']
            if errors:
                raise ValueError(' / '.join(errors))
            d.update(status='approved', approved_at=now().isoformat())
            return self.put(c, 'draft', d)

    def update_product(self, id, data):
        valid = validate_product(data)
        with self.connect() as c:
            p = self.get(c, id, 'product'); self.assert_version(p, data.get('version'))
            for draft in self.rows(c, 'draft'):
                if draft['product_id'] == id:
                    self.assert_unlocked(c, draft['id'])
            if any(old['id'] != id and old['source_url'].split('#')[0] == valid['source_url'].split('#')[0] for old in self.rows(c, 'product')):
                raise ValueError('중복 상품 URL입니다.')
            p.update(valid, version=p['version']+1)
            self.put(c, 'product', p)
            for d in self.rows(c, 'draft'):
                if d['product_id'] == id:
                    self.invalidate(c, d)
            return p

    def create_draft(self, product_id):
        with self.connect() as c:
            p = self.get(c, product_id, 'product')
            existing = next((d for d in self.rows(c, 'draft') if d['product_id'] == product_id), None)
            if existing:
                return existing
            body = f"{p['audience']}을 위한 구매 전 정보 정리입니다. 판매 페이지에서 확인한 내용과 구매 전에 살펴볼 조건을 구분했습니다.\n\n## 구매 전 확인할 점\n{p['caveats']}\n\n배송비, 옵션별 가격, 반품 조건은 결제 화면에서 별도로 확인하세요. 상품 정보와 가격은 확인 시점 이후 달라질 수 있습니다."
            d = dict(id=uuid.uuid4().hex, product_id=product_id, title=p['name'] + ' 구매 전 확인할 정보', body=body, body_format='markdown-v1', version=1, status='review', approved_at=None, created_at=now().isoformat())
            return self.put(c, 'draft', d)

    def add_product(self, data):
        p = dict(validate_product(data), id=uuid.uuid4().hex, version=1, created_at=now().isoformat())
        with self.connect() as c:
            if any(old['source_url'].split('#')[0] == p['source_url'].split('#')[0] for old in self.rows(c, 'product')):
                raise ValueError('같은 출처 URL의 중복 상품입니다.')
            return self.put(c, 'product', p)
