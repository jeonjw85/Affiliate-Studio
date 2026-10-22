"""Standalone browser-test server: real app, synthetic external partners.

Usage: .venv/bin/python tests/serve_automation_fixture.py PORT
Never used by production; no outbound network calls are made.
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

from pathlib import Path
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio import app as app_module

AT = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
STATE = {'updates': [], 'sent': [], 'posts': {}, 'next_bot_message': 500,
         'next_post': 42, 'ai': [], 'tick_error': ''}


class FakeTelegram:
    chat_id = 1234
    user_id = 5678
    bot_id = 900

    def connect(self):
        return self.bot_id

    def updates(self, offset):
        return [item for item in STATE['updates'] if item['update_id'] >= offset]

    def send(self, text, *, reply_to_message_id=None, force_reply=False):
        STATE['next_bot_message'] += 1
        STATE['sent'].append({'text': text, 'id': STATE['next_bot_message']})
        return STATE['next_bot_message']


class FakeAI:
    mode = 'local'
    model = 'fixture-model'
    daily_limit = 10
    url = 'https://ai.example.test/v1'

    def request(self, method, path, payload=None):
        STATE['ai'].append(payload)
        facts = json.loads(payload['messages'][1]['content'])['facts']
        answer = {'title': '합성 상품 안내',
                  'body': '## 확인한 내용\n확인한 사실 1과 확인한 사실 2를 살펴보세요.',
                  'evidence_refs': [1] if len(facts) == 1 else [1, 2]}
        return {'choices': [{'message': {'content': json.dumps(answer)}, 'finish_reason': 'stop'}]}


class FakeWordPress:
    url = 'https://wp.example.test'

    def request(self, method, suffix, payload=None, *, params=None):
        if method == 'GET' and suffix == '':
            return []
        if method == 'POST' and suffix == '':
            post_id = STATE['next_post']
            STATE['next_post'] += 1
            STATE['posts'][post_id] = {
                'id': post_id, 'status': 'draft', 'slug': payload['slug'],
                'title': {'raw': payload['title']},
                'content': {'raw': payload['content']}, 'date_gmt': ''}
            return {'id': post_id, 'status': 'draft'}
        post_id = int(suffix.strip('/'))
        post = STATE['posts'][post_id]
        if method == 'POST':
            post.update(status=payload.get('status', post['status']),
                        date_gmt=payload.get('date_gmt', post['date_gmt']))
            return {'id': post_id, 'status': post['status']}
        return dict(post)

    def read(self, post_id):
        return dict(STATE['posts'][post_id])

    def verify_snapshot(self, data, snapshot):
        if data['title']['raw'] != snapshot['title'] or data['content']['raw'] != snapshot['html']:
            raise ValueError('원격 본문 불일치')
        return data


def create(data=None):
    data = data or tempfile.mkdtemp(prefix='automation-fixture-')
    os.environ.update(
        TELEGRAM_BOT_TOKEN='123456789:fixture_bot_token_value_abc',
        TELEGRAM_CHAT_ID='1234', TELEGRAM_ALLOWED_USER_ID='5678',
        WP_URL='https://wp.example.test', WP_USERNAME='fixture',
        WP_APP_PASSWORD='fixture-password', AI_MODE='api',
        AI_MODEL='fixture-model', AI_API_KEY='fixture-ai-key',
        AI_BASE_URL='https://ai.example.test/v1')
    app_module.Telegram.from_env = classmethod(lambda cls, environ: FakeTelegram())
    app_module.WordPress = lambda *args, **kwargs: FakeWordPress()
    app_module.OpenAIClient = lambda *args, **kwargs: FakeAI()
    app_module.LocalAI = lambda *args, **kwargs: FakeAI()
    app = app_module.create_app(data)

    @app.post('/__fixture/update')
    def add_update(body: dict):
        STATE['updates'].append(body)
        return {'ok': True}

    @app.get('/__fixture/state')
    def fixture_state():
        from studio.automation_store import public_state
        return {'automation': public_state(app.state.store, AT, target=FakeWordPress.url),
                'sent': STATE['sent'], 'posts': STATE['posts']}

    @app.post('/__fixture/tick')
    def fixture_tick(body: dict | None = None):
        automation = getattr(app.state, 'automation', None)
        if automation is None:
            return {'ok': False, 'error': 'not running'}
        moment = datetime.fromisoformat(body['at']) if body and body.get('at') else AT - timedelta(minutes=10)
        automation.tick(moment)
        automation.deliver(moment)
        return {'ok': True}
    @app.post('/__fixture/allow')
    def allow(body: dict):
        app.state.fixture_allowed = bool(body.get('allowed'))
        return {'ok': True}
    return app


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
    app = create()
    app.state.allow_fixture = True
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='error')


if __name__ == '__main__':
    main()
