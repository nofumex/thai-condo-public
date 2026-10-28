"""Latest and 1/7/30 windows share a finalized Settings snapshot.

No network is required to open Latest after collection preparation. Paid
listings are sorted by creation timestamp rather than sponsor tier, and old
2023 ads do not leak into the 30-day Latest view.
"""
import unittest
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from phbot.bot import Application
from phbot.filters import clean
from phbot.collections import signature
from test_flow import FakeBot, FakeDB, sample

NOW=datetime.now(timezone.utc)

def condo(ident,days,tier):
    d=sample(ident)
    d['sponsorPackage']=tier
    d['createdAt']=(NOW-timedelta(days=days)).isoformat()
    d['project']['nameEnglish']=f'Apartment {ident}'
    return d

class SnapshotDB(FakeDB):
    def __init__(self):
        super().__init__()
        self.filters=clean({'sort':'CREATED_AT'})
        self.cache=None
    async def get_filters(self,uid,kind='latest'):return self.filters.copy()
    async def get_collection_cache(self,uid):return self.cache

class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def app_ready(self):
        db=SnapshotDB()
        await db.put_listings([
            condo(3820686,1203,'PREMIUM'),
            condo(6512308,4.38,'FREE'),
            condo(6516896,2.33,'FREE'),
            condo(1000000,35,'EXCLUSIVE'),
        ])
        db.cache={'filter_signature':signature(db.filters),'status':'ready',
                  'candidate_ids':['3820686','6512308','1000000','6516896'],
                  'scanned_pages':116,'target_pages':116,'buckets':{'1':[],'7':['6516896','6512308'],
                          '30':['6516896','6512308']}}
        app=Application.__new__(Application)
        app.db=db;app.bot=FakeBot();app.fx=SimpleNamespace(rate=None);app.photo_cache={}
        app.catalogs={};app.panel_kind={};app.panels={};app.retail_markup=0;app.tasks=set()
        return app

    async def test_latest_newest_free_beats_old_paid_from_same_snapshot(self):
        app=await self.app_ready()
        await app.latest_from_cache(1,1)
        self.assertEqual(app.catalogs[1].ids,['6516896','6512308'])
        self.assertTrue(app.catalogs[1].done)
        self.assertEqual(app.catalogs[1].source,'latest')
        self.assertIsNotNone(app.catalogs[1].message_id)
        # Reopening must never contact GraphQL.
        await app.latest_from_cache(1,1)
        self.assertEqual(app.catalogs[1].ids,['6516896','6512308'])

    async def test_price_sort_applies_to_same_snapshot(self):
        app=await self.app_ready()
        app.db.saved['6512308']['price']['forRent']['monthly']['price']=9000
        app.db.filters['sort']='PRICE_MONTHLY_LOW_TO_HIGH'
        app.db.cache['filter_signature']=signature(app.db.filters)
        await app.latest_from_cache(1,1)
        self.assertEqual(app.catalogs[1].ids,['6512308','6516896'])

    async def test_incomplete_snapshot_is_not_treated_as_final(self):
        app=await self.app_ready()
        cache=app.db.cache
        cache['scanned_pages']=80
        self.assertFalse(app._cache_is_complete(cache,app.db.filters))
        cache['scanned_pages']=116
        self.assertTrue(app._cache_is_complete(cache,app.db.filters))
        app.db.filters['price_min']=10000
        self.assertFalse(app._cache_is_complete(cache,app.db.filters))

if __name__=='__main__':unittest.main()
