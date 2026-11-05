"""Audit a downloaded CI archive against exact committed Git-object source bytes."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

root = Path(__file__).resolve().parents[2]
archive, sha = Path(sys.argv[1]), sys.argv[2]
with zipfile.ZipFile(archive) as z:
    def read(name):
        matches = [n for n in z.namelist() if n.rsplit('/', 1)[-1] == name]
        assert len(matches) == 1, (name, matches)
        item = z.getinfo(matches[0])
        assert item.file_size <= 8 * 1024 * 1024
        return z.read(item)
    assert read('commit.txt').decode().strip() == sha
    sources = json.loads(read('source.json'))
    for path, expected in sources.items():
        data = subprocess.check_output(['git', 'show', f'{sha}:{path}'], cwd=root)
        assert hashlib.sha256(data).hexdigest() == expected, path
    tests = read('tests.txt').decode('utf-8', errors='strict')
    results = re.findall(r'test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored;', tests)
    assert results and sum(int(row[1]) for row in results) == 0
    assert 'test result: FAILED' not in tests
    sdk = read('sdk-tests.txt').decode('utf-8', errors='strict')
    smoke = json.loads(read('http-smoke-result.json'))
    report = {
        'tested_commit': sha, 'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
        'source_files_exact_git_bytes': len(sources),
        'rust_pass': sum(int(row[0]) for row in results),
        'rust_ignored': sum(int(row[2]) for row in results),
        'sdk_test_summary': [line for line in sdk.splitlines() if re.search(r'(?:# |ℹ )(?:tests|pass|fail|skipped) ', line)],
        'native_http_smoke': smoke,
        'toolchain': read('rustc.txt').decode().strip(),
        'scope': 'CI engineering and installed-copy SDK integration; not production or live model acceptance',
    }
output = archive.with_suffix('.audit.json')
output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')
print(json.dumps({k:v for k,v in report.items() if k not in ('native_http_smoke','toolchain')}, ensure_ascii=False))
