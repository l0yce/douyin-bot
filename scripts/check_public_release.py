"""Check tracked release files for obvious secrets and private runtime artifacts."""
import re
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BAD_PARTS={'state','data','logs','backups','node_modules','.venv','venv','__pycache__','.git'}
PRIVATE_NAMES={'config.json','bridge-config.json','persona.md','panel_token','fault-alerts.json','fault-alerts-state.json'}
PATTERNS={
    'private-key':re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'github-token':re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b'),
    'api-key':re.compile(r'\bsk-[A-Za-z0-9_-]{24,}\b'),
    'douyin-identity':re.compile(r'\bMS4wLjAB[A-Za-z0-9_-]{20,}\b'),
    'credential-url':re.compile(r'https?://[^\s/:@]+:[^\s/@]+@[^\s]+'),
}

def release_files():
    result=subprocess.run(['git','-C',str(ROOT),'ls-files','-z'],capture_output=True)
    if result.returncode==0 and result.stdout:
        return [ROOT/p for p in result.stdout.decode().split('\0') if p]
    return [p for p in ROOT.rglob('*') if p.is_file() and not any(x in BAD_PARTS for x in p.relative_to(ROOT).parts)]

def check():
    findings=[];files=release_files()
    for p in files:
        rel=p.relative_to(ROOT)
        if any(x in BAD_PARTS for x in rel.parts) or p.name in PRIVATE_NAMES or p.suffix in {'.pem','.key','.db','.jsonl','.log','.pyc'} or '.original' in p.name or '.bak' in p.name:
            findings.append(f'{rel}: private runtime file');continue
        try:text=p.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            findings.append(f'{rel}: unexpected binary');continue
        for label,pattern in PATTERNS.items():
            # Placeholder URL in code comments is intentionally non-secret.
            candidate=text.replace('http://user:pass@host:port','http://example.invalid')
            if pattern.search(candidate):findings.append(f'{rel}: {label}')
    if findings:raise SystemExit('\n'.join(findings))
    print(f'PASS: {len(files)} public files checked; no known secret patterns or private runtime artifacts')

if __name__=='__main__':check()
