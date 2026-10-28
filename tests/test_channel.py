"""Offline regression coverage for retail publication and acquisition attribution."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import datetime, timezone, timedelta
from phbot.pricing import payment_amount,public_text
import test_ui  # provides aiogram UI stubs if aiogram is absent
from phbot.render import card_text,details_text,card_kb,detail_kb,filter_kb
from phbot.channel import deep_link,parse_start,channel_caption,publish_cycle
from phbot.filters import clean


def sample(listing_id=6522838,created=None):
    created=created or datetime.now(timezone.utc).isoformat()
    return {
        'id':str(listing_id), 'slug':'sample-condo', 'title':'Nice condo',
        'createdAt':created,'coverPicture':'/pictures/a.jpg',
        'price':{'forRent':{'monthly':{'price':13000}}},
        'priceLandAndHouse':{'forRent':{
            'deposit':{'type':'MONTH','month':2},
            'advancePayment':{'type':'MONTH','month':1}}},
        'project':{'nameEnglish':'Condo','address':'Bangkok'},
        'roomInformation':{'roomArea':35,'onFloor':'12','numberOfBed':1},
        'contactInformation':[{'name':'Real Agent','phone':['0891234567'],'lineId':'@realestate'}],
        'location':{'lat':13.7,'lng':100.5}}

class PricingTests(unittest.TestCase):
    def test_retail_terms_and_contact_scrubbing(self):
        x=sample()
        text=card_text(x,markup=1000)
        self.assertIn('14 000 ฿/мес.',text)
        self.assertIn('Депозит: 2 мес. · 28 000 ฿',text)
        self.assertIn('Предоплата: 1 мес. · 14 000 ฿',text)
        self.assertIn('👤 <b>Аренда, консультация и подбор квартиры - @nofumez</b>',text)
        self.assertNotIn('Real Agent',text)
        self.assertNotIn('0891234567',text)
        self.assertNotIn('@realestate',text)
        self.assertIn('\n\n💰',text)
        self.assertIn('฿/мес.</b>\n\n',text)
        details=details_text(x,x,markup=1000)
        self.assertIn('При заселении: 42 000 ฿',details)
        self.assertNotIn('@realestate',details)
        self.assertNotIn('Real Agent',details)
        self.assertFalse(any(getattr(btn,'url',None) for row in card_kb(x,0,1).inline_keyboard for btn in row))
        self.assertFalse(any(getattr(btn,'url',None) for row in detail_kb(x).inline_keyboard for btn in row))
    def test_ratio_for_nominal_deposit(self):
        amount,months=payment_amount({'type':'AMOUNT','amount':26000},13000,1000)
        self.assertEqual((amount,months),(28000,2.0))
        amount,months=payment_amount({'type':'AMOUNT','amount':20700},11500,1000)
        self.assertAlmostEqual(amount,22500)
        self.assertEqual(months,1.8)
    def test_contact_string_scrubbed(self):
        text='Bedroom 1\nContact agent @abcde on LINE\nPhone 0891234567\nKitchen and balcony'
        self.assertEqual(public_text(text,multiline=True),'Bedroom 1\nKitchen and balcony')
    def test_channel_filter_buttons(self):
        board=filter_kb('channel',clean({})).inline_keyboard
        self.assertEqual(board[-1][0].callback_data,'admin:channel')
        self.assertTrue(all(btn.callback_data is None or btn.callback_data.startswith(('f:c:','admin:')) for row in board for btn in row))

class DeepLinkTests(unittest.TestCase):
    def test_detail_and_home_have_distinct_payload(self):
        self.assertEqual(deep_link('thai_condo1_bot',6522838),'https://t.me/thai_condo1_bot?start=6522838')
        self.assertEqual(deep_link('thai_condo1_bot',6522838,'home'),'https://t.me/thai_condo1_bot?start=home_6522838')
        self.assertEqual(parse_start('/start 6522838'),('detail','6522838'))
        self.assertEqual(parse_start('/start home_6522838'),('home','6522838'))
        self.assertIsNone(parse_start('/start hi'))
    def test_channel_caption_single_footer_and_no_source(self):
        message=channel_caption(sample(),'thai_condo1_bot',1000,'Связь с нами @nofumez')
        self.assertIn('14 000',message)
        self.assertIn('start=home_6522838',message)
        self.assertIn('БОЛЬШЕ КВАРТИР',message)
        self.assertNotIn('propertyhub.in.th',message.lower())
        self.assertNotIn('0891234567',message)
        self.assertLessEqual(len(message),1024)

class ChannelCycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_publishes_new_only_once_and_records_posts(self):
        now=datetime.now(timezone.utc)
        fresh=sample(11,(now-timedelta(minutes=1)).isoformat())
        old=sample(12,(now-timedelta(hours=2)).isoformat())
        settings={'channel_enabled':True,'channel_filters':{},'channel_watermark':(now-timedelta(minutes=20)).isoformat(),'retail_markup_thb':1000}
        seen=set();stored=[]
        class DB:
            async def get_config(self,key,default=None):return settings.get(key,default)
            async def set_config(self,key,value):settings[key]=value
            async def channel_post_exists(self,key):return key in seen
            async def save_channel_post(self,key,*args):seen.add(key);stored.append((key,args))
        class Bot:
            def __init__(self):self.photos=[]
            async def get_me(self):return SimpleNamespace(username='thai_condo1_bot')
            async def send_photo(self,*args,**kwargs):
                self.photos.append((args,kwargs))
                return SimpleNamespace(message_id=101)
        bot=Bot()
        app=SimpleNamespace(db=DB(),bot=bot,cfg=SimpleNamespace(channel_chat='@thai_condo',contact_label='Связь @nofumez'),fx=SimpleNamespace(rate=None),
                            _search_page=AsyncMock(return_value=([fresh,old],{'totalPages':1})))
        self.assertEqual(await publish_cycle(app),1)
        self.assertEqual(stored[0][0],'11')
        self.assertEqual(len(bot.photos),1)
        self.assertEqual(bot.photos[0][1]['reply_markup'].inline_keyboard[0][0].url,
                         'https://t.me/thai_condo1_bot?start=11')
        self.assertEqual(await publish_cycle(app),0)
        self.assertEqual(len(bot.photos),1)

if __name__=='__main__':unittest.main()
