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
        with self.connect() as c:
            return {k: self.rows(c, k[:-1]) for k in ('products',)}










    def assert_version(self, d, version):
        if type(version) is not int or d['version'] != version:
            raise VersionConflict('버전이 변경됐습니다. 새로고침 후 다시 검토하세요.')










    def update_product(self, id, data):
        valid = validate_product(data)
        with self.connect() as c:
            p = self.get(c, id, 'product'); self.assert_version(p, data.get('version'))
            if any(old['id'] != id and old['source_url'].split('#')[0] == valid['source_url'].split('#')[0] for old in self.rows(c, 'product')):
                raise ValueError('중복 상품 URL입니다.')
            p.update(valid, version=p['version']+1)
            self.put(c, 'product', p)
            return p


    def add_product(self, data):
        p = dict(validate_product(data), id=uuid.uuid4().hex, version=1, created_at=now().isoformat())
        with self.connect() as c:
            if any(old['source_url'].split('#')[0] == p['source_url'].split('#')[0] for old in self.rows(c, 'product')):
                raise ValueError('같은 출처 URL의 중복 상품입니다.')
            return self.put(c, 'product', p)
