"""Regression: old sponsored match must not pre-empt much newer FREE matches.

In production, deposit/advance filters are local. The first eligible FREE
may appear well after the sponsor block and hundreds of ineligible FREE rows.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from test_search_first_card_v403 import item, PhotoRecorder
from test_flow import Application, Catalog, FakeDB
from phbot.filters import clean


class RareSearchFirstCardV404(unittest.IsolatedAsyncioTestCase):
    async def test_page_13_and_20_matches_beat_old_paid_page_1(self):
        app=Application.__new__(Application)
        app.db=FakeDB()
        app.fx=SimpleNamespace(rate=None)
        app.photo_cache={}
        app.bot=PhotoRecorder()
        filters=clean({
            'region':'bangkok', 'price_min':10000,'price_max':15000,
            'deposit_max_months':1,'advance_max_months':1,
            'sort':'CREATED_AT',
        })
        c=Catalog(1,1,'search','Search',filters)
        page_calls=[]
        async def page(_filters,number,cache_minutes=25):
            page_calls.append(number)
            if number==1:
                rows=[item(1,1203,'PREMIUM',months=1)]
                regular=False
            elif number==13:
                rows=[item(13,2.32,'FREE',months=1)]
                regular=True
            elif number==20:
                rows=[item(20,4.36,'FREE',months=1)]
                regular=True
            else:
                rows=[]  # other 60 raw FREE listings all fail deposit rule
                regular=True
            return rows, {'totalPages':442,'totalCount':26502,
                          '_raw_has_regular':regular}
        app._search_page=page
        photo_scan_pages=[]
        real_send_photo=app.bot.send_photo
        async def record_photo(*args,**kwargs):
            photo_scan_pages.append(c.scanned_pages)
            return await real_send_photo(*args,**kwargs)
        app.bot.send_photo=record_photo
        with patch('phbot.bot.SEARCH_RARE_PAGES',25):
            await app._load(c)
        self.assertEqual(c.ids,['13','20','1'])
        self.assertEqual(page_calls,list(range(1,27)))
        self.assertEqual(len(app.bot.photos_sent),1)
        self.assertIn('Apartment 13',app.bot.photos_sent[0])
        self.assertEqual(photo_scan_pages,[26],
                         'First card must appear only after initial sorted scan')
        self.assertEqual(app.bot.media_edited,[])
        self.assertIsNone(c.progress_message)


if __name__=='__main__':unittest.main()
