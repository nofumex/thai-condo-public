"""Telegram channel publisher: durable observed-ID inventory and safe manual backfill.

Automatic posts are based on *first appearance in monitored search results*,
not createdAt/refreshedAt timestamps. A full initial baseline is recorded without
sending older listings. Overlapping CREATED_AT/REFRESHED_AT/configured-sort scans
plus durable IDs avoid time-watermark gaps and duplicates.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from html import escape
from urllib.parse import quote

from .filters import clean
from .propertyhub import image_url, price_of
from .render import card_text, kb, link

log = logging.getLogger(__name__)
CHANNEL_MAX_PAGES = 8  # per sort; intentionally bounded external requests
CHANNEL_MAX_PER_CYCLE = 25
CHANNEL_TEST_MAX = 100
CHANNEL_TEST_MAX_PAGES = 40
CHANNEL_POST_DELAY = 1.1  # prevent bursts into a Telegram channel


def deep_link(username: str, listing_id, mode='detail'):
    username = username.strip().lstrip('@')
    if not username or not username.replace('_', '').isalnum():
        raise ValueError('Telegram bot username is unavailable')
    ident = str(listing_id)
    if not ident.isdigit():
        raise ValueError('Invalid listing ID')
    payload = ident if mode == 'detail' else f'home_{ident}'
    return f'https://t.me/{username}?start={quote(payload)}'


def parse_start(text: str):
    parts = (text or '').strip().split(maxsplit=1)
    if len(parts) != 2 or parts[0].split('@')[0] != '/start':
        return None
    arg = parts[1].strip()
    if arg.isascii() and arg.isdigit() and len(arg) < 22:
        return 'detail', arg
    if arg.startswith('home_') and arg[5:].isascii() and arg[5:].isdigit() and len(arg) < 27:
        return 'home', arg[5:]
    return None


def channel_caption(item, username, markup, contact_label, rate=None):
    """HTML photo caption under Telegram's 1024-character limit."""
    rendered = card_text(item, rate=rate, markup=markup, contact_label=contact_label)
    url = deep_link(username, item['id'], 'home')
    footer = (f'\n\n<b><a href="{escape(url, quote=True)}">'
              'БОЛЬШЕ КВАРТИР ПОД ВАШИ ПРЕДПОЧТЕНИЯ</a></b>')
    lines = rendered.splitlines()
    while lines and len('\n'.join(lines)) + len(footer) > 1000:
        lines.pop(-2 if len(lines) > 2 else -1)
    return '\n'.join(lines) + footer


