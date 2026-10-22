"""Server-only affiliate link issuance via the providers' official APIs."""
import hashlib
import hmac
import re
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from .core import safe_url


COUPANG_PATH = '/v2/providers/affiliate_open_api/apis/openapi/v1/deeplink'
LABELS = {'coupang': '쿠팡', 'toss': '토스'}
SETTINGS = {'coupang': ('COUPANG_ACCESS_KEY', 'COUPANG_SECRET_KEY'),
            'toss': ('TOSS_ACCESS_KEY', 'TOSS_SECRET_KEY', 'TOSS_PUBLISHER_ID')}


def issued_url(value, hosts):
    try:
        url = safe_url(value)
        if urlsplit(url).hostname not in hosts:
            raise ValueError()
        return url
    except ValueError:
        raise ValueError('발급 응답의 제휴 URL 형식 확인 불가') from None


class AffiliateLinks:
    def __init__(self, environ):
        self.credentials = {}
        self.status = {}
        self.toss_token = ''
        self.toss_expires = 0
        self.toss_lock = Lock()
        for provider, keys in SETTINGS.items():
            values = tuple(environ.get(key, '').strip() for key in keys)
            error = ''
            if not all(values):
                error = f'{LABELS[provider]} API 설정 필요: {", ".join(keys)}'
            elif any(not re.fullmatch(r'[!-~]{1,2048}', value) for value in values):
                error = f'{LABELS[provider]} API 환경변수 형식 확인 필요'
            elif provider == 'coupang' and not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', values[0]):
                error = '쿠팡 Access Key 형식 확인 필요'
            elif provider == 'toss':
                try:
                    values = (*values[:2], str(UUID(values[2])))
                except ValueError:
                    error = '토스 TOSS_PUBLISHER_ID에 회원 연동 ID(UUID) 설정 필요'
            self.status[provider] = dict(configured=not error, error=error)
            if not error:
                self.credentials[provider] = values

    def generate(self, provider, source_url):
        if provider not in ('coupang', 'toss'):
            raise ValueError('쿠팡 또는 토스 선택 필요')
        source_url = safe_url(source_url)
        url = urlsplit(source_url)
        host, pattern, example = (
            ('www.coupang.com', r'/vp/products/([1-9][0-9]{0,18})/?', 'https://www.coupang.com/vp/products/상품ID')
            if provider == 'coupang' else
            ('toss.shopping', r'/t/([1-9][0-9]{0,18})/?', 'https://toss.shopping/t/상품ID'))
        match = re.fullmatch(pattern, url.path)
        if url.hostname != host or not match or url.fragment:
            raise ValueError(f'{LABELS[provider]} 원본 상품 URL 필요: {example} (단축 링크는 원본 주소로 입력)')
        if not self.status[provider]['configured']:
            raise ValueError(self.status[provider]['error'])
        # Fixed API hosts only; the supplied product URL is never fetched locally.
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            link = self.coupang(client, source_url) if provider == 'coupang' else self.toss(client, int(match[1]))
        return dict(provider=provider, source_url=source_url, affiliate_url=link)

    def post(self, client, provider, url, **kwargs):
        label = LABELS[provider]
        try:
            response = client.post(url, **kwargs)
        except httpx.TimeoutException:
            raise ValueError(f'{label} API 응답 시간 초과: 잠시 후 발급 결과 확인 필요') from None
        except httpx.HTTPError:
            raise ValueError(f'{label} API 연결 실패: 네트워크 상태 확인 필요') from None
        if response.status_code == 401 and provider == 'toss':
            self.toss_token = ''  # The next explicit request obtains a fresh token.
        if response.status_code in (401, 403):
            raise ValueError(f'{label} API 인증·권한 오류: 사용 승인, 키와 권한 설정 확인 필요')
        if response.status_code == 429:
            raise ValueError(f'{label} API 호출 한도 초과: 잠시 후 다시 시도 필요')
        if not response.is_success:
            raise ValueError(f'{label} API 요청 실패 (HTTP {response.status_code}): 설정과 서비스 상태 확인 필요')
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError()
        except ValueError:
            raise ValueError(f'{label} API 응답 형식 확인 불가') from None
        return data

    def coupang(self, client, source_url):
        access, secret = self.credentials['coupang']
        date = datetime.now(timezone.utc).strftime('%y%m%dT%H%M%SZ')
        signature = hmac.new(secret.encode(), (date + 'POST' + COUPANG_PATH).encode(), hashlib.sha256).hexdigest()
        authorization = f'CEA algorithm=HmacSHA256, access-key={access}, signed-date={date}, signature={signature}'
        result = self.post(client, 'coupang', 'https://api-gateway.coupang.com' + COUPANG_PATH,
                           headers={'Authorization': authorization}, json={'coupangUrls': [source_url]})
        if result.get('rCode') != '0':
            raise ValueError('쿠팡 링크 발급 거절: API 승인·키 또는 상품 URL 확인 필요')
        items = result.get('data')
        if (not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict)
                or items[0].get('originalUrl') != source_url):
            raise ValueError('쿠팡 링크 발급 응답의 상품 URL 확인 불가')
        return issued_url(items[0].get('shortenUrl'), ('coupa.ng', 'link.coupang.com'))

    def toss(self, client, taca_id):
        access, secret, publisher = self.credentials['toss']
        # Serialize token refresh and issuance in this single-user process.
        with self.toss_lock:
            if not self.toss_token or monotonic() >= self.toss_expires:
                token = self.post(client, 'toss', 'https://oauth2.cert.toss.im/token', data={
                    'grant_type': 'client_credentials', 'client_id': access, 'client_secret': secret,
                    'scope': 'sharelink:read sharelink:write'})
                value, expires = token.get('access_token'), token.get('expires_in')
                if (not isinstance(value, str) or not re.fullmatch(r'[!-~]{1,16384}', value)
                        or not isinstance(token.get('token_type'), str) or token['token_type'].lower() != 'bearer'
                        or type(expires) is not int or not 0 < expires <= 315360000):
                    raise ValueError('토스 인증 토큰 발급 실패: 키와 토큰 응답 확인 필요')
                self.toss_token = value
                self.toss_expires = monotonic() + expires - min(60, expires / 10)
            result = self.post(client, 'toss', 'https://sharelink.toss.im/openapi/links',
                               headers={'Authorization': f'Bearer {self.toss_token}'},
                               json={'landingType': 'PRODUCT', 'tacaId': taca_id, 'publisherId': publisher})
            if result.get('resultType') != 'SUCCESS':
                error = result.get('error')
                code = error.get('errorCode') if isinstance(error, dict) else None
                if code == 'SHARELINK_OPENAPI_ACCESS_DENIED':
                    raise ValueError('토스 접근 거절: API 승인·인증정보, 호출 서버의 등록 IP와 회원 연동 ID 확인 필요')
                if code == 'SHARELINK_OPENAPI_QUOTA_EXCEEDED':
                    raise ValueError('토스 일 사용 한도 초과: 자정(KST) 이후 다시 시도 필요')
                raise ValueError('토스 링크 발급 거절: 발급 가능한 상품과 계정 권한 확인 필요')
            data = result.get('success')
            if (not isinstance(data, dict) or data.get('publisherId') != publisher
                    or type(data.get('tacaItemId')) is not int or data['tacaItemId'] <= 0):
                raise ValueError('토스 링크 발급 응답의 상품·회원 연동 ID 확인 불가')
            return issued_url(data.get('shortUrl'), ('toss.im', 'toss.shopping'))
