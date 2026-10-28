"""Regression: a sponsored old match must not appear as first newest card
before the regular matching part of the provider's stream has been searched.
"""
import unittest
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from test_flow import Application, Catalog, FakeDB, FakeBot, sample
from phbot.filters import clean

NOW=datetime(2026,10,28,tzinfo=timezone.utc)


def item(ident, age_days, tier, months=1, monthly=13000):
    x=sample(ident)
    x['createdAt']=(NOW-timedelta(days=age_days)).isoformat()
    x['refreshedAt']=x['createdAt']
    x['sponsorPackage']=tier
    x['project']['nameEnglish']=f'Apartment {ident}'
    x['price']['forRent']['monthly']['price']=monthly
    x['priceLandAndHouse']={'forRent':{
        'deposit':{'type':'MONTH','month':months},
        'advancePayment':{'type':'MONTH','month':1}}}
    return x


class PhotoRecorder(FakeBot):
    def __init__(self):
        super().__init__()
        self.photos_sent=[]
        self.media_edited=[]
    async def send_photo(self,*args,**kwargs):
        self.photos_sent.append(kwargs.get('caption',''))
        return await super().send_photo(*args,**kwargs)
    async def edit_message_media(self,*args,**kwargs):
        self.media_edited.append(str(kwargs.get('media','')))
        return await super().edit_message_media(*args,**kwargs)


async def run_case(pages,filters=None):
    app=Application.__new__(Application)
    app.db=FakeDB()
    app.bot=PhotoRecorder()
    app.fx=SimpleNamespace(rate=None)
    app.photo_cache={}
    requests=[]
    async def search_page(f,page,cache_minutes=25):
        requests.append(page)
        rows,has_free=pages[page]
        return rows,{'totalPages':len(pages),'totalCount':len(pages)*60,
                     '_raw_has_regular':has_free}
    app._search_page=search_page
    c=Catalog(1,1,'search','Search',clean(filters or {'sort':'CREATED_AT'}))
    with patch('phbot.bot.SEARCH_RARE_PAGES',5), patch('phbot.bot.SEARCH_STANDARD_PAGES',5):
        await app._load(c)
    return app,c,requests


class SearchFirstCardRegression(unittest.IsolatedAsyncioTestCase):
    async def test_does_not_render_old_paid_when_first_free_fails_deposit_filter(self):
        # Page 2 has FREE results, but none passes the deposit rule and none
        # appears in rows returned by _search_page. The paid 2023 listing
        # must not be shown instead of a newer matching FREE on page 3.
        pages={
            1:([item(1,1203,'EXCLUSIVE',months=1)],False),
            2:([],True),
            3:([item(3,.02,'FREE',months=1)],True),
            4:([item(4,1,'FREE',months=1)],True),
        }
        app,c,requests=await run_case(pages,{
            'sort':'CREATED_AT','price_min':10000,'price_max':15000,
            'deposit_max_months':1,'advance_max_months':1})
        self.assertEqual(requests,[1,2,3,4])
        self.assertEqual(c.ids,['3','4','1'])
        self.assertEqual(len(app.bot.photos_sent),1)
        self.assertIn('Apartment 3',app.bot.photos_sent[0])
        self.assertNotIn('Apartment 1',app.bot.photos_sent[0])

    async def test_newer_paid_may_legitimately_be_first(self):
        pages={
            1:([item(1,.01,'EXCLUSIVE')],False),
            2:([item(3,.02,'FREE')],True),
        }
        app,c,_=await run_case(pages)
        self.assertEqual(c.ids,['1','3'])
        self.assertEqual(len(app.bot.photos_sent),1)
        self.assertIn('Apartment 1',app.bot.photos_sent[0])

    async def test_only_paid_match_still_eventually_shows_result(self):
        pages={
            1:([item(1,1203,'EXCLUSIVE')],False),
            2:([],True),
            3:([],True)
        }
        app,c,_=await run_case(pages)
        self.assertEqual(c.ids,['1'])
        self.assertEqual(len(app.bot.photos_sent),1)
        self.assertIn('Apartment 1',app.bot.photos_sent[0])

    async def test_out_of_order_later_free_repairs_initial_card(self):
        pages={
            1:([item(1,1203,'PREMIUM')],False),
            2:([item(3,2,'FREE')],True),
            3:([item(4,.02,'FREE')],True),
        }
        app,c,_=await run_case(pages)
        self.assertEqual(c.ids,['4','3','1'])
        self.assertEqual(len(app.bot.photos_sent),1)
        self.assertIn('Apartment 3',app.bot.photos_sent[0])
        self.assertEqual(len(app.bot.media_edited),1)
        self.assertIn('Apartment 4',app.bot.media_edited[0])
        self.assertEqual(c.index,0)

if __name__=='__main__':unittest.main()
