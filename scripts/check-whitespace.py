"""Standard Git checks, with immutable exact-path/line historical EOF exceptions.

No Git whitespace configuration is changed. All other diagnostics fail closed.
Working SHA-256 and index blob IDs pin the frozen files before allowing an EOF.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
WHITELIST = Path(__file__).with_name('frozen-eof-whitelist.json')


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(['git', *args], cwd=root, capture_output=True,
                          text=True, encoding='utf-8', errors='replace')


def check_result(result: subprocess.CompletedProcess, allowed: set[str]) -> tuple[list[str], list[str]]:
    lines = result.stdout.splitlines()
    accepted = [line for line in lines if line in allowed]
    rejected = [line for line in lines if line not in allowed]
    # A fatal Git error without diagnostics must not accidentally pass.
    if result.returncode not in (0, 1, 2) or (result.returncode != 0 and not lines):
        rejected.append('Git check failed without recognized whitespace diagnostics')
    return accepted, rejected


def self_test() -> None:
    """Use a disposable Git repo, never change the project/index/config."""
    with tempfile.TemporaryDirectory(prefix='iap-whitespace-') as directory:
        root = Path(directory)
        assert git(root, 'init', '-q').returncode == 0
        files = {'frozen.txt': b'frozen\n\n', 'new.txt': b'new\n\n',
                 'trailing.txt': b'new trailing \n'}
        for name, content in files.items():
            (root / name).write_bytes(content)
        assert git(root, 'add', '--', *files).returncode == 0
        result = git(root, 'diff', '--cached', '--check')
        allowed = {'frozen.txt:2: new blank line at EOF.'}
        accepted, rejected = check_result(result, allowed)
        assert accepted == list(allowed)
        assert any('new.txt:2: new blank line at EOF.' in line for line in rejected)
        assert any('trailing.txt:1: trailing whitespace.' in line for line in rejected)
        (root / 'frozen.txt').write_bytes(b'frozen \n\n')
        assert git(root, 'add', '--', 'frozen.txt').returncode == 0
        _, rejected = check_result(git(root, 'diff', '--cached', '--check'), allowed)
        assert any('frozen.txt:1: trailing whitespace.' in line for line in rejected)
        assert check_result(subprocess.CompletedProcess([], 128, '', 'fatal'), allowed)[1]
    print(json.dumps({'self_test': 'passed', 'new_eof_rejected': True,
                      'frozen_trailing_rejected': True, 'fatal_git_rejected': True}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    rows = json.loads(WHITELIST.read_text(encoding='utf-8'))
    assert len(rows) == len({row['path'] for row in rows}) == 16
    drift = []
    for row in rows:
        path = ROOT / row['path']
        blob = git(ROOT, 'rev-parse', ':' + row['path'])
        if (not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']
                or blob.returncode != 0 or blob.stdout.strip() != row['blob']):
            drift.append(row['path'])
    allowed = {f"{row['path']}:{row['line']}: new blank line at EOF." for row in rows}
    # Standard Git check with exact literal exclusions for non-whitelisted files.
    exclusions = [f":(top,exclude,literal){row['path']}" for row in rows]
    _, normal_errors = check_result(git(ROOT, 'diff', '--cached', '--check', '--', '.', *exclusions), set())
    # Also check every frozen file: only its exact historical EOF line is waived.
    accepted, all_errors = check_result(git(ROOT, 'diff', '--cached', '--check'), allowed)
    _, working_errors = check_result(git(ROOT, 'diff', '--check'), set())
    print(json.dumps({'whitelist_paths': len(rows), 'accepted_historical_eof': accepted,
                      'frozen_hash_or_blob_drift': drift, 'non_whitelisted_errors': normal_errors,
                      'unwaived_staged_errors': all_errors, 'working_errors': working_errors}))
    if drift or normal_errors or all_errors or working_errors:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
