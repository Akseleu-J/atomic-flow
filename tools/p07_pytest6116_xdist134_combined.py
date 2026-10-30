"""Run the full public suite in a combined, separately audited candidate image.

This diagnostic binds separately pinned xdist 1.34, forked, Twisted, NumPy,
optional wheels, a disclosed boxed adapter, and Debian lsof. It does not admit skips, qualify TaskRunner, or grade a model.
Only target code inside the restricted Docker container may be executed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

import p07_pytest6116_collection_recon as recon
import p07_pytest6116_lsof_probe as lsof
import p07_pytest6116_numpy_prepare as numpy_audit
import p07_pytest6116_numpy_smoke as numpy_smoke
import p07_pytest6116_optional_prepare as optional
import p07_pytest6116_py38_repro as py38
import p07_pytest6116_trial_prepare as trial
import p07_pytest6116_trial_smoke as trial_smoke
import p07_pytest6116_wheel_install_inventory as relocated
import p07_pytest6116_wrapper_verify as wrapper_verify
import p07_pytest6116_xdist134_prepare as candidate
import p07_pytest6116_xdist134_runtime as adapter_runtime

from atomic_flow.check_requests_execution import _bounded


def verify_fixed_wrappers(context, profile, installed_root):
    """Verify five fixed entrypoints using bytes from safely extracted roots."""
    plans = [relocated.publisher_wheel_plan((context / row['filename']).read_bytes(), row)
             for row in profile['wheels']]
    all_generated = relocated.merge_plans(plans)['generated_console_scripts']
    generated = {name: all_generated[name] for name in wrapper_verify.ENTRY_POINTS}
    wrapper_raw = {}
    for name in wrapper_verify.ENTRY_POINTS:
        item = installed_root / name.lstrip('/')
        if item.is_symlink() or not item.is_file() or item.stat().st_size > 65536:
            raise ValueError('unsafe generated wrapper')
        wrapper_raw[name] = item.read_bytes()
    return wrapper_verify.verify_wrapper_bytes(generated, wrapper_raw)


NODE_MANIFEST = Path(__file__).resolve().parents[1] / 'benchmarks/stage4/p07-pytest6116-combined-nodes-v1.json'
NODE_SHA = '577ac6aeebe1b4c7bbf5afc217424e680b1aebd91deec0d3646c99b437f995e2'


def load_baseline(raw):
    if hashlib.sha256(raw).hexdigest() != NODE_SHA:
        raise ValueError('prior public node inventory digest differs')
    doc = json.loads(raw)
    nodes = doc.get('nodes', [])
    if (doc.get('schema_version') != 1 or doc.get('source_ci') != 40993867055
            or doc.get('artifact_sha256') != 'eb637e827ffb41a2ba603b38cd7ff5d37678c2fce7878f1d5111dd2e3ff516f1'
            or len(nodes) != 2617 or len(set(nodes)) != 2617):
        raise ValueError('prior public node inventory differs')
    return nodes


def combined_source():
    """Full public selection, exact phase records, full nonpassing repr."""
    code = py38.target_source(True)
    before = "    def pytest_collection_finish(self,session):"
    replacement = ("    def pytest_sessionstart(self,session):\n"
                   "        pm=session.config.pluginmanager\n"
                   "        self.plugins={n:bool(pm.hasplugin(n)) for n in ('xdist','pytest_forked','xdist.boxed','xdist.looponfail')}\n"
                   + before)
    declaration = 'self.collection_skips=[]; self.nonpassing=[]'
    hook = "        k=report.when+':'+report.outcome; self.outcomes[k]=self.outcomes.get(k,0)+1"
    reason = "'reason':str(report.longrepr)[:500]"
    marker = "'collection_skips':r.collection_skips,'nonpassing':r.nonpassing"
    if any(code.count(x) != 1 for x in (before, declaration, hook, reason, marker)):
        raise ValueError('fixed full diagnostic source differs')
    code = code.replace(before, replacement).replace(declaration, declaration + '; self.phases=[]; self.xpasses=[]')
    code = code.replace(hook, hook + "\n        self.phases.append({'nodeid':report.nodeid,'phase':report.when,'outcome':report.outcome,'wasxfail':getattr(report,'wasxfail',None)})"
                        + "\n        if report.outcome=='passed' and hasattr(report,'wasxfail'):\n            self.xpasses.append({'nodeid':report.nodeid,'phase':report.when,'reason':str(report.wasxfail)})")
    return code.replace(reason, "'reason':str(report.longrepr)").replace(marker,
        "'collection_skips':r.collection_skips,'nonpassing':r.nonpassing,'plugins':r.plugins,'xpasses':r.xpasses,'phase_reports':r.phases")


def assess_observation(obs, baseline, exit_code, public_paths):
    nodes, completed = obs.get('items', []), obs.get('completed', [])
    if (not obs.get('finished') or obs.get('exit_code') != exit_code or len(nodes) != len(set(nodes))
            or len(completed) != len(set(completed)) or set(completed) != set(nodes)
            or {node.split('::', 1)[0] for node in nodes} - public_paths):
        raise ValueError('public test inventory incomplete or invalid')
    phases = obs.get('phase_reports', [])
    keys = [(p.get('nodeid'), p.get('phase')) for p in phases]
    if (len(keys) != len(set(keys)) or any(n not in set(nodes) or phase not in ('setup','call','teardown') for n,phase in keys)
            or any((n,'setup') not in keys or (n,'teardown') not in keys for n in nodes)
            or any(p.get('outcome') not in ('passed','failed','skipped') for p in phases)):
        raise ValueError('phase-level public test inventory incomplete')
    by_key = {(p['nodeid'],p['phase']):p for p in phases}
    counts = {}
    for phase in phases:
        key = phase['phase'] + ':' + phase['outcome']
        counts[key] = counts.get(key, 0) + 1
    nonpassing = obs.get('nonpassing', [])
    xpasses = obs.get('xpasses', [])
    if (counts != obs.get('outcomes') or not isinstance(nonpassing, list) or not isinstance(xpasses, list)
            or {(x['nodeid'],x['phase']) for x in nonpassing} != {key for key,p in by_key.items() if p['outcome'] != 'passed'}
            or len(nonpassing) != sum(p['outcome'] != 'passed' for p in phases)
            or any((x.get('outcome'), x.get('wasxfail')) != (by_key[(x['nodeid'],x['phase'])]['outcome'],
                        by_key[(x['nodeid'],x['phase'])].get('wasxfail')) for x in nonpassing)
            or {(x['nodeid'],x['phase']) for x in xpasses} != {key for key,p in by_key.items() if p['outcome'] == 'passed' and p.get('wasxfail') is not None}
            or len(xpasses) != sum(p['outcome'] == 'passed' and p.get('wasxfail') is not None for p in phases)):
        raise ValueError('phase outcome or xfail evidence differs')
    inconsistent = [n for n in nodes if (by_key[(n,'setup')]['outcome'] == 'passed') != ((n,'call') in by_key)]
    if inconsistent:
        raise ValueError('phase call/setup causality differs')
    expected_plugins = {'xdist':True,'pytest_forked':True,'xdist.boxed':False,'xdist.looponfail':True}
    missing, unexpected = sorted(set(baseline)-set(nodes)), sorted(set(nodes)-set(baseline))
    return {'status': 'combined_public_diagnostic_observed' if not missing and not unexpected and not obs.get('collection_errors')
            and not obs.get('collection_skips') and obs.get('plugins') == expected_plugins else 'combined_public_diagnostic_incomplete',
            'collected':len(nodes), 'completed':len(completed), 'outcomes':obs['outcomes'], 'plugins':obs.get('plugins'),
            'collection_errors':obs.get('collection_errors'), 'collection_skips':obs.get('collection_skips'),
            'nonpassing':obs.get('nonpassing'), 'xpasses':obs.get('xpasses'), 'missing_baseline_nodes':missing,
            'unexpected_nodes':unexpected, 'phase_reports':len(phases), 'exit_code':exit_code}


def run(output):
    if os.name != 'posix' or output.exists():
        raise ValueError('fresh output and Linux host required')
    output.mkdir(parents=True)
    report = {'status': 'not_qualified', 'scope': 'full combined public diagnostic only',
              'ordinary_acceptance': False, 'provider_calls': 0, 'hidden_oracle': 'not_run',
              'lsof_publisher_signature_verified': False, 'numpy_generated_wrappers_content_verified': False,
              'candidate_profile_sha256': candidate.PROFILE_SHA256, 'trial_profile_sha256': trial.PROFILE_SHA256,
              'historical_runtime_identity_verified': False, 'generated_wrapper_contents_all_verified': False,
              'adapter_sha256': adapter_runtime.ADAPTER_SHA, 'trial_generated_wrappers_content_verified': False}
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/tmp', 'LANG': 'C', 'LC_ALL': 'C'}

    def command(args, timeout=120):
        return subprocess.run(args, env=env, capture_output=True, check=False, timeout=timeout)

    with tempfile.TemporaryDirectory(prefix='p07-combined-public-') as temporary:
        tmp = Path(temporary)
        cidfile, cid = tmp / 'cid', None
        try:
            baseline = load_baseline(NODE_MANIFEST.read_bytes())
            report['baseline_nodes_sha256'] = NODE_SHA
            tree_raw = recon.PUBLIC_TREE.read_bytes()
            if hashlib.sha256(tree_raw).hexdigest() != adapter_runtime.TREE_SHA:
                raise ValueError('fixed public tree digest differs')
            report['public_tree_sha256'] = adapter_runtime.TREE_SHA
            adapter = adapter_runtime.ADAPTER.read_bytes()
            if hashlib.sha256(adapter).hexdigest() != adapter_runtime.ADAPTER_SHA:
                raise ValueError('fixed boxed adapter digest differs')
            cand_dir = tmp / 'candidate'
            trial_dir = tmp / 'trial'
            cand_receipt = candidate.prepare(cand_dir)
            trial_receipt = trial.prepare(trial_dir)
            closure = trial.closure(cand_receipt['wheels'] + trial_receipt['wheels_added'])
            (output / 'candidate-preparation.json').write_bytes((cand_dir / 'preparation.json').read_bytes())
            (output / 'trial-preparation.json').write_bytes((trial_dir / 'preparation.json').read_bytes())
            (output / 'combined-closure.json').write_text(json.dumps(closure, indent=2, sort_keys=True) + '\n')
            numpy_dir = tmp / 'numpy'
            numpy_receipt = numpy_audit.prepare(numpy_dir)
            (output / 'numpy-preparation.json').write_bytes((numpy_dir / 'preparation.json').read_bytes())
            profile = json.loads(optional.PEXPECT_BASE.read_bytes())
            profile['wheels'].extend(json.loads(optional.PEXPECT_PROFILE.read_bytes())['wheels'])
            profile['wheels'].extend(json.loads(candidate.old.PROFILE.read_bytes())['runtime_wheels'])
            profile['wheels'].extend(json.loads(candidate.PROFILE.read_bytes())['wheels'])
            profile['wheels'].extend(json.loads(trial.PROFILE.read_bytes())['wheels'])
            if (len(profile['wheels']) != 35 or len(cand_receipt['wheels']) != 28 or len(trial_receipt['wheels_added']) != 7
                    or profile['source_image'] != recon.IMAGE or profile['python_image'] != lsof.BASE):
                raise ValueError('fixed combined inputs differ')
            context = tmp / 'context'
            context.mkdir()
            for item in profile['wheels']:
                choices = [cand_dir / item['filename'], cand_dir / 'base' / item['filename'], trial_dir / item['filename']]
                found = [path for path in choices if path.is_file()]
                if len(found) != 1 or hashlib.sha256(found[0].read_bytes()).hexdigest() != item['sha256']:
                    raise ValueError('fixed candidate wheel missing, duplicate or differs')
                shutil.copyfile(found[0], context / item['filename'])
            (context / 'boxed.py').write_bytes(adapter)
            shutil.copyfile(numpy_dir / numpy_audit.FILENAME, context / numpy_audit.FILENAME)
            with urllib.request.urlopen(lsof.PACKAGE, timeout=60) as response:
                package = response.read(1_000_001)
            if len(package) != 318268 or lsof.digest(package) != lsof.PACKAGE_SHA256:
                raise ValueError('lsof package digest differs')
            (context / 'lsof.deb').write_bytes(package)
            payload = command(['dpkg-deb', '--fsys-tarfile', str(context / 'lsof.deb')])
            if payload.returncode or len(payload.stdout) > 8_000_000:
                raise ValueError('lsof payload unavailable')
            binary = lsof.publisher_binary(payload.stdout)
            bases = {}
            for name, image in (('python', profile['python_image']), ('source', profile['source_image'])):
                pulled = command([recon.ENGINE, 'pull', image], timeout=240)
                (output / (name + '-pull.stdout')).write_bytes(pulled.stdout)
                (output / (name + '-pull.stderr')).write_bytes(pulled.stderr)
                if pulled.returncode:
                    raise ValueError('pinned image pull failed: ' + name)
                inspected = command([recon.ENGINE, 'image', 'inspect', image])
                item = json.loads(inspected.stdout)[0] if not inspected.returncode else {}
                if image not in item.get('RepoDigests', []) or item.get('Architecture') != 'amd64':
                    raise ValueError('pinned image identity differs')
                bases[name] = item
                (output / (name + '-inspect.json')).write_bytes(inspected.stdout)
            scheme = ("import pip,sys,sysconfig; p=sysconfig.get_paths(); "
                      "x={'python':sys.executable,'version':list(sys.version_info[:3]),'pip':pip.__version__,"
                      "'data':p['data'],'scripts':p['scripts'],'purelib':p['purelib'],'platlib':p['platlib']}; "
                      'assert x==' + repr(py38.FIXED_SCHEME))
            inject = ("from pathlib import Path; p=Path('/usr/local/lib/python3.8/site-packages/p07-public-source.pth'); "
                      "assert not p.exists(); p.write_text(\"import sys; sys.path.insert(0, '/testbed/src')\\n\")")
            dockerfile = ('FROM ' + profile['source_image'] + ' AS target\nFROM ' + profile['python_image']
                + '\nCOPY --from=target /testbed /testbed\nCOPY *.whl /opt/p07-wheels/\nCOPY lsof.deb /tmp/lsof.deb\n'
                + 'RUN --network=none dpkg -i /tmp/lsof.deb\n'
                + 'RUN --network=none ' + py38.PYTHON + ' -I -B -c ' + shlex.quote(scheme) + '\n'
                + 'RUN --network=none ' + py38.PYTHON + ' -I -B -m pip --isolated install '
                  '--no-index --no-deps --no-compile --force-reinstall /opt/p07-wheels/*.whl\n'
                + 'COPY boxed.py /usr/local/lib/python3.8/site-packages/xdist/boxed.py\n'
                + 'RUN --network=none ' + py38.PYTHON + ' -I -B -c ' + shlex.quote(inject) + '\n')
            (context / 'Dockerfile').write_text(dockerfile)
            (output / 'Dockerfile').write_text(dockerfile)
            build = command([recon.ENGINE, 'build', '--pull=false', '--network=none', '--iidfile', str(tmp / 'iid'), str(context)], timeout=300)
            (output / 'build.stdout').write_bytes(build.stdout)
            (output / 'build.stderr').write_bytes(build.stderr)
            if build.returncode:
                raise ValueError('combined image build failed')
            iid = (tmp / 'iid').read_text().strip()
            if not re.fullmatch(r'sha256:[a-f0-9]{64}', iid):
                raise ValueError('image ID missing')
            img = command([recon.ENGINE, 'image', 'inspect', iid])
            info = json.loads(img.stdout)[0] if not img.returncode else {}
            base_layers = bases['python'].get('RootFS', {}).get('Layers', [])
            layers = info.get('RootFS', {}).get('Layers', [])
            if not base_layers or info.get('Id') != iid or layers[:len(base_layers)] != base_layers or len(layers) <= len(base_layers):
                raise ValueError('combined image ancestry differs')
            (output / 'image-inspect.json').write_bytes(img.stdout)
            create = command([recon.ENGINE, 'create', '--cidfile', str(cidfile), '--init', '--pull=never',
                '--network=none', '--read-only', '--user=65534:65534', '--cap-drop=ALL',
                '--security-opt=no-new-privileges', '--memory=1g', '--cpus=2', '--pids-limit=128',
                '--tmpfs=/tmp:rw,noexec,nosuid,size=134217728,mode=1777', '--workdir=/testbed',
                '--env=HOME=/tmp', '--entrypoint=/bin/sleep', iid, 'infinity'])
            if create.returncode:
                raise ValueError('container creation failed')
            cid = cidfile.read_text().strip()
            if not re.fullmatch(r'[0-9a-f]{64}', cid):
                raise ValueError('container ID missing')
            inspected = command([recon.ENGINE, 'inspect', cid])
            item = json.loads(inspected.stdout)[0]
            host = item['HostConfig']
            if (item['Id'] != cid or item['Image'] != iid or item['State']['Running'] is not False
                    or item['Config']['User'] != '65534:65534' or host['NetworkMode'] != 'none'
                    or host['ReadonlyRootfs'] is not True or host['Init'] is not True or host['Privileged']
                    or host['CapDrop'] != ['ALL'] or host.get('CapAdd') or host.get('Binds')
                    or host.get('VolumesFrom') or not adapter_runtime.valid_mounts(item) or host['Memory'] != 1024**3
                    or host['NanoCpus'] != 2_000_000_000 or host['PidsLimit'] != 128
                    or host.get('Tmpfs') != {'/tmp': 'rw,noexec,nosuid,size=134217728,mode=1777'}
                    or 'no-new-privileges' not in host.get('SecurityOpt', [])):
                raise ValueError('combined container controls differ')
            (output / 'container-inspect.json').write_bytes(inspected.stdout)
            adapter_copy, seen_adapter = output / 'installed-boxed.py', []
            def audited_command(args, timeout=120):
                copied = command(args, timeout=timeout)
                expected_source = cid + ':/usr/local/lib/python3.8/site-packages/xdist'
                if len(args) == 4 and args[:2] == [recon.ENGINE, 'cp'] and args[2] == expected_source:
                    overlay = Path(args[3]) / 'boxed.py'
                    if copied.returncode or overlay.is_symlink() or not overlay.is_file() or overlay.read_bytes() != adapter:
                        raise ValueError('installed adapter differs or is unsafe')
                    adapter_copy.write_bytes(overlay.read_bytes())
                    overlay.unlink()
                    seen_adapter.append(1)
                return copied
            verified, installed, extras, wrappers = py38.audit_relocated_wheels(audited_command, cid, context, profile, tmp / 'installed')
            if len(seen_adapter) != 1 or verified['publisher_files_verified'] != 1973:
                raise ValueError('adapter or publisher inventory differs')
            (output / 'installed-inventory.json').write_text(json.dumps(installed, indent=2, sort_keys=True) + '\n')
            (output / 'generated-wrappers.json').write_text(json.dumps(wrappers, indent=2, sort_keys=True) + '\n')
            wrapper_receipt = verify_fixed_wrappers(context, profile, tmp / 'installed')
            (output / 'verified-wrappers.json').write_text(json.dumps(wrapper_receipt, indent=2, sort_keys=True) + '\n')
            wrapper_dir = output / 'wrapper-bytes'
            wrapper_dir.mkdir()
            for name in wrappers:
                src = tmp / 'installed' / name.lstrip('/')
                if src.is_symlink() or not src.is_file() or src.stat().st_size > 65536:
                    raise ValueError('generated wrapper unavailable')
                (wrapper_dir / Path(name).name).write_bytes(src.read_bytes())
            report['fixed_wrapper_bytes_verified'] = wrapper_receipt['wrappers_verified']
            report['pip_installed_source_verified'] = False
            numpy_root = tmp / 'numpy-installed'
            numpy_root.mkdir()
            for name in numpy_smoke.ROOTS:
                copied = command([recon.ENGINE, 'cp', cid + ':' + numpy_smoke.SITE + '/' + name, str(numpy_root / name)])
                if copied.returncode:
                    raise ValueError('NumPy installed root unavailable: ' + name)
            numpy_actual = numpy_smoke.installed_hashes(numpy_root)
            numpy_extras = numpy_smoke.compare(numpy_receipt['publisher_files'], numpy_actual)
            (output / 'numpy-inventory.json').write_text(json.dumps(numpy_actual, indent=2, sort_keys=True) + '\n')
            wrapper_results = {}
            for name in numpy_smoke.WRAPPERS:
                dst = tmp / ('wrapper-' + name)
                copied = command([recon.ENGINE, 'cp', cid + ':/usr/local/bin/' + name, str(dst)])
                if copied.returncode or dst.is_symlink() or not dst.is_file() or dst.stat().st_size > 65536:
                    raise ValueError('NumPy generated wrapper missing or unsafe')
                wrapper_results[name] = {'sha256': hashlib.sha256(dst.read_bytes()).hexdigest(), 'content_verified': False}
                (wrapper_dir / ('numpy-' + name)).write_bytes(dst.read_bytes())
            (output / 'numpy-wrappers.json').write_text(json.dumps(wrapper_results, indent=2, sort_keys=True) + '\n')
            copied_lsof = tmp / 'installed-lsof'
            copied = command([recon.ENGINE, 'cp', cid + ':/usr/bin/lsof', str(copied_lsof)])
            if (copied.returncode or copied_lsof.is_symlink() or not copied_lsof.is_file()
                    or copied_lsof.stat().st_size != len(binary) or copied_lsof.read_bytes() != binary):
                raise ValueError('installed lsof binary differs')
            fixed_pth = tmp / 'public-source.pth'
            copied = command([recon.ENGINE, 'cp', cid + ':/usr/local/lib/python3.8/site-packages/p07-public-source.pth', str(fixed_pth)])
            if (copied.returncode or fixed_pth.is_symlink() or not fixed_pth.is_file() or fixed_pth.stat().st_size > 1024
                    or fixed_pth.read_bytes() != b"import sys; sys.path.insert(0, '/testbed/src')\n"):
                raise ValueError('source hook differs')
            report.update(image_id=iid, installed_publisher_files=verified['publisher_files_verified'],
                          numpy_publisher_files=len(numpy_receipt['publisher_files']), pip_metadata_extras=extras,
                          numpy_metadata_extras=numpy_extras, lsof_package_sha256=lsof.PACKAGE_SHA256,
                          lsof_binary_sha256=hashlib.sha256(binary).hexdigest(), numpy_wheel_sha256=numpy_audit.WHEEL_SHA256,
                          optional_profile_sha256=optional.PEXPECT_PROFILE_SHA256,
                          optional_base_profile_sha256=optional.PEXPECT_BASE_SHA256)
            if command([recon.ENGINE, 'start', cid]).returncode:
                raise ValueError('container start failed')
            tree = json.loads(tree_raw)
            if tree.get('repo') != 'pytest-dev/pytest' or tree.get('base_commit') != recon.BASE or len(tree.get('blobs', [])) != 474:
                raise ValueError('public tree identity differs')
            verify = command([recon.ENGINE, 'exec', '--user=65534:65534', cid, py38.PYTHON, '-I', '-B', '-c',
                              recon.VERIFY_SOURCE, json.dumps(tree['blobs'], separators=(',', ':'))], timeout=90)
            (output / 'source-verification.stdout').write_bytes(verify.stdout)
            (output / 'source-verification.stderr').write_bytes(verify.stderr)
            if verify.returncode or json.loads(verify.stdout) != {'checked': 474, 'differences': []}:
                raise ValueError('public source bytes differ')
            module_probe = ("import json,os,pytest,_pytest,xdist,xdist.boxed,pytest_forked,execnet; "
                            "print(json.dumps({k:os.path.realpath(v.__file__) for k,v in "
                            "(('pytest',pytest),('_pytest',_pytest),('xdist',xdist),('boxed',xdist.boxed),('forked',pytest_forked),('execnet',execnet))},sort_keys=True))")
            modules = command([recon.ENGINE, 'exec', '--user=65534:65534', '-w', '/tmp', cid, py38.PYTHON,
                               '-I', '-B', '-c', module_probe])
            (output / 'module-paths.stdout').write_bytes(modules.stdout)
            (output / 'module-paths.stderr').write_bytes(modules.stderr)
            expected_paths = {'pytest': '/testbed/src/pytest.py', '_pytest': '/testbed/src/_pytest/__init__.py',
                              'xdist': numpy_smoke.SITE + '/xdist/__init__.py', 'boxed': numpy_smoke.SITE + '/xdist/boxed.py',
                              'execnet': numpy_smoke.SITE + '/execnet/__init__.py', 'forked': numpy_smoke.SITE + '/pytest_forked/__init__.py'}
            if modules.returncode or modules.stderr or json.loads(modules.stdout) != expected_paths:
                raise ValueError('public pytest or xdist/execnet import paths differ')
            report['module_paths'] = expected_paths
            for name, probe in (('scheme', py38.SCHEME_PROBE), ('numpy-native', numpy_smoke.NATIVE_PROBE),
                                ('markupsafe-native', py38.MARKUPSAFE_PROBE), ('trial-native', trial_smoke.PROBE)):
                observed = _bounded([recon.ENGINE, 'exec', '--user=65534:65534', '-w', '/tmp', cid, py38.PYTHON,
                                    '-I', '-B', '-c', probe], home=tmp, timeout=60, limit=100000, check=lambda: None)
                (output / (name + '.stdout')).write_bytes(observed.stdout.encode())
                (output / (name + '.stderr')).write_bytes(observed.stderr.encode())
                if observed.returncode or observed.timed_out or observed.stdout_truncated or observed.stderr_truncated or observed.stderr:
                    raise ValueError(name + ' preflight failed')
                data = json.loads(observed.stdout)
                if name == 'scheme' and data != py38.FIXED_SCHEME:
                    raise ValueError('Python and pip scheme differs')
                if name == 'numpy-native' and (data.get('version') != '1.21.6' or data.get('matmul_ok') is not True):
                    raise ValueError('NumPy native probe differs')
                if name == 'markupsafe-native' and data.get('escape_ok') is not True:
                    raise ValueError('MarkupSafe native probe differs')
                if name == 'trial-native' and (data.get('twisted') != '21.2.0' or data.get('setuptools') != '57.5.0'
                        or data.get('interface_ok') is not True or data.get('native') not in installed or data.get('trial') not in installed):
                    raise ValueError('trial/native probe differs')
                report[name] = data
            result = _bounded([recon.ENGINE, 'exec', '--user=65534:65534', '-w', '/testbed', cid, py38.PYTHON,
                               '-I', '-B', '-c', combined_source()], home=tmp, timeout=1200, limit=24_000_000, check=lambda: None)
            (output / 'stdout').write_bytes(result.stdout.encode())
            (output / 'stderr').write_bytes(result.stderr.encode())
            report.update(exit_code=result.returncode, timed_out=result.timed_out, stdout_truncated=result.stdout_truncated,
                          stderr_truncated=result.stderr_truncated, stdout_sha256=hashlib.sha256(result.stdout.encode()).hexdigest(),
                          stderr_sha256=hashlib.sha256(result.stderr.encode()).hexdigest(), source_blobs_verified=474)
            if result.timed_out or result.stdout_truncated or result.stderr_truncated:
                raise ValueError('combined public result incomplete')
            markers = [line.removeprefix('P07_PY38_REPRO=') for line in result.stdout.splitlines() if line.startswith('P07_PY38_REPRO=')]
            if len(markers) != 1:
                raise ValueError('one complete public marker required')
            obs = json.loads(markers[0])
            (output / 'observation.json').write_text(json.dumps(obs, indent=2, sort_keys=True) + '\n')
            tree = json.loads(tree_raw)
            report.update(assess_observation(obs, baseline, result.returncode, {row['path'] for row in tree['blobs']}))
        except Exception as exc:
            report.update(error_type=type(exc).__name__, error=str(exc)[:500])
        finally:
            if cid is None and cidfile.exists():
                cid = cidfile.read_text().strip()
            if cid and re.fullmatch(r'[0-9a-f]{64}', cid):
                removed = command([recon.ENGINE, 'rm', '-f', cid])
                absent = command([recon.ENGINE, 'container', 'ls', '-a', '--no-trunc', '--filter', 'id=' + cid, '--format', '{{.ID}}'])
                report['cleanup_confirmed'] = not removed.returncode and not absent.returncode and not absent.stdout.strip()
                if not report['cleanup_confirmed']:
                    report['status'] = 'cleanup_uncertain'
    (output / 'summary.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    result = run(parser.parse_args().output)
    raise SystemExit(0 if result['status'] == 'combined_public_diagnostic_observed' and result['exit_code'] == 0 else 1)
