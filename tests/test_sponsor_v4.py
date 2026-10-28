"""Sponsor-aware V4 regression tests; all offline, no Telegram/PropertyHub requests."""
import asyncio
import ast
import sqlite3
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_flow  # supplies offline aiogram stubs when dependency is absent
from test_flow import Application, Catalog, FakeBot, FakeDB, FakeAPI, sample
from test_channel import fake_app, sample as channel_sample
from phbot.channel import publish_manual, publish_cycle
from phbot.collections import build_buckets
from phbot.filters import clean
from phbot.sponsor import is_paid, is_regular, free_page_is_old, sort_items

NOW = datetime.now(timezone.utc)

def listing(ident, age, tier, price=10000, updated=None):
    x = sample(ident)
    x['createdAt'] = (NOW - timedelta(days=age)).isoformat()
    x['refreshedAt'] = (NOW - timedelta(days=updated if updated is not None else age)).isoformat()
    x['sponsorPackage'] = tier
    x['price']['forRent']['monthly']['price'] = price
    return x

class SponsorRulesTest(unittest.TestCase):
    def test_provider_explicit_tiers(self):
        self.assertTrue(is_paid({'sponsorPackage':'EXCLUSIVE'}))
        self.assertTrue(is_paid({'sponsorPackage':'PREMIUM'}))
        self.assertTrue(is_paid({'sponsorPackage':'STANDARD'}))
        self.assertFalse(is_paid({'sponsorPackage':'FREE'}))
        self.assertTrue(is_regular({'sponsorPackage':'FREE'}))
        self.assertFalse(is_regular({'sponsorPackage':'PREMIUM'}))

    def test_dates_beat_promoted_blocks(self):
        ads=[listing(1,400,'EXCLUSIVE'),listing(2,50,'PREMIUM'),listing(3,1,'STANDARD')]
        regular=[listing(4,.01,'FREE'),listing(5,.2,'FREE')]
        results=sort_items(ads+regular,'CREATED_AT')
        self.assertEqual([x['id'] for x in results],['4','5','3','2','1'])

    def test_price_and_refreshed_sort(self):
        items=[listing(1,3,'EXCLUSIVE',20000,1), listing(2,.2,'FREE',10000,.2), listing(3,1,'STANDARD',12000,.01)]
        self.assertEqual([x['id'] for x in sort_items(items,'PRICE_MONTHLY_LOW_TO_HIGH')],['2','3','1'])
        self.assertEqual([x['id'] for x in sort_items(items,'REFRESHED_AT')],['3','2','1'])

    def test_same_date_preserves_provider_order(self):
        same=NOW.isoformat()
        rows=[listing(30,1,'FREE'),listing(2,1,'FREE'),listing(1,1,'FREE')]
        for x in rows:x['createdAt']=same
        self.assertEqual([x['id'] for x in sort_items(rows)],['30','2','1'])

    def test_free_age_horizon_ignores_old_paid_ads(self):
        cutoff=NOW-timedelta(days=30)
        self.assertFalse(free_page_is_old([listing(1,400,'EXCLUSIVE')],cutoff))
        self.assertTrue(free_page_is_old([listing(2,40,'FREE')],cutoff))
        self.assertFalse(free_page_is_old([listing(3,1,'FREE')],cutoff))

    def test_collections_not_truncated_at_100(self):
        rows=[listing(i,.25,'FREE') for i in range(1,251)]
        buckets=build_buckets(rows,NOW)
        self.assertEqual(len(buckets['1']),250)
        self.assertEqual(len(buckets['7']),250)
        self.assertEqual(len(buckets['30']),250)

    def test_sponsored_index_schema(self):
        tree=ast.parse((Path(__file__).resolve().parent.parent/'phbot/db.py').read_text(encoding='utf8'))
        scripts=[n.args[0].value for n in ast.walk(tree)
                 if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)
                 and n.func.attr=='executescript' and n.args
                 and isinstance(n.args[0],ast.Constant) and isinstance(n.args[0].value,str)]
        self.assertTrue(scripts)
        db=sqlite3.connect(':memory:')
        try:
            db.executescript(scripts[0])
            cols={r[1] for r in db.execute('PRAGMA table_info(sponsored_inventory)')}
            self.assertTrue({'query_key','listing_id','sponsor_package','listing_json','observed_at'}<=cols)
        finally:db.close()

class SponsorFlowTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_first_result_is_newest_free_not_first_paid(self):
        pages={
            1:[listing(1,100,'EXCLUSIVE'),listing(2,10,'PREMIUM')],
            2:[listing(3,8,'STANDARD')],
            3:[listing(4,.01,'FREE'),listing(5,.2,'FREE')],
            4:[listing(6,2,'FREE')]
        }
        app=Application.__new__(Application)
        app.db=FakeDB();app.api=FakeAPI(pages);app.bot=FakeBot()
        app.fx=SimpleNamespace(rate=None);app.photo_cache={}
        c=Catalog(1,1,'latest','Новые',clean({}))
        with patch('phbot.bot.SEARCH_STANDARD_PAGES',3):
            await app._load(c)
        self.assertEqual(c.ids, ['4','5','6','3','2','1'])
        self.assertEqual(c.message_id is not None, True)
        self.assertEqual(app.api.requests,[1,2,3,4])

    async def test_manual_top_two_from_free_not_sponsor_pins(self):
        app=fake_app(settings={'channel_enabled':True,'channel_filters':{'sort':'CREATED_AT'},'retail_markup_thb':0})
        parts={
            1:[listing(1,300,'EXCLUSIVE'),listing(2,50,'PREMIUM')],
            2:[listing(3,20,'STANDARD')],
            3:[listing(4,.01,'FREE'),listing(5,.02,'FREE')]
        }
        async def page(filters,page,cache_minutes):
            return parts[page],{'totalPages':3}
        app._search_page.side_effect=page
        with patch('phbot.channel.CHANNEL_POST_DELAY',0):
            result=await publish_manual(app,2)
        self.assertEqual(result['posted'],2)
        self.assertEqual(set(app.db.posts),{'4','5'})

    async def test_automatic_never_backfills_old_paid_and_dedupes(self):
        app=fake_app(settings={'channel_enabled':True,'channel_filters':{},'retail_markup_thb':0})
        def make_page(items):
            async def page(filters,page,cache_minutes):return items,{'totalPages':1}
            return page
        app._search_page.side_effect=make_page([listing(1,300,'EXCLUSIVE'),listing(2,1,'FREE')])
        self.assertEqual(await publish_cycle(app),0)
        fresh=listing(3,-.0001,'FREE')
        # Use the actual baseline time, not module-level NOW: test runs can exceed 8.64 s.
        fresh['createdAt']=(datetime.now(timezone.utc)+timedelta(seconds=30)).isoformat()
        app._search_page.side_effect=make_page([fresh, listing(1,300,'EXCLUSIVE'),listing(2,1,'FREE')])
        with patch('phbot.channel.CHANNEL_POST_DELAY',0):
            self.assertEqual(await publish_cycle(app),1)
            self.assertEqual(await publish_cycle(app),0)
        self.assertEqual(set(app.db.posts),{'3'})

if __name__=='__main__':unittest.main()
