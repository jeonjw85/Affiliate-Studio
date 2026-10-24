"""Automation orchestration: receive, compose, publish, notify.

Every transition is persisted before the next external request. Composition is
limited by each service's own daily quota; a request is never moved to another
service's slot. The web screen calls this object through thin routes.
"""
import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .automation_publish import (SnapshotConflict, ensure_draft, reconcile_post, schedule_post,
                                 update_snapshot)
from .automation_products import derive_state, distinct_providers, products_of
from .automation_schedule import claimable, slots_for_day
from .automation_store import (claim, enqueue_notice, finish, get_optional, get_settings,
                               public, public_state, record_id)
from .ai import reserve_ai_operation
from .writing_policy import check_article, compose, prepare_products, render_article

FINAL_STATES = ('checking', 'draft', 'draft_sending', 'scheduled', 'published', 'failed', 'uncertain')
RESUME_STATES = ('checking', 'draft', 'draft_sending', 'scheduled', 'uncertain')
QUOTA_STATES = ('checking', 'draft', 'draft_sending', 'scheduled', 'published')


def _providers(request):
    return distinct_providers(products_of(request))


def _has_remote(request_id, store):
    with store.connect() as c:
        publication = get_optional(store, c, 'automation_publication', record_id('pub', request_id))
    return bool(publication and publication.get('remote_id'))


def _due_for_check(request, at):
    """Poll no more often than the plan's reconciliation cadence."""
    last = request.get('checked_at')
    if not last:
        return True
    try:
        previous = datetime.fromisoformat(last)
    except ValueError:
        return True
    interval = 60 if request.get('run_at') and datetime.fromisoformat(request['run_at']) <= at + timedelta(minutes=1) else 300
    return at >= previous + timedelta(seconds=interval)


def _remember_check(store, request_id, at):
    with store.connect() as c:
        current = get_optional(store, c, 'automation_request', request_id)
        if current is not None:
            store.put(c, 'automation_request', {**current, 'checked_at': at.isoformat()})


