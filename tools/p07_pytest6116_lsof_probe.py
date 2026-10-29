"""Offline-install and inspect a Debian lsof candidate under bounded isolation.

No historical tests, target code, model, provider or admission is executed here.
Publisher hash is pinned; independent Debian signature verification is a later gate.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from atomic_flow.check_requests_execution import _bounded

ENGINE = '/usr/bin/docker'
BASE = 'python@sha256:1d52838af602b4b5a831beb13a0e4d073280665ea7be7f69ce2382f29c5a613f'
PACKAGE = 'https://deb.debian.org/debian/pool/main/l/lsof/lsof_4.95.0-1_amd64.deb'
PACKAGE_SHA256 = 'e4b15cf8d0b9051cf7957e7ab29a67ca7d21f42ea1b2b7dad1b52e65d02d1408'
PROBE = r'''
import json,os,subprocess,tempfile
f=tempfile.NamedTemporaryFile(dir='/tmp',delete=True)
f.write(b'p07-lsof-probe');f.flush()
pid=os.getpid()
def count():
    p=subprocess.run(['/usr/bin/lsof','-p',str(pid)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20)
    text=p.stdout.decode('utf8')
    return {'exit':p.returncode,'stderr':p.stderr.decode('utf8'),'regular':sum('REG' in x for x in text.splitlines()),
            'target_visible':f.name in text}
a=count();b=count()
print(json.dumps({'pid':pid,'first':a,'second':b,'fd_check':b['regular']<a['regular']+3},sort_keys=True))
'''


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def publisher_binary(archive):
    """Read one regular binary from the Debian payload without extracting paths."""
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:*') as tar:
        matches = [x for x in tar.getmembers() if x.name in ('./usr/bin/lsof', 'usr/bin/lsof')]
        if len(matches) != 1 or not matches[0].isfile() or matches[0].size > 4_000_000:
            raise ValueError('exact bounded publisher lsof binary required')
        return tar.extractfile(matches[0]).read()


def run(output):
    if os.name != 'posix' or output.exists():
        raise ValueError('fresh Linux output required')
    output.mkdir(parents=True)
    report = {'status': 'not_qualified', 'scope': 'lsof own-process capability only', 'provider_calls': 0,
              'ordinary_acceptance': False, 'publisher_signature_verified': False, 'target_tests_run': False}
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/tmp', 'LANG': 'C', 'LC_ALL': 'C'}
    def command(args, timeout=120):
        return subprocess.run(args, env=env, capture_output=True, timeout=timeout, check=False)
    with tempfile.TemporaryDirectory(prefix='p07-lsof-probe-') as temp:
        home = Path(temp)
        cidfile = home / 'cid'
        cid = None
        try:
            with urllib.request.urlopen(PACKAGE, timeout=60) as response:
                raw = response.read(1_000_001)
            if len(raw) != 318268 or digest(raw) != PACKAGE_SHA256:
                raise ValueError('Debian package bytes differ')
            deb = home / 'lsof.deb'
            deb.write_bytes(raw)
            payload = command(['dpkg-deb', '--fsys-tarfile', str(deb)])
            if payload.returncode or len(payload.stdout) > 8_000_000:
                raise ValueError('Debian package payload unavailable')
            binary = publisher_binary(payload.stdout)
            report.update(package_sha256=digest(raw), publisher_binary_sha256=digest(binary))
            pull = command([ENGINE, 'pull', BASE], timeout=240)
            (output / 'pull.stdout').write_bytes(pull.stdout)
            (output / 'pull.stderr').write_bytes(pull.stderr)
            if pull.returncode:
                raise ValueError('pinned base unavailable')
            inspected = command([ENGINE, 'image', 'inspect', BASE])
            base = json.loads(inspected.stdout)[0]
            if inspected.returncode or BASE not in base.get('RepoDigests', []) or base.get('Architecture') != 'amd64':
                raise ValueError('base image identity differs')
            dockerfile = ('FROM ' + BASE + '\nCOPY lsof.deb /tmp/lsof.deb\n'
                          'RUN --network=none dpkg -i /tmp/lsof.deb\n')
            (home / 'Dockerfile').write_text(dockerfile, encoding='ascii')
            (output / 'Dockerfile').write_text(dockerfile, encoding='ascii')
            built = command([ENGINE, 'build', '--pull=false', '--network=none', '--iidfile', str(home / 'iid'), str(home)], timeout=300)
            (output / 'build.stdout').write_bytes(built.stdout)
            (output / 'build.stderr').write_bytes(built.stderr)
            if built.returncode:
                raise ValueError('offline lsof installation failed; inspect build log')
            iid = (home / 'iid').read_text(encoding='ascii').strip()
            if not re.fullmatch(r'sha256:[a-f0-9]{64}', iid):
                raise ValueError('image ID missing')
            report['image_id'] = iid
            created = command([ENGINE, 'create', '--cidfile', str(cidfile), '--init', '--pull=never', '--network=none',
                '--read-only', '--user=65534:65534', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                '--memory=1g', '--cpus=2', '--pids-limit=128', '--tmpfs=/tmp:rw,noexec,nosuid,size=134217728,mode=1777',
                '--workdir=/tmp', '--env=HOME=/tmp', '--entrypoint=/bin/sleep', iid, 'infinity'])
            if created.returncode:
                raise ValueError('container creation failed')
            cid = cidfile.read_text(encoding='ascii').strip()
            if not re.fullmatch(r'[a-f0-9]{64}', cid):
                raise ValueError('container ID missing')
            inspected = command([ENGINE, 'inspect', cid])
            item = json.loads(inspected.stdout)[0]
            host = item['HostConfig']
            if (item['Image'] != iid or item['State']['Running'] is not False or item['Config']['User'] != '65534:65534'
                    or host['NetworkMode'] != 'none' or not host['ReadonlyRootfs'] or host['Privileged']
                    or host['CapDrop'] != ['ALL'] or host.get('CapAdd') or host.get('Binds') or item.get('Mounts')
                    or host['Init'] is not True or host['Memory'] != 1024**3 or host['NanoCpus'] != 2_000_000_000
                    or host['PidsLimit'] != 128 or 'no-new-privileges' not in host.get('SecurityOpt', [])):
                raise ValueError('container isolation differs')
            (output / 'container-inspect.json').write_bytes(inspected.stdout)
            copied = command([ENGINE, 'cp', cid + ':/usr/bin/lsof', str(home / 'installed-lsof')])
            if copied.returncode or (home / 'installed-lsof').read_bytes() != binary:
                raise ValueError('installed lsof differs from package binary')
            report['installed_binary_verified'] = True
            if command([ENGINE, 'start', cid]).returncode:
                raise ValueError('container start failed')
            proc = _bounded([ENGINE, 'exec', '--user=65534:65534', '-w', '/tmp', cid, '/usr/local/bin/python',
                             '-I', '-B', '-c', PROBE], home=home, timeout=60, limit=100000, check=lambda: None)
            (output / 'probe.stdout').write_bytes(proc.stdout.encode())
            (output / 'probe.stderr').write_bytes(proc.stderr.encode())
            if proc.returncode or proc.timed_out or proc.stdout_truncated or proc.stderr_truncated or proc.stderr:
                raise ValueError('lsof capability probe incomplete')
            result = json.loads(proc.stdout)
            (output / 'observation.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
            if not all(result[x]['exit'] == 0 and result[x]['target_visible'] and result[x]['regular'] > 0 for x in ('first', 'second')) or not result['fd_check']:
                raise ValueError('own-process regular-file check did not pass')
            report['status'] = 'own_process_capability_observed'
        except Exception as exc:
            report.update(error_type=type(exc).__name__, error=str(exc)[:500])
        finally:
            if cid is None and cidfile.exists():
                cid = cidfile.read_text(encoding='ascii').strip()
            if cid and re.fullmatch(r'[a-f0-9]{64}', cid):
                removed = command([ENGINE, 'rm', '-f', cid])
                absent = command([ENGINE, 'container', 'ls', '-a', '--no-trunc', '--filter', 'id=' + cid, '--format', '{{.ID}}'])
                report['cleanup_confirmed'] = not removed.returncode and not absent.returncode and not absent.stdout.strip()
                if not report['cleanup_confirmed']:
                    report['status'] = 'cleanup_uncertain'
    (output / 'summary.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    raise SystemExit(0 if run(parser.parse_args().output)['status'] == 'own_process_capability_observed' else 1)
