"""Scheduled channel publisher; append-only, idempotent and bounded.

No external PropertyHub links in public Telegram content. All discoveries are
from server-side search with locally applied filters and a persisted watermark.
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from html import escape
from urllib.parse import quote
from .filters import clean
from .propertyhub import image_url,price_of
from .render import card_text,kb,link

log=logging.getLogger(__name__)
CHANNEL_MAX_PAGES=8
CHANNEL_MAX_PER_CYCLE=25

def deep_link(username:str,listing_id,mode='detail'):
    username=username.strip().lstrip('@')
    if not username or not username.replace('_','').isalnum():
        raise ValueError('Telegram bot username is unavailable')
    ident=str(listing_id)
    if not ident.isdigit():raise ValueError('Invalid listing ID')
    payload=ident if mode=='detail' else f'home_{ident}'
    return f'https://t.me/{username}?start={quote(payload)}'

def parse_start(text:str):
    parts=(text or '').strip().split(maxsplit=1)
    if len(parts)!=2 or parts[0].split('@')[0]!='/start':return None
    arg=parts[1].strip()
    if arg.isascii() and arg.isdigit() and len(arg)<22:
        return 'detail',arg
    if arg.startswith('home_') and arg[5:].isascii() and arg[5:].isdigit() and len(arg)<27:
        return 'home',arg[5:]
    return None

def channel_caption(item,username,markup,contact_label,rate=None):
    """Telegram photo captions are capped at 1024 chars (HTML markup included)."""
    rendered=card_text(item,rate=rate,markup=markup,contact_label=contact_label)
    url=deep_link(username,item['id'],'home')
    footer=f'\n\n<a href="{escape(url,quote=True)}">БОЛЬШЕ КВАРТИР ПОД ВАШИ КОНКРЕТНЫЕ ПРЕДПОЧТЕНИЯ</a>'
    # Avoid slicing inside HTML tags; drop complete lines instead.
    lines=rendered.splitlines()
    while lines and len('\n'.join(lines))+len(footer)>1000:
        lines.pop(-2 if len(lines)>2 else -1)
    return '\n'.join(lines)+footer

def iso_utc(s):
    try:
        x=datetime.fromisoformat(str(s).replace('Z','+00:00'))
        return x.astimezone(timezone.utc) if x.tzinfo else x.replace(tzinfo=timezone.utc)
    except (ValueError,TypeError):return None

async def publish_cycle(app):
    db=app.db
    if not await db.get_config('channel_enabled',False):return 0
    filters=clean(await db.get_config('channel_filters',{}))
    # Baseline defaults to when the feature was first enabled, so old ads
    # are never blasted into the channel on first run or after a filter edit.
    last=await db.get_config('channel_watermark')
    if not last:
        await db.set_config('channel_watermark',datetime.now(timezone.utc).isoformat())
        return 0
    watermark=iso_utc(last)
    if watermark is None:raise RuntimeError('Invalid channel watermark')
    bot_user=await app.bot.get_me()
    username=getattr(bot_user,'username',None)
    if not username:raise RuntimeError('Channel bot must have a username')
    discovered=[]
    for page in range(1,CHANNEL_MAX_PAGES+1):
        # A fresh provider result, no stale page cache for a latest-news feed.
        rows,meta=await app._search_page(filters,page,cache_minutes=0)
        for item in rows:
            ts=iso_utc(item.get('createdAt'))
            if ts and watermark<ts<=datetime.now(timezone.utc)+timedelta(minutes=5) and item.get('id') and price_of(item) is not None:
                discovered.append((ts,item))
        if page>=int(meta.get('totalPages') or 1):break
        await asyncio.sleep(.2)
    # Chronological posting and persistence, de-duplicate sponsored repeats.
    discovered.sort(key=lambda e:(e[0],str(e[1]['id'])))
    seen=set();published=0;latest=watermark
    markup=max(0,int(await db.get_config('retail_markup_thb',0)))
    for stamp,item in discovered:
        key=str(item['id'])
        if key in seen:continue
        seen.add(key)
        if await db.channel_post_exists(key):
            latest=max(latest,stamp)
            continue
        if published>=CHANNEL_MAX_PER_CYCLE:break
        caption=channel_caption(item,username,markup,app.cfg.contact_label,app.fx.rate)
        button=kb([[link('🏠 Информация о квартире',deep_link(username,key,'detail'))]])
        photo=image_url(item.get('coverPicture'))
        if photo:
            try:
                message=await app.bot.send_photo(app.cfg.channel_chat,photo,caption=caption,
                                                 parse_mode='HTML',reply_markup=button)
            except Exception as e:
                log.warning('Channel photo failed for listing %s, using text: %s',key,e)
                message=await app.bot.send_message(app.cfg.channel_chat,caption,
                    parse_mode='HTML',reply_markup=button,disable_web_page_preview=True)
        else:
            message=await app.bot.send_message(app.cfg.channel_chat,caption,
                parse_mode='HTML',reply_markup=button,disable_web_page_preview=True)
        await db.save_channel_post(key,app.cfg.channel_chat,message.message_id,item.get('createdAt'))
        published+=1
        latest=max(latest,stamp)
    # If no matching posts, keep watermark at previous watermark until now.
    # The scan covered at most 8 pages; if capped, retain progress for the next
    # cycle rather than moving past unsent items.
    if len(seen)<=CHANNEL_MAX_PER_CYCLE:
        latest=max(latest,datetime.now(timezone.utc))
    await db.set_config('channel_watermark',latest.isoformat())
    log.info('Channel cycle: %d posted (%d observed)',published,len(discovered))
    return published