class Automation:
    def __init__(self, store, telegram, ai, wordpress, *, clock=None, images=None, data_dir=None):
        self.store = store
        self.telegram = telegram
        self.ai = ai
        self.wordpress = wordpress
        self.images = images
        self.data_dir = data_dir or store.path.parent
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.running = False
        self.last_error = ''
        self._tasks = []

    async def start(self):
        """Run intake, processing and delivery on independent loops."""
        self.running = True
        self._tasks = [asyncio.create_task(self._loop(self.inbox, 1)),
                       asyncio.create_task(self._loop(self.tick, 5)),
                       asyncio.create_task(self._loop(self.deliver, 5))]

    async def _loop(self, stage, seconds):
        while self.running:
            try:
                await asyncio.to_thread(stage)
            except Exception as error:
                self.last_error = str(error) if isinstance(error, ValueError) else '자동 운영 처리 오류'
            await asyncio.sleep(seconds)

    async def stop(self):
        self.running = False
        for task in getattr(self, '_tasks', []):
            task.cancel()
        for task in getattr(self, '_tasks', []):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []

    def status(self):
        return {'configured': bool(self.telegram and self.ai and self.wordpress),
                'telegram': bool(self.telegram), 'ai': bool(self.ai),
                'wordpress': bool(self.wordpress), 'image': bool(self.images),
                'running': self.running, 'last_error': self.last_error}

    def public(self, at=None):
        return public_state(self.store, at or self.clock(),
                            target=getattr(self.wordpress, 'url', None))

    def inbox(self):
        """Fetch and persist updates; delivery happens separately."""
        if not self.telegram:
            return 0
        from .telegram_inbox import receive_update
        with self.store.connect() as c:
            cursor = get_optional(self.store, c, 'telegram_cursor', 'telegram_current')
        count = 0
        for update in self.telegram.updates((cursor or {}).get('next_offset', 0)):
            receive_update(self.store, self.telegram, update, self.clock())
            count += 1
        return count

    def tick(self, at=None):
        at = at or self.clock()
        settings = get_settings(self.store, at)
        if not settings['enabled']:
            return 0
        processed = 0
        with self.store.connect() as c:
            rows = [public(row) for row in self.store.rows(c, 'automation_request')]
        ready = sorted((row for row in rows if row.get('state') == 'ready'),
                       key=lambda row: row.get('created_at', ''))
        local_day = at.astimezone(ZoneInfo(settings['schedule']['timezone'])).date()
        day = local_day.isoformat()
        target = getattr(self.wordpress, 'url', '')
        slots = {provider: [slot for slot in slots_for_day(settings, local_day, target)
                            if slot['provider'] == provider]
                 for provider in ('coupang', 'toss')}
        used_slots = {row.get('slot_id') for row in rows if row.get('slot_id')}
        # One comparison post can include several services; it uses one slot
        # but consumes one unit from every included service's daily quota.
        def used(provider):
            return sum(1 for row in rows
                       if row.get('slot_date') == day
                       and row.get('state') in QUOTA_STATES + ('failed',)
                       and provider in _providers(row))

        for request in ready:
            providers = _providers(request)
            if not providers:
                continue
            available = [provider for provider in providers
                         if settings['schedule']['daily_limits'][provider]
                         and used(provider) < settings['schedule']['daily_limits'][provider]]
            if len(available) != len(providers):
                continue
            picks = {}
            for provider in providers:
                pick = next((slot for slot in slots[provider]
                             if slot['id'] not in used_slots and claimable(slot, at)), None)
                if pick is None:
                    break
                picks[provider] = pick
            if len(picks) != len(providers):
                continue
            with self.store.connect() as c:
                claimed = claim(self.store, c, 'automation_request', request['id'], ('ready',), at)
            if claimed is None:
                continue
            first = min(picks.values(), key=lambda slot: slot['run_at'])
            self._compose(claimed, settings, at, day, run_at=first['run_at'],
                          slot_id=first['id'], providers=providers)
            used_slots.update(slot['id'] for slot in picks.values())
            processed += 1
        if self.wordpress is not None:
            for request in (row for row in rows if row.get('update_state') == 'updating'):
                processed += self._apply_update(request, settings, at)
        for request in (row for row in rows if row.get('state') in RESUME_STATES):
            if self.wordpress is None:
                break
            if request['state'] in ('checking', 'draft', 'draft_sending') \
                    or (request['state'] == 'uncertain' and not _has_remote(request['id'], self.store)):
                self._resume(request, at)
                processed += 1
                continue
            if not _due_for_check(request, at):
                continue
            try:
                publication = reconcile_post(self.store, request['id'], self.wordpress, at)
            except Exception as error:
                self.last_error = str(error) if isinstance(error, ValueError) else 'WordPress 상태 확인 실패'
                _remember_check(self.store, request['id'], at)
                continue
            if publication['state'] == 'draft':
                # The draft exists but was never scheduled (recovered after a
                # lost response); finish the remaining step.
                self._resume({**request, 'state': 'draft'}, at)
                processed += 1
                continue
            if publication['state'] == 'published' and self.telegram:
                with self.store.connect() as c:
                    enqueue_notice(self.store, c, request['id'], 'published',
                                   f'발행 확인: {request["evidence"].get("name", "상품")}\n'
                                   f'{publication.get("remote_url", "")}',
                                   chat_id=self.telegram.chat_id)
            processed += 1
        return processed

    def _existing_images(self, request_id):
        from .automation_media import media_record
        result = {'featured': None, 'body': None}
        for kind in ('featured', 'body'):
            record = media_record(self.store, request_id, kind)
            if record and record.get('state') == 'uploaded':
                result[kind] = record
        return result

    def _prepare_images(self, request, products, settings, at):
        """Prepare both images independently; never raise into publishing."""
        from .automation_media import prepare_body_image, prepare_featured_image
        featured = body = None
        reasons = []
        evidence = products[0]['evidence'] if products else {}
        try:
            featured = prepare_featured_image(self.store, request['id'], evidence,
                                              self.wordpress, self.data_dir, at)
        except Exception:
            featured = None
            reasons.append('대표 이미지 준비 실패')
        try:
            body = prepare_body_image(self.store, request['id'], self.images, self.wordpress,
                                      products, settings, self.data_dir, at)
        except Exception:
            body = None
            reasons.append('본문 이미지 준비 실패')
        for record, label in ((featured, '대표'), (body, '본문')):
            if record and record.get('state') != 'uploaded':
                reasons.append(f'{label} 이미지 없음: {record.get("error") or "사용 불가"}')
        return {'featured': featured if featured and featured.get('state') == 'uploaded' else None,
                'body': body if body and body.get('state') == 'uploaded' else None,
                'reasons': reasons}

    def _resume(self, request, at):
        """Finish a request that stopped between draft creation and scheduling."""
        try:
            publication = ensure_draft(self.store, request['id'], self.wordpress, at)
            if publication['state'] == 'published':
                return
            due = datetime.fromisoformat(publication['run_at']) if publication.get('run_at') \
                else at + timedelta(minutes=15)
            if due < at + timedelta(minutes=5):
                due = at + timedelta(minutes=5)
            schedule_post(self.store, request['id'], self.wordpress, due, at)
        except Exception as error:
            self.last_error = str(error) if isinstance(error, ValueError) else 'WordPress 재개 실패'

    def _apply_update(self, request, settings, at):
        """Rebuild the article from every ready product and update the same post."""
        products = products_of(request)
        ready = [product for product in products if product['state'] == 'ready']
        if not ready:
            self._update_state(request['id'], 'awaiting_info', '')
            return 0
        try:
            prepared = prepare_products(ready, at)
            media = self._existing_images(request['id'])
            article = compose(self.ai, prepared, settings)
            problems = check_article(article, prepared)
            if problems:
                article = compose(self.ai, prepared, settings, feedback=problems)
                problems = check_article(article, prepared)
            if problems:
                raise ValueError(' / '.join(problems))
            body_image = media['body']['remote_url'] if media['body'] else None
            rendered = render_article(article, prepared, settings=settings, body_image=body_image)
            snapshot = {'title': article['title'], 'html': rendered['html'],
                        'featured_media': media['featured']['remote_id'] if media['featured'] else None,
                        'body_media': media['body']['remote_id'] if media['body'] else None,
                        'body_image_html': rendered.get('body_image_html', ''),
                        'disclosures': [__import__('studio.exporting', fromlist=['DISCLOSURES'])
                                        .DISCLOSURES[p] for p in distinct_providers(prepared)],
                        'products': [{'index': p['index'], 'provider': p['provider'],
                                      'affiliate_url': p['affiliate_url'], 'evidence': p['evidence']}
                                     for p in prepared],
                        'provider': prepared[0]['provider'],
                        'affiliate_url': prepared[0]['affiliate_url']}
            update_snapshot(self.store, request['id'], self.wordpress, snapshot, at)
            with self.store.connect() as c:
                enqueue_notice(self.store, c, request['id'], f'updated:{at.isoformat()}',
                               f'글 보완 완료: {article["title"]}', chat_id=self.telegram.chat_id
                               if self.telegram else 0)
            return 1
        except SnapshotConflict as error:
            self._update_state(request['id'], 'update_blocked', str(error))
            return 1
        except ValueError as error:
            self._update_state(request['id'], 'update_blocked', str(error))
            return 1
        except Exception:
            self.last_error = 'WordPress 보완 실패'
            return 0

    def _update_state(self, request_id, state, error):
        with self.store.connect() as c:
            current = get_optional(self.store, c, 'automation_request', request_id)
            self.store.put(c, 'automation_request',
                           {**current, 'update_state': state, 'update_error': error})

    def retry_update(self, request_id):
        with self.store.connect() as c:
            current = get_optional(self.store, c, 'automation_request', request_id)
            if current is None or current.get('update_state') != 'update_blocked':
                raise ValueError('수정 차단 상태의 요청만 재시도 가능')
            current.update(update_state='updating', update_error='')
            return self.store.put(c, 'automation_request', current)

    def _compose(self, request, settings, at, day, *, run_at=None, slot_id=None, providers=None):
        publish_at = datetime.fromisoformat(run_at) if run_at else at + timedelta(minutes=15)
        operation = None
        try:
            if self.ai is None:
                raise ValueError('AI 연결 설정 필요')
            with self.store.connect() as c:
                operation = reserve_ai_operation(self.store, c, request['id'], self.ai.daily_limit,
                                                 at, scope='automation')
            # Only products with enough evidence go into this post. A blocked
            # product waits for its own description and updates the same post
            # later; it must not add a link the article cannot describe.
            ready = [product for product in products_of(request) if product['state'] == 'ready']
            if not ready:
                raise ValueError('발행할 상품 정보 없음')
            products = prepare_products(ready, publish_at)
            media = self._prepare_images(request, products, settings, at)
            article = compose(self.ai, products, settings)
            problems = check_article(article, products)
            if problems:
                with self.store.connect() as c:
                    operation = reserve_ai_operation(self.store, c, request['id'],
                                                     self.ai.daily_limit, at, scope='automation')
                article = compose(self.ai, products, settings, feedback=problems)
                problems = check_article(article, products)
            if problems:
                raise ValueError(' / '.join(problems))
            body_image = media['body']['remote_url'] if media['body'] else None
            rendered = render_article(article, products, settings=settings,
                                      body_image=body_image)
            snapshot = {'title': article['title'], 'html': rendered['html'],
                        'featured_media': media['featured']['remote_id'] if media['featured'] else None,
                        'body_media': media['body']['remote_id'] if media['body'] else None,
                        'body_image_html': rendered.get('body_image_html', ''),
                        'disclosures': [__import__('studio.exporting', fromlist=['DISCLOSURES'])
                                        .DISCLOSURES[p] for p in distinct_providers(products)],
                        'products': [{'index': p['index'], 'provider': p['provider'],
                                      'affiliate_url': p['affiliate_url'], 'evidence': p['evidence']}
                                     for p in products],
                        'provider': products[0]['provider'],
                        'affiliate_url': products[0]['affiliate_url']}
            with self.store.connect() as c:
                self.store.put(c, 'automation_article', {
                    'id': record_id('article', request['id']), 'request_id': request['id'],
                    'title': article['title'], 'html': rendered['html'], 'snapshot': snapshot,
                    'provider': snapshot['provider'], 'affiliate_url': snapshot['affiliate_url'],
                    'style': article.get('style'), 'policy_version': article.get('policy_version'),
                    'checks': [], 'created_at': at.isoformat()})
                finish(self.store, c, 'automation_request', request['id'], request['_claim'],
                       {'state': 'checking', 'slot_date': day, 'slot_id': slot_id,
                        'version': request['version'] + 1})
            if self.wordpress is None:
                return
            with self.store.connect() as c:
                current = get_optional(self.store, c, 'automation_request', request['id'])
                self.store.put(c, 'automation_request', {**current, 'target': self.wordpress.url})
            draft = ensure_draft(self.store, request['id'], self.wordpress, at)
            if draft['state'] != 'draft':
                # Recovery is handled by the resume path on the next pass.
                return
            due = datetime.fromisoformat(run_at) if run_at else at + timedelta(minutes=15)
            publication = schedule_post(self.store, request['id'], self.wordpress, due, at)
            if publication['state'] == 'scheduled' and self.telegram:
                images = []
                if media['featured']:
                    images.append('대표 이미지 포함')
                else:
                    images.append('대표 이미지 없이 발행')
                if media['body']:
                    images.append('본문 이미지 포함')
                else:
                    images.append('본문 이미지 없이 발행')
                with self.store.connect() as c:
                    enqueue_notice(self.store, c, request['id'], 'scheduled',
                                   f'예약 확인: {article["title"]}\n{publication["run_at"]}\n'
                                   + ' · '.join(images),
                                   chat_id=self.telegram.chat_id)
        except Exception as error:
            message = str(error) if isinstance(error, ValueError) else '자동 작성 중 연결 오류'
            with self.store.connect() as c:
                current = get_optional(self.store, c, 'automation_request', request['id'])
                if operation is not None:
                    store_operation = get_optional(self.store, c, 'operation', operation['id'])
                    if store_operation is not None:
                        store_operation['state'] = 'failed'
                        self.store.put(c, 'operation', store_operation)
                # A transient failure returns the item to the queue without
                # consuming today's slot; permanent problems stop it.
                # Retry problems that stopped us before a remote post existed,
                # up to a bounded number of attempts. Once WordPress holds the
                # draft, recovery belongs to the resume/reconcile path instead.
                publication = get_optional(self.store, c, 'automation_publication',
                                           record_id('pub', request['id']))
                published = bool(publication and publication.get('remote_id'))
                attempts = current.get('attempts', 0) + 1
                transient = not published and attempts < 3
                next_state = 'ready' if transient else 'failed'
                self.store.put(c, 'automation_request', {
                    **current, 'state': next_state, 'error': message, '_claim': None,
                    'attempts': attempts, 'version': current['version'] + 1})
                enqueue_notice(self.store, c, request['id'], f'{next_state}:{current["version"]}',
                               f'자동 작성 {"재시도 대기" if transient else "실패"}: {message}',
                               chat_id=self.telegram.chat_id if self.telegram else 0)

            self.last_error = message

    def refresh(self, request_id, at=None):
        """Read-only remote status refresh for one request."""
        if not self.wordpress:
            raise ValueError('WordPress 연결 설정 필요')
        return reconcile_post(self.store, request_id, self.wordpress, at or self.clock())

    def deliver(self, at=None):
        if not self.telegram:
            return 0
        from .telegram import deliver_notice
        at = at or self.clock()
        with self.store.connect() as c:
            pending = [public(row) for row in self.store.rows(c, 'notice')
                       if row.get('state') == 'pending']
        for notice in reversed(pending):
            deliver_notice(self.store, self.telegram, notice['id'], at)
        return len(pending)
