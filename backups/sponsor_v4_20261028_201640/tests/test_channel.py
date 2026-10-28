"""Offline regression coverage for retail publication and acquisition attribution."""
import unittest
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from datetime import datetime, timezone, timedelta
from phbot.pricing import payment_amount,public_text
import test_ui  # provides aiogram UI stubs if aiogram is absent
from phbot.render import card_text,details_text,card_kb,detail_kb,filter_kb
from phbot.channel import deep_link,parse_start,channel_caption,publish_cycle,publish_manual,channel_signature,discovery_orders
from phbot.filters import clean


def sample(listing_id=6522838,created=None):
    created=created or datetime.now(timezone.utc).isoformat()
    return {
        'id':str(listing_id), 'slug':'sample-condo', 'title':'Nice condo',
        'createdAt':created,'refreshedAt':created,'coverPicture':'/pictures/a.jpg',
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
        self.assertIn('🔄 Обновлено:',message)
        self.assertNotIn('propertyhub.in.th',message.lower())
        self.assertNotIn('0891234567',message)
        self.assertLessEqual(len(message),1024)

class FakeDB:
    def __init__(self, settings=None):
        self.settings=dict(settings or {'channel_enabled':True,'channel_filters':{},'retail_markup_thb':1000})
        self.seen={}
        self.posts={}
        self.listings={}
        self.config_updates=[]

    async def get_config(self,key,default=None):return self.settings.get(key,default)
    async def set_config(self,key,value):
        self.settings[key]=value
        self.config_updates.append((key,value))
    async def put_listings(self,rows):
        for row in rows:self.listings[str(row['id'])]=row
    async def channel_record_seen(self,rows,baseline=False):
        new=0
        for row in rows:
            ident=str(row['id'])
            if ident not in self.seen:
                self.seen[ident]=['baseline' if baseline else 'pending',row]
                if not baseline:new+=1
            else:self.seen[ident][1]=row
        return new
    async def channel_ignore_pending(self):
        for value in self.seen.values():
            if value[0]=='pending':value[0]='baseline'
    async def channel_pending(self,limit):
        return [value[1] for key,value in sorted(self.seen.items())
                if value[0]=='pending'][:limit]
    async def channel_pending_count(self):
        return sum(1 for value in self.seen.values() if value[0]=='pending')
    async def channel_post_exists(self,ident):return str(ident) in self.posts
    async def save_channel_post(self,ident,chat,message_id,created):
        self.posts[str(ident)]=(chat,message_id,created)
    async def channel_mark_published(self,ident):
        if str(ident) in self.seen:self.seen[str(ident)][0]='published'


class FakeBot:
    def __init__(self):self.photos=[];self.texts=[]
    async def get_me(self):return SimpleNamespace(username='thai_condo1_bot')
    async def send_photo(self,*args,**kwargs):
        self.photos.append((args,kwargs))
        return SimpleNamespace(message_id=len(self.photos))
    async def send_message(self,*args,**kwargs):
        self.texts.append((args,kwargs))
        return SimpleNamespace(message_id=100+len(self.texts))


def fake_app(rows=None,settings=None):
    db=FakeDB(settings)
    bot=FakeBot()
    app=SimpleNamespace(db=db,bot=bot,cfg=SimpleNamespace(
         channel_chat='@thai_condo',contact_label='Связь @nofumez'),
         fx=SimpleNamespace(rate=None),
         _search_page=AsyncMock(return_value=(rows or [],{'totalPages':1})))
    return app


class ChannelCycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_listings_are_not_new_even_when_first_seen(self):
        now = datetime.now(timezone.utc)
        old = sample(11, (now-timedelta(days=100)).isoformat())
        app = fake_app([old])

        self.assertEqual(await publish_cycle(app), 0)

        app._search_page.return_value = (
            [old, sample(12, (now-timedelta(days=90)).isoformat())],
            {'totalPages':1}
        )
        self.assertEqual(await publish_cycle(app), 0)
        self.assertEqual(len(app.db.posts), 0)

        fresh = sample(13)
        app._search_page.return_value = ([fresh, old], {'totalPages':1})
        self.assertEqual(await publish_cycle(app), 1)
        self.assertEqual(set(app.db.posts), {'13'})

    async def test_auto_scan_uses_creation_sort_only(self):
        self.assertEqual(discovery_orders({'sort':'REFRESHED_AT'}), ['CREATED_AT'])

        app = fake_app(
            [sample(1)],
            {'channel_enabled':True, 'channel_filters':{'sort':'REFRESHED_AT'}}
        )
        await publish_cycle(app)

        orders = [call.args[0]['sort'] for call in app._search_page.call_args_list]
        self.assertEqual(orders, ['CREATED_AT'])

    async def test_filter_change_resets_baseline(self):
        app=fake_app([sample(1)])
        await publish_cycle(app)
        app.db.settings['channel_filters']={'price_min':9000}
        app._search_page.return_value=([sample(2)],{'totalPages':1})
        self.assertEqual(await publish_cycle(app),0)
        self.assertNotIn('2',app.db.posts)
        app._search_page.return_value=([sample(2),sample(3)],{'totalPages':1})
        self.assertEqual(await publish_cycle(app),1)
        self.assertIn('3',app.db.posts)

    @patch('phbot.channel.CHANNEL_POST_DELAY',0)
    async def test_manual_fifty_posts_skips_already_published_and_respects_sort(self):
        app=fake_app([sample(i) for i in range(1,61)],
                     {'channel_enabled':False,'channel_filters':{'sort':'REFRESHED_AT'},'retail_markup_thb':500})
        app.db.posts['1']=('channel',1,'old')
        app.db.posts['2']=('channel',2,'old')
        result=await publish_manual(app,50)
        self.assertEqual(result['posted'],50)
        self.assertEqual(result['already_posted'],2)
        self.assertEqual(len(app.bot.photos),50)
        self.assertEqual(app.db.settings['channel_manual_progress']['status'],'done')
        self.assertEqual(app._search_page.call_args_list[0].args[0]['sort'],'REFRESHED_AT')
        self.assertEqual(set(app.db.posts),{str(i) for i in range(1,53)})
        result=await publish_manual(app,2)
        self.assertEqual(result['posted'],2)
        self.assertEqual(result['already_posted'],52)
        self.assertEqual(len(app.bot.photos),52)

    async def test_incomplete_scan_does_not_advance_baseline_or_checkpoint(self):
        app=fake_app([sample(1)])
        async def fail_second(filters,page,cache_minutes):
            if page==2:raise RuntimeError('provider timed out')
            return [sample(1)],{'totalPages':2}
        app._search_page.side_effect=fail_second
        with self.assertRaisesRegex(RuntimeError,'provider timed out'):
            await publish_cycle(app)
        self.assertFalse(app.db.seen)
        self.assertNotIn('channel_baseline_signature',app.db.settings)
        self.assertNotIn('channel_scan_checkpoint',app.db.settings)

    async def test_signature_changes_when_order_changes(self):
        self.assertNotEqual(channel_signature({'sort':'CREATED_AT'}),
                            channel_signature({'sort':'REFRESHED_AT'}))

    async def test_manual_bounds(self):
        app=fake_app()
        with self.assertRaises(ValueError):await publish_manual(app,0)
        with self.assertRaises(ValueError):await publish_manual(app,101)

    async def test_changed_filters_during_scan_are_not_published(self):
        app=fake_app([sample(1)])
        await publish_cycle(app)
        async def change_filters(filters,page,cache_minutes):
            app.db.settings['channel_filters']={'price_min':12500}
            return [sample(2)],{'totalPages':1}
        app._search_page.side_effect=change_filters
        self.assertEqual(await publish_cycle(app),0)
        self.assertNotIn('2',app.db.seen)
        self.assertFalse(app.db.posts)

    async def test_manual_post_is_not_republished_automatically(self):
        app=fake_app([sample(1)])
        await publish_cycle(app)  # baseline
        app._search_page.return_value=([sample(2)],{'totalPages':1})
        manual=await publish_manual(app,1)
        self.assertEqual(manual['posted'],1)
        self.assertEqual(await publish_cycle(app),0)
        self.assertEqual(len(app.bot.photos),1)

    async def test_channel_schema_ddl_valid(self):
        import ast,sqlite3
        from pathlib import Path
        tree=ast.parse((Path(__file__).resolve().parent.parent/'phbot'/'db.py').read_text(encoding='utf8'))
        schema=None
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='executescript':
                if node.args and isinstance(node.args[0],ast.Constant):schema=node.args[0].value
        self.assertIsNotNone(schema)
        connection=sqlite3.connect(':memory:')
        try:
            connection.executescript(schema)
            connection.execute('''INSERT INTO channel_seen
                (listing_id,first_seen,last_seen,status,listing_json,sort_order)
                VALUES('42','2026-10-28','2026-10-28','pending','{}',1)''')
            self.assertEqual(connection.execute('''SELECT status FROM channel_seen
                WHERE listing_id='42' ''').fetchone()[0],'pending')
        finally:connection.close()

if __name__=='__main__':unittest.main()
