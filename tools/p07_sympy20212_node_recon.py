"""Bounded SymPy20212 native test-name reconnaissance, never an ordinary gate.

Imports test modules and expands generator tests inside isolated containers;
those operations can execute arbitrary target code. Ordinary test callables are
filtered at the native ``matches`` hook. This changes execution and cannot prove
what an unmodified test run would collect, finish, or pass. Data and claimed
counts from target processes are untrusted diagnostics, never receipts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import p07_sympy20212_partition_contract as partition
import p07_sympy20212_public_discovery as discovery
from p07_pytest6116_collection_recon import VERIFY_SOURCE

from atomic_flow.check_requests_execution import _bounded

ENGINE = '/usr/bin/docker'
MAX_OUTPUT = 4_000_000
BATCH_TIMEOUT = 150

SOURCE = r'''
import json,os,sys
sys.dont_write_bytecode=True
if any(os.environ.get(n) for n in ('TRAVIS_BUILD_NUMBER','PYTHONPATH','PYTHONHOME','PYTHONSTARTUP','PYTHONINSPECT','PYTHONUSERBASE')):
    raise SystemExit(125)
if os.path.realpath(os.getcwd())!='/testbed' or sys.executable!='/opt/miniconda3/envs/testbed/bin/python' or not sys.flags.isolated:
    raise SystemExit(125)
sys.path.insert(0,'/testbed')
import sympy
from sympy.testing import runtests
if runtests.ON_TRAVIS or os.path.realpath(sympy.__file__)!='/testbed/sympy/__init__.py' or os.path.realpath(runtests.__file__)!='/testbed/sympy/testing/runtests.py':
    raise SystemExit(125)
selectors=json.loads(sys.argv[1])
if not isinstance(selectors,list) or not 1<=len(selectors)<=256:
    raise SystemExit(125)
state={'active':None,'started':0,'finished':0,'nodes':0,'errors':0}
def emit(kind,**data):
    print('P07_NODE_RECON='+json.dumps({'kind':kind,**data},sort_keys=True),flush=True)
original_file=runtests.SymPyTests.test_file
original_matches=runtests.SymPyTests.matches
original_import_error=runtests.PyTestReporter.import_error
original_test_exception=runtests.PyTestReporter.test_exception
def file_hook(self,filename,*args,**kwargs):
    path=os.path.relpath(os.path.realpath(filename),'/testbed').replace(os.sep,'/')
    state['active']=path;state['started']+=1;emit('file_started',path=path)
    try:
        result=original_file(self,filename,*args,**kwargs)
    except BaseException as exc:
        state['errors']+=1;emit('file_raised',path=path,error_type=type(exc).__name__)
        raise
    else:
        state['finished']+=1;emit('file_finished',path=path)
        return result
    finally:
        state['active']=None
def match_hook(self,func):
    selected=bool(original_matches(self,func))
    state['nodes']+=1
    emit('candidate',path=state['active'],name=getattr(func,'__name__',None),
         qualname=getattr(func,'__qualname__',None),line=getattr(getattr(func,'__code__',None),'co_firstlineno',None),
         native_selected=selected)
    return False
def import_error_hook(self,filename,info):
    state['errors']+=1;emit('import_error',path=state['active'])
    return original_import_error(self,filename,info)
def exception_hook(self,info):
    state['errors']+=1;emit('test_exception',path=state['active'])
    return original_test_exception(self,info)
runtests.SymPyTests.test_file=file_hook
runtests.SymPyTests.matches=match_hook
runtests.PyTestReporter.import_error=import_error_hook
runtests.PyTestReporter.test_exception=exception_hook
code=runtests._test(*selectors,colors=False,seed=1)
emit('finished',native_exit=code,started=state['started'],completed=state['finished'],candidates=state['nodes'],errors=state['errors'],
     python=list(sys.version_info[:3]),sympy_version=sympy.__version__,on_travis=bool(runtests.ON_TRAVIS))
raise SystemExit(code)
'''


def summarize_events(stdout, batch):
    """Parse diagnostics without equating events with authenticated test IDs."""
    events = []
    for line in stdout.splitlines():
        if line.startswith('P07_NODE_RECON='):
            obj = json.loads(line.removeprefix('P07_NODE_RECON='))
            if type(obj) is not dict or obj.get('kind') not in {'file_started', 'file_finished', 'file_raised',
                    'candidate', 'import_error', 'test_exception', 'finished'}:
                raise ValueError('invalid diagnostic event')
            events.append(obj)
    started = [e['path'] for e in events if e['kind'] == 'file_started']
    finished = [e['path'] for e in events if e['kind'] == 'file_finished']
    if len(started) != len(set(started)) or len(finished) != len(set(finished)) or not set(finished) <= set(started):
        raise ValueError('duplicated or inconsistent file events')
    if not set(started) <= set(batch) or any(e.get('path') not in set(batch) for e in events if e['kind'] == 'candidate'):
        raise ValueError('nonpublic event outside batch')
    endings = [e for e in events if e['kind'] == 'finished']
    if len(endings) > 1:
        raise ValueError('duplicate final event')
    if endings:
        final = endings[0]
        if (final.get('started') != len(started) or final.get('completed') != len(finished)
                or final.get('candidates') != sum(e['kind'] == 'candidate' for e in events)
                or final.get('python') != [3, 9, 20] or final.get('on_travis') is not False):
            raise ValueError('final event conflicts with observed raw events')
    return {'started': len(started), 'finished': len(finished),
            'candidate_events': sum(e['kind'] == 'candidate' for e in events),
            'issue_events': sum(e['kind'] in {'file_raised', 'import_error', 'test_exception'} for e in events),
            'final_event_present': len(endings) == 1, 'missing_started': sorted(set(batch) - set(started)),
            'missing_finished': sorted(set(batch) - set(finished))}


def run(output):
    if os.name != 'posix' or output.exists():
        raise ValueError('fresh Linux output required')
    output.mkdir(parents=True)
    report = {'status': 'not_qualified', 'scope': 'bounded native hook diagnostics only', 'provider_calls': 0,
              'ordinary_acceptance': False, 'model_quality': 'not_measured', 'hidden_oracle': 'not_run',
              'test_module_imports_and_generator_code_execute': True, 'native_matches_hook_filters_callables': True,
              'target_event_authenticity_verified': False, 'batches': []}
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/tmp', 'LANG': 'C', 'LC_ALL': 'C'}
    def command(args, timeout=90):
        return subprocess.run(args, env=env, capture_output=True, check=False, timeout=timeout)
    try:
        file_report = discovery.run(output / 'file-discovery')
        if file_report.get('status') != 'native_file_discovery_matches_static_inventory':
            raise ValueError('file discovery failed or cleanup uncertain')
        rows = discovery.manifest_data(discovery.MANIFEST.read_bytes())
        files = [row['path'] for row in rows if not row['native_default_blacklisted']]
        planned = partition.plan(files)
        report['plan_sha256'] = planned['plan_sha256']
        (output / 'partition-plan.json').write_text(json.dumps(planned, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        report['file_discovery_summary_sha256'] = hashlib.sha256((output / 'file-discovery/summary.json').read_bytes()).hexdigest()
        image_raw = command([ENGINE, 'image', 'inspect', discovery.IMAGE])
        image = json.loads(image_raw.stdout)[0] if not image_raw.returncode else {}
        if (discovery.IMAGE not in image.get('RepoDigests', []) or image.get('Os') != 'linux'
                or image.get('Architecture') != 'amd64' or not re.fullmatch(r'sha256:[a-f0-9]{64}', image.get('Id', ''))):
            raise ValueError('immutable image identity differs')
        report['image_id'] = image['Id']
        (output / 'image-inspect.json').write_bytes(image_raw.stdout)
        for index, batch in enumerate(planned['batches']):
            case = output / ('batch-' + str(index + 1))
            case.mkdir()
            (case / 'selectors.json').write_text(json.dumps(batch, indent=2) + '\n', encoding='utf-8')
            row = {'batch_index': index, 'selector_count': len(batch), 'status': 'not_qualified', 'cleanup_confirmed': False}
            report['batches'].append(row)
            with tempfile.TemporaryDirectory(prefix='p07-sympy-node-') as tmp:
                home, cid = Path(tmp), None
                cidfile = home / 'cid'
                try:
                    created = command([ENGINE, 'create', '--cidfile', str(cidfile), '--init', '--pull=never', '--network=none',
                        '--read-only', '--user=65534:65534', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                        '--memory=1g', '--cpus=2', '--pids-limit=128', '--tmpfs=/tmp:rw,noexec,nosuid,size=134217728,mode=1777',
                        '--workdir=/testbed', '--env=HOME=/tmp', '--env=TRAVIS_BUILD_NUMBER=', '--entrypoint=/bin/sleep', image['Id'], 'infinity'])
                    if created.returncode:
                        raise ValueError('worker create failed')
                    cid = cidfile.read_text(encoding='ascii').strip()
                    if not re.fullmatch(r'[a-f0-9]{64}', cid) or command([ENGINE, 'start', cid]).returncode:
                        raise ValueError('worker start failed')
                    raw = command([ENGINE, 'inspect', cid])
                    item = json.loads(raw.stdout)[0] if not raw.returncode else {}
                    discovery.verify_container(item, cid, image['Id'])
                    (case / 'container-inspect.json').write_bytes(raw.stdout)
                    public = [{k: r[k] for k in ('path', 'sha', 'size')} for r in rows] + discovery.RUNNER
                    check = _bounded([ENGINE, 'exec', '--user=65534:65534', '-w', '/testbed', cid, discovery.PYTHON, '-I', '-B',
                                      '-c', VERIFY_SOURCE, json.dumps(public, separators=(',', ':'))],
                                     home=home, timeout=90, limit=MAX_OUTPUT, check=lambda: None)
                    (case / 'source-verification.stdout').write_bytes(check.stdout.encode())
                    (case / 'source-verification.stderr').write_bytes(check.stderr.encode())
                    verified = json.loads(check.stdout) if not check.returncode and not check.timed_out and not check.stdout_truncated and not check.stderr_truncated else {}
                    if verified.get('checked') != len(public) or verified.get('differences'):
                        raise ValueError('worker source audit incomplete or differs')
                    row['source_blobs_checked'] = verified['checked']
                    result = _bounded([ENGINE, 'exec', '--user=65534:65534', '-w', '/testbed', cid, discovery.PYTHON, '-I', '-B',
                                       '-c', SOURCE, json.dumps(batch, separators=(',', ':'))],
                                      home=home, timeout=BATCH_TIMEOUT, limit=MAX_OUTPUT, check=lambda: None)
                    (case / 'stdout').write_bytes(result.stdout.encode())
                    (case / 'stderr').write_bytes(result.stderr.encode())
                    row.update(exit_code=result.returncode, timed_out=result.timed_out, stdout_truncated=result.stdout_truncated,
                               stderr_truncated=result.stderr_truncated, stdout_sha256=hashlib.sha256(result.stdout.encode()).hexdigest(),
                               stderr_sha256=hashlib.sha256(result.stderr.encode()).hexdigest())
                    if result.stdout_truncated or result.stderr_truncated:
                        row['status'] = 'output_truncated'
                    else:
                        row['events'] = summarize_events(result.stdout, batch)
                        row['status'] = 'timed_out_partial' if result.timed_out else 'process_finished_diagnostic_only'
                except Exception as exc:
                    row.update(error_type=type(exc).__name__, error=str(exc)[:500])
                finally:
                    if cid is None and cidfile.is_file():
                        cid = cidfile.read_text(encoding='ascii').strip()
                    if cid and re.fullmatch(r'[a-f0-9]{64}', cid):
                        removed = command([ENGINE, 'rm', '-f', cid])
                        absent = command([ENGINE, 'container', 'ls', '-a', '--no-trunc', '--filter', 'id=' + cid, '--format', '{{.ID}}'])
                        row.update(container_id=cid, remove_exit=removed.returncode, cleanup_confirmed=absent.returncode == 0 and not absent.stdout.strip())
                    if not row['cleanup_confirmed']:
                        row['status'] = 'cleanup_uncertain'
            (case / 'summary.json').write_text(json.dumps(row, indent=2, sort_keys=True) + '\n', encoding='utf-8')
            if row['status'] == 'cleanup_uncertain':
                break
        if (len(report['batches']) == 3 and all(r['cleanup_confirmed'] and r['status'] in
                {'timed_out_partial', 'process_finished_diagnostic_only', 'output_truncated'} for r in report['batches'])):
            report['status'] = 'bounded_batch_diagnostics_recorded'
    except Exception as exc:
        report.update(error_type=type(exc).__name__, error=str(exc)[:500])
    (output / 'summary.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    outcome = run(parser.parse_args().output)
    print(json.dumps(outcome, sort_keys=True))
    raise SystemExit(0 if outcome['status'] == 'bounded_batch_diagnostics_recorded' else 1)
