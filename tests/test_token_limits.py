"""Blank budgets omit the API limit; explicit budgets survive unchanged."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'douyin-friend-ai-bot/src'))
from ai_client import AIClient, AIError


class TokenLimitTests(unittest.TestCase):
    def request(self, settings, *, image=False, override=None):
        cfg = {'ai': {'base_url': 'https://api.example.com/v1', 'api_key': 'example',
                      'model': 'example-model', **settings}}
        captured = []
        def handle(request):
            import json
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})
        real_client = httpx.Client
        with patch('ai_client.httpx.Client', side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw)):
            AIClient(cfg).chat([{'role': 'user', 'content': 'hello'}],
                               image_b64='aW1hZ2U=' if image else None, max_tokens=override)
        return captured[0]

    def test_missing_and_blank_limits_are_omitted(self):
        for value in (None, '', 0):
            with self.subTest(value=value):
                for image in (False, True):
                    payload = self.request({'max_tokens': value, 'vision_max_tokens': value}, image=image)
                    self.assertNotIn('max_tokens', payload)
        self.assertNotIn('max_tokens', self.request({}))

    def test_explicit_text_vision_and_request_limits(self):
        cfg = {'max_tokens': 1234, 'vision_max_tokens': 2345}
        self.assertEqual(self.request(cfg)['max_tokens'], 1234)
        self.assertEqual(self.request(cfg, image=True)['max_tokens'], 2345)
        self.assertEqual(self.request(cfg, override=3456)['max_tokens'], 3456)

    def test_invalid_limits_are_rejected(self):
        for value in (-1, 'invalid', 1.5, True):
            with self.subTest(value=value), self.assertRaises(AIError):
                self.request({'max_tokens': value})


if __name__ == '__main__':
    unittest.main()
