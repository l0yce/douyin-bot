"""Start the real bridge with empty temporary data and no model/network account."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from init_config import initialize

class BridgeSmoke(unittest.TestCase):
    @unittest.skipUnless(os.name!='nt' and shutil.which(os.environ.get('NODE_BIN','node')) and (ROOT/'douyin-interlude/node_modules/koishi').exists(),'Linux bridge smoke test requires npm dependencies')
    def test_authenticated_bridge_and_engine_start_with_empty_data(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp=Path(directory)
            for rel in ['douyin-friend-ai-bot/config/config.example.json','douyin-friend-ai-bot/config/persona.example.md','douyin-interlude/bridge-config.example.json','douyin-bot-panel/fault-alerts.example.json']:
                dest=tmp/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/rel,dest)
            initialize(tmp)
            bot=tmp/'douyin-friend-ai-bot/config/config.json'
            cfg=json.loads(bot.read_text(encoding='utf-8'));cfg['prompts']['system_prompt_file']=str(tmp/'douyin-friend-ai-bot/config/persona.md')
            # Guard accidental model calls: no valid remote endpoint or credentials.
            cfg['ai'].update(base_url='http://127.0.0.1:1/v1',api_key='offline-secret-sentinel',model='offline-test')
            bot.write_text(json.dumps(cfg),encoding='utf-8')
            options=tmp/'douyin-interlude/bridge-config.json'
            value=json.loads(options.read_text(encoding='utf-8'));value['autoAdvanceEnabled']=False
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));value['port']=sock.getsockname()[1]
            options.write_text(json.dumps(value),encoding='utf-8')
            log=tmp/'bridge.log'
            with log.open('w',encoding='utf-8') as handle:
                process=subprocess.Popen([os.environ.get('NODE_BIN','node'),'bridge.cjs'],cwd=ROOT/'douyin-interlude',env={**os.environ,'BOT_CONFIG':str(bot),'BRIDGE_CONFIG':str(options)},stdout=handle,stderr=subprocess.STDOUT)
                try:
                    url=f'http://127.0.0.1:{value["port"]}/health'
                    health=None;deadline=time.monotonic()+35
                    while time.monotonic()<deadline and process.poll() is None:
                        try:
                            req=urllib.request.Request(url,headers={'Authorization':'Bearer '+value['token']})
                            health=json.load(urllib.request.urlopen(req,timeout=2))
                            if health.get('ready'):break
                        except (OSError,ValueError):pass
                        time.sleep(.3)
                    self.assertTrue(health and health.get('ready'),log.read_text(encoding='utf-8'))
                    self.assertEqual(health['pending'],0)
                    with self.assertRaises(urllib.error.HTTPError) as rejected:urllib.request.urlopen(url,timeout=2)
                    self.assertEqual(rejected.exception.code,401)
                    req=urllib.request.Request(url.replace('/health','/admin'),data=json.dumps({'action':'config'}).encode(),headers={'Authorization':'Bearer '+value['token'],'Content-Type':'application/json'})
                    config=json.load(urllib.request.urlopen(req,timeout=5))
                    self.assertIsInstance(config,dict)
                    self.assertTrue('offline-secret-sentinel' not in json.dumps(config),'Config API must redact the key')
                finally:
                    process.terminate()
                    try:process.wait(timeout=12)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)

if __name__=='__main__':unittest.main()
