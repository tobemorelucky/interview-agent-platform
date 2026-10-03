"""Read-only Git/secret audit plus an optional local hash checkpoint. No secret output."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.runtime/finalization/f4-freeze.json'

def git(*args):
    return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True,
                          encoding='utf-8', errors='replace')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--capture', action='store_true')
    args = parser.parse_args()
    files = git('ls-files', '--cached', '--others', '--exclude-standard').stdout.splitlines()
    config = dotenv_values(ROOT / '.env')
    public_defaults = {'interview_agent', 'minioadmin', 'change_me_to_a_random_string'}
    secrets = [v for k, v in config.items() if v and len(v) >= 8 and
               any(word in k for word in ('API_KEY','SECRET','PASSWORD','ACCESS_KEY')) and v not in public_defaults]
    for k in ('DATABASE_URL', 'CELERY_BROKER_URL', 'CELERY_RESULT_BACKEND'):
        if config.get(k):
            value = make_url(config[k]).password
            if value and value not in public_defaults:
                secrets.append(value)
    leaks = [f for f in files if (ROOT/f).is_file() and
             any(s.encode() in (ROOT/f).read_bytes() for s in secrets)]
    dangerous = [f for f in files if f == '.env' or f.startswith(('.runtime/', 'uploads/', 'private_resumes/', 'backups/')) or
                 f.endswith(('.dump','.backup','.pid','.log'))]
    ignored = {p: git('check-ignore','-q',p).returncode == 0 for p in (
        '.env', '.runtime/finalization/f4-freeze.json',
        '.runtime/backups/f0f1-20261003/interview_agent_before_0024.dump', 'output/playwright/example.png')}
    protected = {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in (
        '.env', '.runtime/backups/f0f1-20261003/interview_agent_before_0024.dump')}
    frozen = {f: hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in files if
        (ROOT/f).is_file() and f.startswith(('apps/api/src/','apps/worker/src/','apps/api/alembic/','apps/web/src/')) and
        f != 'apps/api/src/interview_api/core/config.py'}
    if args.capture:
        if STATE.exists():
            raise SystemExit('F4 capture already exists; refusing overwrite')
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(dict(frozen=frozen, protected=protected)), encoding='utf-8')
    baseline = json.loads(STATE.read_text()) if STATE.exists() else None
    drift = [f for f, h in baseline['frozen'].items() if frozen.get(f) != h] if baseline else []
    staged = git('diff','--cached').stdout
    result = dict(files=len(files), classification=dict(Counter(f.split('/')[0] for f in files)),
                  ignored=ignored, secret_leak_files=leaks, forbidden_artifacts=dangerous,
                  secret_in_diff=any(s in git('diff').stdout or s in staged for s in secrets),
                  frozen_drift=drift, protected_unchanged=not baseline or protected==baseline['protected'])
    print(json.dumps(result))
    assert not leaks and not dangerous and not result['secret_in_diff'] and all(ignored.values())
    assert not drift and result['protected_unchanged']

if __name__ == '__main__':
    main()
