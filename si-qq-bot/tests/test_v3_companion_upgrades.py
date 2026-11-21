"""Unit tests for v3 multi-bubble, sticker/voice tags, proactive outreach,
private offline files, xlsx/pptx parsing, group follow-ups, and clean LoRA context."""
from __future__ import annotations

import asyncio
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import companion_style
import companion_tools
import core
import media_support as ms
import model_connection as mc
import model_router as mr
import service_reply
from onebot_adapter import OneBotAdapter, _group_message_text


def _make_xlsx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr(
            'xl/sharedStrings.xml',
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<si><t>项目</t></si><si><t>预算</t></si>'
            '<si><t>服务器</t></si></sst>',
        )
        zf.writestr(
            'xl/worksheets/sheet1.xml',
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            '<row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row>'
            '<row><c t="s"><v>2</v></c><c><v>1200</v></c></row>'
            '</sheetData></worksheet>',
        )
    return buf.getvalue()


def _make_pptx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr(
            'ppt/slides/slide1.xml',
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>第一页标题</a:t></a:r></a:p>'
            '<a:p><a:r><a:t>核心内容</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>',
        )
    return buf.getvalue()


class CompanionV3UpgradeTests(unittest.TestCase):
    def test_multi_bubble_and_inline_reaction_and_voice_tags(self):
        fake_voice = {'type': 'record', 'data': {'file': 'base64://synthetic-wav'}}
        with tempfile.TemporaryDirectory() as tmp:
            with patch('voice_reply.check_service', return_value='available'), \
                 patch('voice_reply.prepare_reply', return_value=SimpleNamespace(voice_ready=True, segments=[fake_voice])):
                rep = service_reply.respond(
                    lambda: '哎呀||等等我看看[表情:开心][语音]',
                    (),
                    '早安',
                    tmp,
                    private=True,
                )
        self.assertEqual(str(rep), '哎呀\n等等我看看')
        self.assertEqual(rep.bubbles, ('哎呀', '等等我看看'))
        self.assertIsNone(rep.segment)
        self.assertEqual(len(rep.extra_segments), 2)
        self.assertEqual(rep.extra_segments[0]['type'], 'image')
        self.assertEqual(rep.extra_segments[1], fake_voice)

    def test_adapter_delivers_multi_bubbles_then_extra_segments(self):
        adapter = OneBotAdapter('ws://127.0.0.1:3001', 'tok', lambda _: None, send_pacing=0)
        sent = []
        finalized = []

        async def api(ws, pending, action, params):
            sent.append((action, params['message']))
            return {'status': 'ok', 'retcode': 0, 'data': {'message_id': len(sent)}}

        async def finalize(core_obj, token, delivered):
            finalized.append((token, delivered))
            return True

        adapter._api = api
        adapter._finalize_core = finalize
        sticker = {'type': 'image', 'data': {'file': 'base64://synthetic'}}
        reply = core.CoreReply(
            '第一段\n第二段',
            'chat',
            'tok-1',
            extra_segments=(sticker,),
            bubbles=('第一段', '第二段'),
        )
        asyncio.run(adapter._deliver_private_chunks(None, {}, Mock(), reply, '12345', ['第一段\n第二段'], True))
        self.assertEqual(len(sent), 3)
        self.assertEqual(sent[0][1], [{'type': 'text', 'data': {'text': '第一段'}}])
        self.assertEqual(sent[1][1], [{'type': 'text', 'data': {'text': '第二段'}}])
        self.assertEqual(sent[2][1], [sticker])
        self.assertEqual(finalized, [('tok-1', True)])

    def test_include_demo_false_skips_fake_samples_for_lora(self):
        msgs = [
            {'role': 'system', 'content': 'persona'},
            {'role': 'user', 'content': '我今天换了个新话题'},
        ]
        plain = companion_style.demonstrate(msgs, include_demo=False)
        self.assertEqual(plain, msgs)

        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            snap = mc.prepare_snapshot(mc.LOCAL_PROVIDER, 'http://127.0.0.1:11434/v1', 'si-qwen2.5-3b-lang-lora:latest', '')
            conn = mc.ModelConnection(Path(tmp) / 'mc.json', snap, request_fn=lambda e, m, msg, k: (calls.append(msg) or '好'))
            conn.configure_companion_tools(enabled=True, search_enabled=False)
            conn.configure_router(mr.RouterConfig(enabled=True))
            conn('', '', [{'role': 'user', 'content': '今天天气不错'}], '')
        flat = '\n'.join(str(m.get('content', '')) for m in calls[0])
        self.assertNotIn('刚把作业交了', flat)
        self.assertNotIn('我上周末去了哪儿', flat)

    def test_xlsx_and_pptx_document_text_extraction(self):
        xlsx_text = ms.document_text(_make_xlsx(), 'budget.xlsx')
        self.assertIn('项目 | 预算', xlsx_text)
        self.assertIn('服务器 | 1200', xlsx_text)
        pptx_text = ms.document_text(_make_pptx(), 'slides.pptx')
        self.assertIn('[第1页] 第一页标题 / 核心内容', pptx_text)

    def test_private_clear_turns_command_and_proactive_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = core.QQCore(tmp, '999000', request_reply=lambda *a: '你好呀||吃饭没？')
            code = c.begin_pairing()
            bound = c.handle_private('12345', 'm0', f'绑定 {code}')
            self.assertEqual(bound.kind, 'bound')
            r1 = c.handle_private('12345', 'm1', '记住我买了白鞋')
            self.assertTrue(c.finalize_reply(r1.delivery_token, True))
            self.assertEqual(len(c._turns), 1)
            cleared = c.handle_private('12345', 'm2', '/新对话')
            self.assertEqual(cleared.kind, 'mode')
            self.assertEqual(len(c._turns), 0)
            pro = c.proactive_private()
            self.assertIsNotNone(pro)
            self.assertEqual(pro.bubbles, ('你好呀', '吃饭没？'))
            self.assertTrue(c.finalize_reply(pro.delivery_token, True))
            self.assertEqual(len(c._turns), 1)

    def test_group_followup_and_offline_file_helpers(self):
        # Default strict mode still ignores un-@-ed message
        self.assertIsNone(_group_message_text([{'type': 'text', 'data': {'text': '小祀在吗'}}], '10001'))
        # Followup mode accepts calling 小祀, quoting bot msg, or active recent turn
        self.assertEqual(
            _group_message_text([{'type': 'text', 'data': {'text': '小祀在吗'}}], '10001', allow_followup=True),
            '小祀在吗',
        )
        self.assertEqual(
            _group_message_text(
                [{'type': 'reply', 'data': {'id': '777'}}, {'type': 'text', 'data': {'text': '接着说'}}],
                '10001',
                allow_followup=True,
                bot_msg_ids={'777'},
            ),
            '接着说',
        )
        self.assertEqual(
            _group_message_text([{'type': 'text', 'data': {'text': '为什么呀'}}], '10001', allow_followup=True, active_recent=True),
            '为什么呀',
        )

    def test_natural_reaction_request_and_synonym_mapping(self):
        import outbound_images
        self.assertEqual(outbound_images.request_kind('来张开心的表情包'), ('reaction', '开心'))
        with tempfile.TemporaryDirectory() as tmp:
            rep = service_reply.respond(lambda: '嘿嘿[表情:偷笑]', (), '随便聊聊', tmp, private=True)
            self.assertEqual(str(rep), '嘿嘿')
            self.assertEqual(len(rep.extra_segments), 1)
            self.assertEqual(rep.extra_segments[0]['type'], 'image')

    def test_polish_reply_never_keeps_admin_self_claim(self):
        msgs = [{'role': 'user', 'content': '你是谁？'}]
        self.assertEqual(companion_tools.polish_reply('我是管理员祀。', msgs), '我是祀。')
        self.assertEqual(companion_tools.polish_reply('我是管理员，找我有事吗？', msgs), '我是祀，找我有事吗？')
        self.assertEqual(companion_tools.polish_reply('管理员祀不会乱说的。', msgs), '祀不会乱说的。')
        self.assertEqual(companion_tools.polish_reply('哪有“管理员祀”这种叫法。', msgs), '哪有“管理员祀”这种叫法。')


if __name__ == '__main__':
    unittest.main()
