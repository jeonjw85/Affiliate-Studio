"""Opt-in WordPress REST adapter. Strict raw read-back; no redirects/retries."""
from urllib.parse import urlsplit
import httpx
from .core import safe_url, text, timestamp
from .exporting import render, DISCLOSURES


def normalize(value):
    if not isinstance(value, str): raise ValueError('원격 raw 본문/제목 권한 필요')
    # Only transport line endings and outside whitespace; never strip HTML/links.
    return value.replace('\r\n', '\n').strip()


class WordPress:
    def __init__(self, url, username, password, *, allow_local_test=False):
        if allow_local_test and urlsplit(url).hostname == '127.0.0.1' and urlsplit(url).scheme == 'http':
            pass
        else:
            safe_url(url)
        u = urlsplit(url)
        if u.query or u.fragment or u.username or u.password:
            raise ValueError('WordPress URL은 사이트 기본 주소 필요')
        self.url = url.rstrip('/')
        self.username = text(username, 200)
        self.password = text(password, 300)

    def request(self, method, suffix, payload=None, *, params=None):
        query = dict(params or {})
        if method == 'GET':
            query['context'] = 'edit'
        with httpx.Client(auth=(self.username, self.password), timeout=15,
                          follow_redirects=False, trust_env=False) as client:
            with client.stream(method, self.url + '/wp-json/wp/v2/posts' + suffix,
                               json=payload, params=query or None) as response:
                if response.status_code not in ((200,) if method == 'GET' else (200, 201)):
                    raise ValueError('WordPress 응답 확인 불가')
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 524288: raise ValueError('WordPress 응답 크기 초과')
                import json
                return json.loads(raw)

    def read(self, id):
        if type(id) is not int or id <= 0: raise ValueError('확인된 원격 글 ID 필요')
        data = self.request('GET', '/' + str(id))
        if not isinstance(data, dict) or type(data.get('id')) is not int or data['id'] != id:
            raise ValueError('원격 글 ID 불일치')
        return data

    def verify_snapshot(self, data, snapshot):
        """Compare a raw read-back with an automatic immutable snapshot."""
        if (normalize(data.get('title', {}).get('raw')) != normalize(snapshot['title'])
                or normalize(data.get('content', {}).get('raw')) != normalize(snapshot['html'])
                or DISCLOSURES[snapshot['provider']] not in data.get('content', {}).get('raw', '')):
            raise ValueError('원격 제목·본문·광고 표시가 자동 발행 스냅샷과 다름')
        return data

    def verify(self, data, draft, product):
        if (normalize(data.get('title', {}).get('raw')) != normalize(draft['title'])
                or normalize(data.get('content', {}).get('raw')) != normalize(render(draft, product)['html'])
                or DISCLOSURES[product['provider']] not in data['content']['raw']):
            raise ValueError('원격 제목·본문·광고 표시가 승인한 내용과 다름: WordPress에서 확인 필요')
        return data

    def create(self, draft, product):
        payload = dict(title=draft['title'], content=render(draft, product)['html'], status='draft',
                       comment_status='closed', ping_status='closed')
        data = self.request('POST', '', payload)
        id = data.get('id')
        if type(id) is not int or id <= 0 or data.get('status') != 'draft':
            raise ValueError('초안 상태 응답이 아님')
        verified = self.verify(self.read(id), draft, product)
        if verified.get('status') != 'draft': raise ValueError('원격 글이 초안 상태가 아님')
        return dict(remote_id=id, remote_url=self.url + f'/wp-admin/post.php?post={id}&action=edit', remote_status='draft')
