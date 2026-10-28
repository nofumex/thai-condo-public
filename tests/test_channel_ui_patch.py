"""Regression tests for temporary channel notices and description-free listing UI."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import test_flow  # lightweight aiogram stubs when the SDK is not installed
from phbot.bot import Application, Catalog
from phbot.render import card_text, details_text
from phbot.channel import channel_caption
from phbot.filters import clean


def listing():
    return {
        'id': '6522838',
        'slug': 'zenith-place',
        'title': 'Zenith place Sukhumvit☎️ big 1 bed 42 sqm ‼️16000/month‼️ NOW AVAILABLE 🔆✅',
        'detail': 'DESCRIPTION_FROM_AGENT 16000 THB + LINE @oldagent',
        'project': {'nameEnglish': 'Zenith Place @ Sukhumvit', 'address': 'Bangkok'},
        'roomInformation': {'roomArea': 42, 'onFloor': '8', 'numberOfBed': 1},
        'location': {'lat': 13.715, 'lng': 100.587},
        'coverPicture': '/pictures/a.jpg',
        'price': {'forRent': {'monthly': {'price': 14000}}},
        'priceLandAndHouse': {'forRent': {
            'deposit': {'type': 'MONTH', 'month': 2},
            'advancePayment': {'type': 'MONTH', 'month': 1},
        }},
    }


class PublicPresentationTests(unittest.TestCase):
    def test_preview_never_includes_propertyhub_raw_headline(self):
        info = listing()
        caption = card_text(info, markup=1000, contact_label='Связь @nofumez')
        self.assertIn('🏙 <b>Zenith Place @ Sukhumvit</b>', caption)
        self.assertNotIn('16000/month', caption)
        self.assertNotIn('NOW AVAILABLE', caption)
        self.assertNotIn('DESCRIPTION_FROM_AGENT', caption)
        self.assertIn('15 000 ฿/мес.', caption)
        self.assertIn('\n\n👤 <b>Связь @nofumez</b>\n', caption)
        # The marketing headline must not be inserted between project and district.
        top = caption.splitlines()
        self.assertTrue(top[1].startswith('📍'), top[:3])

    def test_channel_caption_has_bold_short_deeplink(self):
        s = channel_caption(listing(),'thai_condo1_bot',1000,'Связь @nofumez')
        self.assertIn('<b><a href="https://t.me/thai_condo1_bot?start=home_6522838">', s)
        self.assertIn('БОЛЬШЕ КВАРТИР ПОД ВАШИ ПРЕДПОЧТЕНИЯ</a></b>', s)
        self.assertNotIn('КОНКРЕТНЫЕ', s)
        self.assertNotIn('16000/month',s)
        self.assertLessEqual(len(s),1024)

    def test_details_use_structured_fields_only(self):
        info=listing()
        s=details_text(info,info,markup=1000)
        self.assertIn('Zenith Place @ Sukhumvit',s)
        self.assertIn('15 000',s)
        self.assertNotIn('16000/month',s)
        self.assertNotIn('DESCRIPTION_FROM_AGENT',s)


class GalleryTests(unittest.IsolatedAsyncioTestCase):
    async def test_gallery_then_structured_details_no_separate_description(self):
        info = listing()
        full = dict(info, images=[
            {'pictureUrl':'/pictures/photo1.jpg'},
            {'pictureUrl':'/pictures/photo2.jpg'},
        ])
        app=Application.__new__(Application)
        bot=test_flow.FakeBot()
        bot.send_media_group=AsyncMock(return_value=[
            SimpleNamespace(message_id=201),SimpleNamespace(message_id=202)])
        app.bot=bot
        app.db=SimpleNamespace(get_listing=AsyncMock(return_value=(info,full)))
        app.fx=SimpleNamespace(rate=None)
        app.cfg=SimpleNamespace(contact_label='Связь @nofumez')
        app.retail_markup=1000
        catalog=Catalog(1,2,'channel-entry','Test',clean({}),ids=['6522838'])
        await app.show_detail(catalog)
        self.assertEqual(bot.send_media_group.await_count,1)
        self.assertEqual(len(bot.messages),2) # loader and summary; no description message
        summary=bot.messages[-1][2]['parse_mode']
        self.assertEqual(summary,'HTML')
        text=bot.messages[-1][1][1]
        self.assertIn('Zenith Place @ Sukhumvit',text)
        self.assertNotIn('16000/month',text)
        self.assertNotIn('DESCRIPTION_FROM_AGENT',text)
        self.assertIn(101,bot.deleted)  # loader removed
        self.assertEqual(catalog.extra_messages,[201,202,102])


class ManualNoticeTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_edits_and_deletes_one_notice(self):
        app=Application.__new__(Application)
        app.channel_lock=asyncio.Lock()
        app.bot=test_flow.FakeBot()
        app.bot.edit_message_text=AsyncMock()
        app.db=SimpleNamespace()
        returned={'posted':15,'already_posted':0,'inspected':60}
        with patch('phbot.bot.publish_manual',new=AsyncMock(return_value=returned)), \
             patch('phbot.bot.asyncio.sleep',new=AsyncMock()) as pause:
            await app.channel_manual_job(1,1,15,progress_id=501)
        app.bot.edit_message_text.assert_awaited_once()
        self.assertIn('15/15',app.bot.edit_message_text.await_args.args[0])
        self.assertEqual(app.bot.deleted,[501])
        self.assertEqual(app.bot.messages,[]) # no separate permanent success notification
        pause.assert_awaited_once_with(5)

    async def test_failure_still_removes_notice(self):
        app=Application.__new__(Application)
        app.channel_lock=asyncio.Lock()
        app.bot=test_flow.FakeBot()
        app.bot.edit_message_text=AsyncMock()
        app.db=SimpleNamespace(get_config=AsyncMock(return_value={'posted':4}),set_config=AsyncMock())
        with patch('phbot.bot.publish_manual',new=AsyncMock(side_effect=RuntimeError('API failed'))), \
             patch('phbot.bot.asyncio.sleep',new=AsyncMock()):
            await app.channel_manual_job(1,1,15,progress_id=501)
        self.assertEqual(app.bot.deleted,[501])
        self.assertIn('остановилась',app.bot.edit_message_text.await_args.args[0])
        self.assertEqual(app.bot.messages,[])

    async def test_locked_does_not_leave_a_warning_message(self):
        app=Application.__new__(Application)
        app.channel_lock=asyncio.Lock()
        app.bot=test_flow.FakeBot()
        app.bot.edit_message_text=AsyncMock()
        await app.channel_lock.acquire()
        try:
            with patch('phbot.bot.asyncio.sleep',new=AsyncMock()):
                await app.channel_manual_job(1,1,15,progress_id=501)
        finally:
            app.channel_lock.release()
        self.assertEqual(app.bot.deleted,[501])
        self.assertEqual(app.bot.messages,[])

if __name__=='__main__':unittest.main()
