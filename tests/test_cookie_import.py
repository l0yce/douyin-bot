"""Cookie conversion, authenticated writes and one-time profile replacement."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'douyin-bot-panel/app'))
sys.path.insert(0, str(ROOT/'douyin-friend-ai-bot/src'))
import cookie_import


class CookieConversion(unittest.TestCase):
    def test_raw_header_preserves_encoded_values(self):
        cookies, warnings = cookie_import.convert('Cookie: sessionid=offline=fixture%3D; empty=; other=a+b')
        self.assertEqual(cookies[0]['value'], 'offline=fixture%3D')
        self.assertEqual(cookies[1]['value'], '')
        self.assertEqual(cookies[2]['value'], 'a+b')
        self.assertEqual(warnings, [])

    def test_export_and_storage_wrapper_normalize(self):
        cookies, warnings = cookie_import.convert(json.dumps({'cookies': [
            {'name':'sessionid', 'value':'offline', 'domain':'.douyin.com', 'session':True,
             'expirationDate':1, 'httpOnly':True, 'sameSite':'no_restriction'},
            {'name':'foreign', 'value':'offline', 'domain':'.example.com'},
            {'name':'expired', 'value':'offline', 'domain':'.douyin.com', 'expires':1}]}))
        self.assertEqual(len(cookies), 1)
        self.assertEqual(cookies[0]['expires'], -1)
        self.assertEqual(cookies[0]['sameSite'], 'None')
        self.assertTrue(cookies[0]['secure'])
        self.assertEqual(len(warnings), 2)
        self.assertEqual(set(cookies[0]), {'name','value','domain','path','expires','httpOnly','secure','sameSite'})

    def test_invalid_formats_never_echo_cookie_values(self):
        secret = 'offline-sensitive-sentinel'
        for content in ['', '[', json.dumps([{'name':'bad name', 'value':secret}]),
                        f'sessionid={secret}\nReferer: example', 'not-a-cookie',
                        json.dumps([{'name':'ok', 'value':secret, 'expires':'bad'}]),
                        json.dumps([{'name':'ok', 'value':secret, 'secure':'wrong'}]),
                        'a=one; a=two']:
            with self.subTest(content=content[:1]):
                with self.assertRaises(ValueError) as raised:
                    cookie_import.convert(content)
                self.assertNotIn(secret, str(raised.exception))


class CookieImportAPI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.bot = root/'bot'
        (self.bot/'config').mkdir(parents=True)
        (self.bot/'state').mkdir()
        (self.bot/'config/config.json').write_text(json.dumps({'account':{
            'cookies_file':'state/cookies.json','storage_state':'state/douyin_storage.json'}}))
        self.env = patch.dict(os.environ, {'BOT_DIR':str(self.bot), 'PANEL_DIR':str(root/'panel')})
        self.env.start()
        spec = importlib.util.spec_from_file_location('cookie_test_panel', ROOT/'douyin-bot-panel/app/app.py')
        self.panel = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.panel)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def request(self, content, auth=True):
        async def call():
            import httpx
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.panel.app), base_url='http://test') as client:
                return await client.post('/api/cookies/import',json={'content':content},
                    headers={'Authorization':'Bearer '+self.panel.TOKEN} if auth else {})
        return asyncio.run(call())

    def test_unauthorized_and_malformed_do_not_stop_or_write(self):
        with patch.object(self.panel, 'systemctl') as command:
            self.assertEqual(self.request('sessionid=offline', auth=False).status_code,401)
            self.assertEqual(self.request('[broken').status_code,400)
            command.assert_not_called()
        self.assertFalse((self.bot/'state/cookies.json').exists())

    def test_active_bot_stops_and_private_files_agree_without_secret_response(self):
        with patch.object(self.panel,'unit_properties',side_effect=[{'ActiveState':'active'}, {'ActiveState':'inactive'}]), \
             patch.object(self.panel,'systemctl',return_value=(0,'')) as command:
            response=self.request('sessionid=offline-sensitive-sentinel')
        self.assertEqual(response.status_code,200,response.text)
        command.assert_called_once_with('stop',self.panel.BOT_UNIT)
        self.assertNotIn('offline-sensitive-sentinel',response.text)
        cf=self.bot/'state/cookies.json'
        self.assertEqual(json.loads(cf.read_text()),json.loads((self.bot/'state/douyin_storage.json').read_text())['cookies'])
        self.assertTrue((self.bot/'state/cookies.json.import-pending').exists())
        if os.name!='nt':self.assertEqual(cf.stat().st_mode & 0o777,0o600)

    def test_discovery_and_stop_failure_preserve_existing_files(self):
        cf=self.bot/'state/cookies.json';cf.write_text('original')
        self.panel._discover_state['running']=True
        self.assertEqual(self.request('sessionid=offline').status_code,409)
        self.panel._discover_state['running']=False
        with patch.object(self.panel,'unit_properties',return_value={'ActiveState':'active'}), \
             patch.object(self.panel,'systemctl',return_value=(1,'')):
            self.assertEqual(self.request('sessionid=offline').status_code,503)
        self.assertEqual(cf.read_text(),'original')

    def test_partial_write_rolls_back(self):
        cf=self.bot/'state/cookies.json';cf.write_text('original')
        original=self.panel._private_write
        calls=0
        def fail_once(target,data):
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('disk failure')
            return original(target,data)
        with patch.object(self.panel,'unit_properties',return_value={'ActiveState':'inactive'}), \
             patch.object(self.panel,'_private_write',side_effect=fail_once):
            self.assertEqual(self.request('sessionid=offline').status_code,500)
        self.assertEqual(cf.read_text(),'original')
        self.assertFalse((self.bot/'state/douyin_storage.json').exists())


class BrowserCookieReplacement(unittest.TestCase):
    @unittest.skipIf(os.name=='nt','Firefox Linux smoke test')
    def test_persistent_profile_removes_old_cookie_jar(self):
        async def run():
            from playwright.async_api import async_playwright
            from browser_manager import BrowserSession
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);profile=root/'profile';cf=root/'cookies.json'
                async with async_playwright() as pw:
                    context=await pw.firefox.launch_persistent_context(str(profile),headless=True)
                    await context.add_cookies([{'name':'old-account','value':'offline','domain':'.douyin.com','path':'/'}])
                    await context.close()
                cf.write_text(json.dumps(cookie_import.convert('sessionid=offline-new')[0]))
                marker=cf.with_name(cf.name+'.import-pending');marker.write_text('pending')
                cfg={'account':{'engine':'firefox','headless':True,'profile_dir':str(profile),
                                'storage_state':str(root/'unused.json'),'cookies_file':str(cf)}}
                session=BrowserSession(cfg)
                try:
                    await session.start()
                    cookies=await session.context.cookies()
                    self.assertEqual([c['name'] for c in cookies],['sessionid'])
                    self.assertEqual(cookies[0]['value'],'offline-new')
                    self.assertFalse(marker.exists())
                finally:await session.stop()
        asyncio.run(run())


if __name__=='__main__':unittest.main()
