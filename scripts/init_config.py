"""Create private configuration from public examples. Never overwrite local data."""
import argparse
import json
import os
from pathlib import Path
import secrets

def write_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return False
    # Exclusive create also protects concurrent initializers.
    with path.open('x', encoding='utf-8') as handle:
        os.chmod(path, 0o600)
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    return True

def initialize(root):
    bot=root/'douyin-friend-ai-bot'
    bridge=root/'douyin-interlude'
    panel=root/'douyin-bot-panel'
    for path in [bot/'state',bot/'logs',bridge/'data',panel/'data']:
        path.mkdir(parents=True,exist_ok=True)
        os.chmod(path,0o700)
    cfg=json.loads((bot/'config/config.example.json').read_text(encoding='utf-8'))
    cfg['interlude']['connection_file']=str(bridge/'bridge-config.json')
    write_private(bot/'config/config.json',cfg)
    persona=bot/'config/persona.md'
    if not persona.exists():
        with persona.open('x',encoding='utf-8') as handle:
            os.chmod(persona,0o600)
            handle.write((bot/'config/persona.example.md').read_text(encoding='utf-8'))
    opts=json.loads((bridge/'bridge-config.example.json').read_text(encoding='utf-8'))
    opts['token']=secrets.token_urlsafe(32)
    opts['databasePath']=str(bridge/'data/interlude.db')
    opts['statePath']=str(bridge/'data/bridge-state.json')
    # Let the worker use the locally configured persona instead of example profile.
    opts['story'].pop('characterProfile',None)
    write_private(bridge/'bridge-config.json',opts)
    write_private(panel/'data/fault-alerts.json',json.loads((panel/'fault-alerts.example.json').read_text(encoding='utf-8')))
    token=panel/'panel_token'
    if not token.exists():
        with token.open('x',encoding='utf-8') as handle:
            os.chmod(token,0o600)
            handle.write(secrets.token_urlsafe(32)+'\n')
    print('配置已初始化；现有配置不会覆盖。请在本地填写 AI、账号和白名单。')

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    initialize(parser.parse_args().root.resolve())
