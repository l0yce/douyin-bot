"""Deployment boundaries: no production accounts, model calls or messages."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
sys.path.insert(0,str(ROOT/'douyin-friend-ai-bot/src'))
from init_config import initialize
from config_loader import load_config
from conversation_context import with_conversation_context

class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        for rel in ['douyin-friend-ai-bot/config/config.example.json','douyin-friend-ai-bot/config/persona.example.md','douyin-interlude/bridge-config.example.json','douyin-bot-panel/fault-alerts.example.json']:
            dest=self.root/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/rel,dest)
        initialize(self.root)
    def tearDown(self):self.tmp.cleanup()
    def test_init_preserves_local_data_and_random_tokens(self):
        token=self.root/'douyin-bot-panel/panel_token'
        before=token.read_text()
        self.assertGreaterEqual(len(before.strip()),40)
        cfg=self.root/'douyin-friend-ai-bot/config/config.json'
        saved=json.loads(cfg.read_text());saved['owner']['nickname']='local test owner'
        cfg.write_text(json.dumps(saved))
        initialize(self.root)
        self.assertEqual(token.read_text(),before)
        self.assertEqual(json.loads(cfg.read_text())['owner']['nickname'],'local test owner')
        opts=json.loads((self.root/'douyin-interlude/bridge-config.json').read_text())
        self.assertNotEqual(opts['token'],before.strip())
        self.assertEqual(Path(opts['databasePath']).parent,self.root/'douyin-interlude/data')
        self.assertFalse(opts['allowProactiveMessages'])
        if os.name!='nt':self.assertEqual(token.stat().st_mode&0o777,0o600)
    def test_example_configuration_loads_in_bot_cwd(self):
        old=Path.cwd()
        try:
            os.chdir(self.root/'douyin-friend-ai-bot')
            cfg=load_config('config/config.json',require_friends=False,require_ai=False)
            self.assertTrue(Path(cfg['interlude']['connection_file']).exists())
            self.assertEqual(cfg['reply']['conversation_context']['history_messages'],7)
            self.assertEqual(cfg['reply']['video_comments']['count'],5)
            with self.assertRaises(ValueError):load_config('config/config.json')
        finally:os.chdir(old)
    def test_panel_starts_in_isolated_directory_and_requires_auth(self):
        async def run():
            import httpx
            spec=importlib.util.spec_from_file_location('public_test_panel',ROOT/'douyin-bot-panel/app/app.py')
            mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mod.app),base_url='http://test') as client:
                self.assertEqual((await client.get('/api/whitelist')).status_code,401)
                headers={'Authorization':'Bearer '+mod.TOKEN}
                self.assertEqual((await client.get('/api/whitelist',headers=headers)).status_code,200)
                alerts=await client.get('/api/fault-alerts',headers=headers)
                self.assertEqual(alerts.status_code,200)
                self.assertNotIn('auth_code',alerts.json()['settings'])
                self.assertFalse(alerts.json()['settings']['enabled'])
                video=await client.get('/api/video-comments',headers=headers)
                self.assertEqual(video.status_code,200)
                self.assertFalse(any('/api/xhh/' in r.path for r in mod.app.routes))
        with patch.dict(os.environ,{'PANEL_DIR':str(self.root/'douyin-bot-panel'),'BOT_DIR':str(ROOT/'douyin-friend-ai-bot'),'INTERLUDE_DIR':str(self.root/'douyin-interlude')}):
            # BOT config must be temp; video_comments is imported via sys.path above.
            os.environ['BOT_DIR']=str(self.root/'douyin-friend-ai-bot')
            sys.path.insert(0,str(ROOT/'douyin-bot-panel/app'))
            try:asyncio.run(run())
            finally:sys.path.pop(0)

if __name__=='__main__':unittest.main()
