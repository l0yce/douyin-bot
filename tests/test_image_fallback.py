"""Media failures must not prevent submitting the original text batch."""
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'douyin-friend-ai-bot/src'))
import runner
from ai_client import AIError
from douyin_chat import DouyinPageError
from reply_engine import Decision


class ImageFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def run_batch(self, *, bridge=True, capture_error=False, vision_result=None, text_error=False):
        images = [dict(kind='image', text='[图片]', media_token='test-1'),
                  dict(kind='image', text='[图片]', media_token='test-2')]
        selected = images + [dict(kind='text', text='这周一起玩吗？')]
        decision = Decision('reply', 'new', incoming_text='[图片]\n[图片]\n这周一起玩吗？')
        decision.selected_messages = selected
        chat = SimpleNamespace(open_friend=AsyncMock(return_value={'nickname': 'Example', 'unique_id': 'example'}),
                               read_messages=AsyncMock(return_value=list(reversed(selected))),
                               capture_message_image_png=AsyncMock(return_value=b'fake-png'))
        if capture_error:
            chat.capture_message_image_png.side_effect = DouyinPageError('unavailable')
        store = Mock()
        store.friend_state.return_value = {}
        audit = Mock()
        def generate(messages, image_b64=None):
            if image_b64:
                if vision_result is None:
                    raise AIError('image unsupported')
                return vision_result
            if text_error:
                raise AIError('text unavailable')
            return '好啊，周末一起玩'
        ai = Mock()
        ai.chat.side_effect = generate
        cfg = {'reply': {'images': {'enabled': True}}, 'whitelist': {}, 'prompts': {},
               'interlude': {'enabled': bridge}, 'ai': {'model': 'text-model'}}
        submit = AsyncMock()
        with patch.object(runner, 'decide_reply', return_value=decision), \
             patch.object(runner, 'split_decision', return_value=[decision]), \
             patch.object(runner, 'recover_pending', new=AsyncMock()), \
             patch.object(runner, 'native_vision_enabled', new=AsyncMock(return_value=False)), \
             patch.object(runner, 'interlude_submit', new=submit):
            result = await runner.handle_friend(chat=chat, cfg=cfg, friend={'name': 'Example'},
                                               store=store, audit=audit, ai=ai, dry_run=not bridge)
        return result, submit, ai, audit, store

    async def test_vision_error_submits_text_and_stops_repeated_image_calls(self):
        result, submit, ai, audit, store = await self.run_batch()
        self.assertEqual(result['action'], 'nothing')
        submit.assert_awaited_once()
        content = submit.call_args.args[5]
        self.assertIn('这周一起玩吗？', content)
        self.assertEqual(content.count('内容未能识别'), 2)
        self.assertEqual(submit.call_args.args[6], [])
        self.assertEqual(ai.chat.call_count, 1)
        store.set.assert_not_called()  # No premature acknowledgement on fallback.

    async def test_capture_failure_still_submits_batch(self):
        result, submit, ai, *_ = await self.run_batch(capture_error=True)
        submit.assert_awaited_once()
        self.assertIn('内容未能识别', submit.call_args.args[5])
        ai.chat.assert_not_called()

    async def test_empty_vision_output_falls_back(self):
        _, submit, ai, *_ = await self.run_batch(vision_result=' ')
        self.assertIn('内容未能识别', submit.call_args.args[5])
        self.assertEqual(ai.chat.call_count, 1)

    async def test_successful_vision_preserves_observations(self):
        _, submit, ai, *_ = await self.run_batch(vision_result='一个笑脸')
        self.assertEqual(submit.call_args.args[5].count('一个笑脸'), 2)
        self.assertNotIn('内容未能识别', submit.call_args.args[5])

    async def test_direct_reply_falls_back_to_text(self):
        result, _, ai, *_ = await self.run_batch(bridge=False)
        self.assertEqual(result['action'], 'dry_reply')
        self.assertEqual(result['reply_text'], '好啊，周末一起玩')
        self.assertEqual(ai.chat.call_count, 2)
        last = ai.chat.call_args
        self.assertIsNone(last.kwargs['image_b64'])
        self.assertIn('内容未能识别', last.args[0][-1]['content'])

    async def test_text_provider_failure_remains_retryable(self):
        result, _, _, audit, store = await self.run_batch(bridge=False, text_error=True)
        self.assertEqual(result['action'], 'error')
        self.assertIn('消息保留待重试', result['reason'])
        store.set.assert_not_called()


if __name__ == '__main__':
    unittest.main()