def iso_utc(s):
    try:
        x = datetime.fromisoformat(str(s).replace('Z', '+00:00'))
        return x.astimezone(timezone.utc) if x.tzinfo else x.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def channel_signature(filters):
    """Changes to any publication filter trigger a fresh no-backfill baseline."""
    value = json.dumps(clean(filters), sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def discovery_orders(filters):
    """Configured sorting works, while new/updated listings remain discoverable."""
    ordered = [filters.get('sort') or 'CREATED_AT', 'CREATED_AT', 'REFRESHED_AT']
    return list(dict.fromkeys(ordered))


async def _scan(app, filters, orders, pages):
    """Return all matching rows after *all* requested pages have succeeded.

    No watermark or baseline checkpoint advances on partial/failed scans.
    Overlapping scans intentionally re-read the same listings every cycle.
    """
    ids = {}
    scanned_pages = 0
    total_rows = 0
    for order in orders:
        current = dict(filters, sort=order)
        for page in range(1, pages + 1):
            rows, meta = await app._search_page(current, page, cache_minutes=0)
            if meta.get('staleCache'):
                raise RuntimeError('PropertyHub недоступен: канал не публикует объявления из устаревшего кэша')
            scanned_pages += 1
            total_rows += len(rows)
            for item in rows:
                ident = str(item.get('id') or '')
                if ident.isascii() and ident.isdigit() and price_of(item) is not None:
                    ids.setdefault(ident, item)
            if page >= int(meta.get('totalPages') or 1):
                break
            await asyncio.sleep(.15)
    return list(ids.values()), {'pages': scanned_pages, 'rows': total_rows, 'unique': len(ids)}


async def _post(app, item, username, markup):
    """Send a single channel post, then durably store its Telegram message id."""
    ident = str(item['id'])
    caption = channel_caption(item, username, markup, app.cfg.contact_label, app.fx.rate)
    button = kb([[link('🏠 Информация о квартире', deep_link(username, ident, 'detail'))]])
    photo = image_url(item.get('coverPicture'))
    if photo:
        try:
            message = await app.bot.send_photo(
                app.cfg.channel_chat, photo, caption=caption,
                parse_mode='HTML', reply_markup=button)
        except Exception as exc:
            # Bad/unsupported image URL may fall back to a text post. Do not
            # hide permissions, network errors, 429 or other uncertain errors.
            if exc.__class__.__name__ != 'TelegramBadRequest':
                raise
            log.warning('Channel image unavailable for %s: %s', ident, exc)
            message = await app.bot.send_message(
                app.cfg.channel_chat, caption, parse_mode='HTML',
                reply_markup=button, disable_web_page_preview=True)
    else:
        message = await app.bot.send_message(
            app.cfg.channel_chat, caption, parse_mode='HTML',
            reply_markup=button, disable_web_page_preview=True)
    await app.db.save_channel_post(ident, app.cfg.channel_chat,
                                   message.message_id, item.get('createdAt'))
    # Manual publishing can occur before the first automatic baseline.
    await app.db.channel_record_seen([item],baseline=True)
    await app.db.channel_mark_published(ident)
    return message


async def _bot_username(app):
    user = await app.bot.get_me()
    username = getattr(user, 'username', None)
    if not username:
        raise RuntimeError('Telegram-бот должен иметь username для deep links')
    return username


async def publish_cycle(app):
    """Monitor overlapping search windows; publish only never-before-seen IDs.

    First cycle for each filter revision populates a baseline (0 posts).
    Pending IDs are kept on disk until successfully published; a 25-post cap
    never skips them. Already published channel IDs are never sent again.
    """
    db = app.db
    if not await db.get_config('channel_enabled', False):
        return 0
    filters = clean(await db.get_config('channel_filters', {}))
    sig = channel_signature(filters)
    baseline = sig != await db.get_config('channel_baseline_signature', None)

    rows, scanned = await _scan(app, filters, discovery_orders(filters), CHANNEL_MAX_PAGES)
    # An admin may edit filters or disable publishing during the HTTP scan.
    # Never publish a stale scan using an obsolete profile.
    if (channel_signature(await db.get_config('channel_filters', {})) != sig
            or not await db.get_config('channel_enabled', False)):
        log.info('Channel filters/enablement changed mid-scan; discarding old result')
        return 0
    # All discovered rows must be in listings for channel deep links to work.
    await db.put_listings(rows)
    if baseline:
        await db.channel_ignore_pending()
    newly = await db.channel_record_seen(rows, baseline=baseline)
    if baseline:
        await db.set_config('channel_baseline_signature', sig)
        await db.set_config('channel_scan_checkpoint', datetime.now(timezone.utc).isoformat())
        status = {'status':'baseline','scanned_pages':scanned['pages'],
                  'scanned_rows':scanned['rows'], 'unique':scanned['unique'],
                  'new':0,'posted':0,'pending':0,
                  'checked_at':datetime.now(timezone.utc).isoformat()}
        await db.set_config('channel_last_cycle_stats', status)
        log.info('Channel baseline recorded: %d listings, %d pages',
                 scanned['unique'], scanned['pages'])
        return 0

    pending = await db.channel_pending(CHANNEL_MAX_PER_CYCLE)
    progress = {'status':'posting','scanned_pages':scanned['pages'],
                'scanned_rows':scanned['rows'],'unique':scanned['unique'],
                'new':newly,'posted':0,
                'pending':await db.channel_pending_count(),
                'checked_at':datetime.now(timezone.utc).isoformat()}
    await db.set_config('channel_last_cycle_stats',progress)
    username = await _bot_username(app) if pending else None
    markup = max(0, int(await db.get_config('retail_markup_thb', 0)))
    posted = 0
    for item in pending:
        if (channel_signature(await db.get_config('channel_filters', {})) != sig
                or not await db.get_config('channel_enabled', False)):
            log.info('Channel posting interrupted after settings update')
            return posted
        ident = str(item['id'])
        if await db.channel_post_exists(ident):
            await db.channel_mark_published(ident)
            continue
        await _post(app, item, username, markup)
        posted += 1
        if posted % 5 == 0:
            await db.set_config('channel_last_cycle_stats',
                dict(progress,posted=posted,pending=await db.channel_pending_count()))
        if posted < len(pending):
            await asyncio.sleep(CHANNEL_POST_DELAY)

    pending_count = await db.channel_pending_count()
    status = {'status':'ok','scanned_pages':scanned['pages'],
              'scanned_rows':scanned['rows'], 'unique':scanned['unique'],
              'new':newly, 'posted':posted, 'pending':pending_count,
              'checked_at':datetime.now(timezone.utc).isoformat()}
    await db.set_config('channel_last_cycle_stats', status)
    # Unlike the old time watermark, this checkpoint advances only after a
    # complete scan + successful publication loop, never after partial scan.
    await db.set_config('channel_scan_checkpoint', status['checked_at'])
    log.info('Channel cycle: discovered=%d posted=%d pending=%d scanned=%d',
             newly, posted, pending_count, scanned['rows'])
    return posted


async def publish_manual(app, requested: int):
    """Publish N first available matches according to selected channel sort.

    Manual posts intentionally include listings predating channel activation.
    Every ID is de-duplicated against existing channel posts, and test posts
    also enter observed inventory so the auto-publisher cannot repeat them.
    """
    if not isinstance(requested, int) or not 1 <= requested <= CHANNEL_TEST_MAX:
        raise ValueError(f'Количество должно быть от 1 до {CHANNEL_TEST_MAX}')
    db = app.db
    filters = clean(await db.get_config('channel_filters', {}))
    username = await _bot_username(app)
    markup = max(0, int(await db.get_config('retail_markup_thb', 0)))
    seen_in_run = set()
    posted = duplicates = inspected = pages = 0
    await db.set_config('channel_manual_progress',
                        {'requested':requested,'posted':0,'status':'running'})
    manual_sig=channel_signature(filters)
    for page in range(1, CHANNEL_TEST_MAX_PAGES + 1):
        if channel_signature(await db.get_config('channel_filters', {})) != manual_sig:
            raise RuntimeError('Фильтры канала изменились во время отправки. Повторите с новыми фильтрами.')
        # Respect the selected sort: newest/updated/cheapest can be chosen.
        rows, meta = await app._search_page(filters, page, cache_minutes=0)
        if meta.get('staleCache'):
            raise RuntimeError('PropertyHub недоступен: тестовая отправка не использует устаревший кэш')
        pages += 1
        inspected += len(rows)
        await db.put_listings(rows)
        for item in rows:
            if channel_signature(await db.get_config('channel_filters', {})) != manual_sig:
                raise RuntimeError('Фильтры канала изменились во время отправки.')
            ident = str(item.get('id') or '')
            if not ident.isdigit() or ident in seen_in_run or price_of(item) is None:
                continue
            seen_in_run.add(ident)
            if await db.channel_post_exists(ident):
                duplicates += 1
                continue
            await _post(app, item, username, markup)
            posted += 1
            await db.set_config('channel_manual_progress',
                {'requested':requested,'posted':posted,'status':'running'})
            if posted >= requested:
                break
            await asyncio.sleep(CHANNEL_POST_DELAY)
        if posted >= requested or page >= int(meta.get('totalPages') or 1):
            break
        await asyncio.sleep(.2)
    result = {'status':'done','requested':requested,'posted':posted,
              'already_posted':duplicates,'inspected':inspected,'scanned_pages':pages,
              'finished_at':datetime.now(timezone.utc).isoformat()}
    await db.set_config('channel_manual_progress', result)
    log.info('Manual channel publication: %s', result)
    return result
