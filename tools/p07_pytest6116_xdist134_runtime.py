"""Check a pinned xdist 1.34 compatibility adapter in an isolated focused runtime.

This is a compatibility smoke, not a full suite, model result or admission.
Publisher wheels are hash and RECORD checked before isolated installation.
All target imports and public test execution occur in a bounded container.
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
from pathlib import Path

import p07_pytest6116_collection_recon as recon
import p07_pytest6116_optional_prepare as optional
import p07_pytest6116_py38_repro as py38
import p07_pytest6116_six_failure_focus as focus
import p07_pytest6116_xdist134_prepare as candidate

from atomic_flow.check_requests_execution import _bounded

ADAPTER = Path(__file__).resolve().parent / 'p07_pytest6116_boxed_adapter_v1.py'
ADAPTER_SHA = '8dc5e761ae0887528b1102dbd8c7a58e7dfedecf9d9257f1cd117b93c06440f0'
TREE_SHA = '715b9610bc004fbb13f365bca29478877499c62a59013b558b42bdeb02a5915d'
FORK_NAMES = ('test_passfunction', 'test_failfunction', 'test_skipfunction', 'test_skip_in_setup_function',
              'test_failure_in_setup_function', 'test_failure_in_teardown_function', 'test_custom_failure_repr',
              'test_teardown_final_returncode', 'test_logstart_logfinish_hooks', 'test_exact_teardown_issue90',
              'test_exact_teardown_issue1206', 'test_failure_in_setup_function_ignores_custom_repr',
              'test_systemexit_does_not_bail_out', 'test_exit_propagates', 'test_suicide')
NODES = tuple('testing/test_runner.py::TestExecutionForked::' + name for name in FORK_NAMES) + focus.NODES[2:]
SMOKE = r"""
import json,os,sys
sys.dont_write_bytecode=True
os.environ['HYPOTHESIS_STORAGE_DIRECTORY']='/tmp/p07-hypothesis'
import pytest,_pytest,xdist,xdist.boxed,pytest_forked,execnet
if sys.version_info[:3]!=(3,8,20) or os.path.realpath(pytest.__file__)!='/testbed/src/pytest.py': raise SystemExit(125)
class Recorder:
    def __init__(self): self.nodes=[];self.completed=[];self.outcomes={};self.plugins={};self.errors=[];self.finished=False;self.nonpassing=[]
    def pytest_sessionstart(self,session):
        pm=session.config.pluginmanager
        self.plugins={n:bool(pm.hasplugin(n)) for n in ('xdist','pytest_forked','xdist.looponfail','xdist.boxed')}
    def pytest_collection_finish(self,session): self.nodes=[i.nodeid for i in session.items]
    def pytest_collectreport(self,report):
        if report.failed or report.skipped:self.errors.append(report.nodeid)
    def pytest_runtest_logreport(self,report):
        k=report.when+':'+report.outcome;self.outcomes[k]=self.outcomes.get(k,0)+1
        if report.outcome!='passed' or hasattr(report,'wasxfail'):self.nonpassing.append({'nodeid':report.nodeid,'when':report.when,'outcome':report.outcome,'reason':str(report.longrepr),'wasxfail':getattr(report,'wasxfail',None)})
        if report.when=='teardown':self.completed.append(report.nodeid)
    def pytest_sessionfinish(self,session,exitstatus):self.finished=True
r=Recorder()
args=['-vv','--tb=long','--show-capture=all','-o','cache_dir=/tmp/p07-cache']+NODES_PLACEHOLDER
code=int(pytest.main(args,plugins=[r]))
print('\nP07_XDIST_SMOKE='+json.dumps({'nodes':r.nodes,'completed':r.completed,'outcomes':r.outcomes,
 'plugins':r.plugins,'errors':r.errors,'nonpassing':r.nonpassing,'finished':r.finished,'exit_code':code,
 'xdist':os.path.realpath(xdist.__file__),'boxed':os.path.realpath(xdist.boxed.__file__),
 'execnet':os.path.realpath(execnet.__file__),'forked':os.path.realpath(pytest_forked.__file__)},sort_keys=True),flush=True)
raise SystemExit(code)
""".replace('NODES_PLACEHOLDER', repr(list(NODES)))


def valid_mounts(container):
    """Allow no mounts or the already fixed writable /tmp tmpfs only."""
    host, config = container.get('HostConfig', {}), container.get('Config', {})
    if host.get('Mounts') not in (None, []) or config.get('Volumes') not in (None, {}):
        return False
    mounts = container.get('Mounts')
    if not isinstance(mounts, list):
        return False
    if not mounts:
        return True
    if len(mounts) != 1 or not isinstance(mounts[0], dict):
        return False
    mount = mounts[0]
    return (mount.get('Type') == 'tmpfs' and mount.get('Destination') == '/tmp'
            and mount.get('RW') is True and mount.get('Source') in (None, '')
            and mount.get('Name') in (None, '') and mount.get('Propagation') in (None, ''))


def verify_observation(obs, exit_code):
    """Require all 19 unchanged public nodes, outcomes and plugin identities."""
    nodes, completed = obs.get('nodes', []), obs.get('completed', [])
    if (not obs.get('finished') or obs.get('exit_code') != exit_code or len(nodes) != len(NODES)
            or set(nodes) != set(NODES) or len(set(nodes)) != len(nodes)
            or len(completed) != len(NODES) or set(completed) != set(NODES) or obs.get('errors')):
        raise ValueError('focused public node inventory incomplete')
    paths = {'xdist': '/usr/local/lib/python3.8/site-packages/xdist/__init__.py',
             'boxed': '/usr/local/lib/python3.8/site-packages/xdist/boxed.py',
             'execnet': '/usr/local/lib/python3.8/site-packages/execnet/__init__.py',
             'forked': '/usr/local/lib/python3.8/site-packages/pytest_forked/__init__.py'}
    plugins = {'xdist': True, 'pytest_forked': True, 'xdist.looponfail': True, 'xdist.boxed': False}
    outcomes = {'setup:passed': len(NODES), 'call:passed': len(NODES), 'teardown:passed': len(NODES)}
    passed = (exit_code == 0 and obs.get('plugins') == plugins and obs.get('outcomes') == outcomes
              and obs.get('nonpassing') == [] and all(obs.get(name) == value for name, value in paths.items()))
    return {'status': 'focused_smoke_passed' if passed else 'focused_smoke_failed',
            'outcomes': obs.get('outcomes'), 'plugins': obs.get('plugins'), 'nonpassing': obs.get('nonpassing'),
            'module_paths': {name: obs.get(name) for name in paths}, 'collected': len(nodes), 'completed': len(completed)}


def run(output):
    if os.name != "posix" or output.exists():
        raise ValueError("fresh output and Linux host required")
    output.mkdir(parents=True)
    report = {"status": "not_qualified", "scope": "19 public focused nodes; prospective adapter",
              "ordinary_acceptance": False, "provider_calls": 0, "hidden_oracle": "not_run",
              "adapter_sha256": ADAPTER_SHA, "candidate_profile_sha256": candidate.PROFILE_SHA256}
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/tmp", "LANG": "C", "LC_ALL": "C"}

    def command(args, timeout=120):
        return subprocess.run(args, env=env, capture_output=True, check=False, timeout=timeout)

    with tempfile.TemporaryDirectory(prefix="p07-xdist-smoke-") as temp:
        tmp = Path(temp)
        cidfile = tmp / "cid"
        cid = None
        try:
            adapter = ADAPTER.read_bytes()
            if hashlib.sha256(adapter).hexdigest() != ADAPTER_SHA:
                raise ValueError('fixed adapter bytes differ')
            wheelhouse = tmp / 'candidate'
            prep = candidate.prepare(wheelhouse)
            profile = json.loads(optional.PEXPECT_BASE.read_bytes())
            profile['wheels'].extend(json.loads(optional.PEXPECT_PROFILE.read_bytes())['wheels'])
            profile['wheels'].extend(json.loads(candidate.old.PROFILE.read_bytes())['runtime_wheels'])
            profile['wheels'].extend(json.loads(candidate.PROFILE.read_bytes())['wheels'])
            if len(profile['wheels']) != 28 or len(prep['wheels']) != 28:
                raise ValueError('candidate wheel inventory differs')
            context = tmp / 'context'
            context.mkdir()
            for item in profile['wheels']:
                name = item['filename']
                source_path = wheelhouse / name
                if not source_path.is_file():
                    source_path = wheelhouse / 'base' / name
                if hashlib.sha256(source_path.read_bytes()).hexdigest() != item['sha256']:
                    raise ValueError('prepared wheel bytes differ')
                shutil.copyfile(source_path, context / name)
            (context / 'boxed.py').write_bytes(adapter)
            (output / 'wheel-preparation.json').write_text(json.dumps(prep, indent=2, sort_keys=True) + "\n")
            image_bases = {}
            for name, image in (("python", profile["python_image"]), ("source", profile["source_image"])):
                pull = command([recon.ENGINE, "pull", image], timeout=240)
                (output / (name + "-pull.stdout")).write_bytes(pull.stdout)
                (output / (name + "-pull.stderr")).write_bytes(pull.stderr)
                if pull.returncode:
                    raise ValueError("pinned image pull failed: " + name)
                inspect = command([recon.ENGINE, "image", "inspect", image])
                item = json.loads(inspect.stdout)[0]
                if inspect.returncode or image not in item.get("RepoDigests", []) or item.get("Architecture") != "amd64":
                    raise ValueError("image identity differs")
                image_bases[name] = item
                (output / (name + "-image-inspect.json")).write_bytes(inspect.stdout)
            inject = (
                "from pathlib import Path; p=Path('/usr/local/lib/python3.8/site-packages/p07-public-source.pth'); "
                "assert not p.exists(); p.write_text(\"import sys; sys.path.insert(0, '/testbed/src')\\n\")"
            )
            dockerfile = (
                "FROM "
                + profile["source_image"]
                + " AS target\nFROM "
                + profile["python_image"]
                + "\nCOPY --from=target /testbed /testbed\nCOPY *.whl /opt/p07-wheels/\n"
                + "RUN --network=none /usr/local/bin/python -I -B -m pip --isolated install --no-index --no-deps --no-compile /opt/p07-wheels/*.whl\n"
                + "COPY boxed.py /usr/local/lib/python3.8/site-packages/xdist/boxed.py\n"
                + "RUN --network=none /usr/local/bin/python -I -B -c "
                + shlex.quote(inject)
                + "\n"
            )
            (context / "Dockerfile").write_text(dockerfile)
            (output / "Dockerfile").write_text(dockerfile)
            build = command(
                [recon.ENGINE, "build", "--pull=false", "--network=none", "--iidfile", str(tmp / "iid"), str(context)], timeout=300
            )
            (output / "build.stdout").write_bytes(build.stdout)
            (output / "build.stderr").write_bytes(build.stderr)
            if build.returncode:
                raise ValueError("runtime image build failed")
            iid = (tmp / "iid").read_text().strip()
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", iid):
                raise ValueError("image ID missing")
            image_inspect = command([recon.ENGINE, "image", "inspect", iid])
            image_info = json.loads(image_inspect.stdout)[0] if not image_inspect.returncode else {}
            base_layers = image_bases["python"].get("RootFS", {}).get("Layers", [])
            layers = image_info.get("RootFS", {}).get("Layers", [])
            if not base_layers or image_info.get("Id") != iid or layers[:len(base_layers)] != base_layers or len(layers) <= len(base_layers):
                raise ValueError("runtime image ancestry differs")
            (output / "image-inspect.json").write_bytes(image_inspect.stdout)
            created = command(
                [
                    recon.ENGINE,
                    "create",
                    "--cidfile",
                    str(cidfile),
                    "--init",
                    "--pull=never",
                    "--network=none",
                    "--read-only",
                    "--user=65534:65534",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    "--memory=1g",
                    "--cpus=2",
                    "--pids-limit=128",
                    "--tmpfs=/tmp:rw,noexec,nosuid,size=134217728,mode=1777",
                    "--workdir=/testbed",
                    "--env=HOME=/tmp",
                    "--entrypoint=/bin/sleep",
                    iid,
                    "infinity",
                ]
            )
            if created.returncode:
                raise ValueError("container create failed")
            cid = cidfile.read_text().strip()
            if not re.fullmatch(r"[0-9a-f]{64}", cid):
                raise ValueError("invalid container ID")
            detail = command([recon.ENGINE, "inspect", cid])
            c = json.loads(detail.stdout)[0]
            h = c["HostConfig"]
            if (
                c["Id"] != cid
                or c["Image"] != iid
                or c["State"]["Running"] is not False
                or c["Config"]["User"] != "65534:65534"
                or h["NetworkMode"] != "none"
                or not h["ReadonlyRootfs"]
                or not h["Init"]
                or h["Privileged"]
                or h["CapDrop"] != ["ALL"]
                or h.get("CapAdd")
                or h.get("Binds")
                or h.get("VolumesFrom")
                or h.get("Tmpfs") != {"/tmp": "rw,noexec,nosuid,size=134217728,mode=1777"}
                or not valid_mounts(c)
                or h["Memory"] != 1024**3
                or h["NanoCpus"] != 2_000_000_000
                or h["PidsLimit"] != 128
                or "no-new-privileges" not in h.get("SecurityOpt", [])
            ):
                raise ValueError("container controls differ")
            (output / "container-inspect.json").write_bytes(detail.stdout)
            # Remove only a verified copy of our disclosed overlay from the
            # host-side extraction, so the generic publisher inventory still
            # rejects every other extra file. The container is never mutated.
            adapter_copy = output / 'installed-boxed.py'
            seen_adapter = []
            def audited_command(args, timeout=120):
                result = command(args, timeout=timeout)
                expected_source = cid + ':/usr/local/lib/python3.8/site-packages/xdist'
                if len(args) == 4 and args[:2] == [recon.ENGINE, 'cp'] and args[2] == expected_source:
                    overlay = Path(args[3]) / 'boxed.py'
                    if result.returncode or overlay.is_symlink() or not overlay.is_file() or overlay.read_bytes() != adapter:
                        raise ValueError('installed adapter differs or is unsafe')
                    adapter_copy.write_bytes(overlay.read_bytes())
                    overlay.unlink()
                    seen_adapter.append(1)
                return result
            verified, inventory, extras, wrappers = py38.audit_relocated_wheels(audited_command, cid, context, profile, tmp / "installed")
            if len(seen_adapter) != 1 or not adapter_copy.is_file():
                raise ValueError('installed adapter audit missing')
            (output / "installed-inventory.json").write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n")
            (output / "generated-scripts.json").write_text(json.dumps(wrappers, indent=2, sort_keys=True) + "\n")
            report.update(
                image_id=iid,
                installed_publisher_files=verified["publisher_files_verified"],
                pip_metadata_extras=extras,
                generated_script_contents_verified=False,
            )
            if command([recon.ENGINE, "start", cid]).returncode:
                raise ValueError("container start failed")
            tree_raw = recon.PUBLIC_TREE.read_bytes()
            if hashlib.sha256(tree_raw).hexdigest() != TREE_SHA:
                raise ValueError('fixed public tree manifest digest differs')
            tree = json.loads(tree_raw)
            report['public_tree_sha256'] = TREE_SHA
            if tree.get("base_commit") != recon.BASE or len(tree.get("blobs", [])) != 474:
                raise ValueError("public tree identity differs")
            check = command(
                [
                    recon.ENGINE,
                    "exec",
                    "--user=65534:65534",
                    cid,
                    py38.PYTHON,
                    "-I",
                    "-B",
                    "-c",
                    recon.VERIFY_SOURCE,
                    json.dumps(tree["blobs"], separators=(",", ":")),
                ],
                timeout=90,
            )
            (output / "source-verification.stdout").write_bytes(check.stdout)
            if check.returncode or json.loads(check.stdout) != {"checked": 474, "differences": []}:
                raise ValueError("public source differs")
            scheme = command(
                [recon.ENGINE, "exec", "--user=65534:65534", "-w", "/tmp", cid, py38.PYTHON, "-I", "-B", "-c", py38.SCHEME_PROBE]
            )
            if scheme.returncode or scheme.stderr or json.loads(scheme.stdout) != py38.FIXED_SCHEME:
                raise ValueError("Python or pip scheme differs")
            (output / "scheme.stdout").write_bytes(scheme.stdout)
            result = _bounded(
                [recon.ENGINE, "exec", "--user=65534:65534", "-w", "/testbed", cid, py38.PYTHON, "-I", "-B", "-c", SMOKE],
                home=tmp,
                timeout=180,
                limit=1_000_000,
                check=lambda: None,
            )
            (output / "smoke.stdout").write_bytes(result.stdout.encode())
            (output / "smoke.stderr").write_bytes(result.stderr.encode())
            report.update(
                exit_code=result.returncode,
                timed_out=result.timed_out,
                stdout_truncated=result.stdout_truncated,
                stderr_truncated=result.stderr_truncated,
                stdout_sha256=hashlib.sha256(result.stdout.encode()).hexdigest(),
                stderr_sha256=hashlib.sha256(result.stderr.encode()).hexdigest(),
            )
            if result.timed_out or result.stdout_truncated or result.stderr_truncated:
                raise ValueError("smoke output incomplete")
            markers = [line.removeprefix("P07_XDIST_SMOKE=") for line in result.stdout.splitlines() if line.startswith("P07_XDIST_SMOKE=")]
            if len(markers) != 1:
                raise ValueError("smoke marker missing")
            obs = json.loads(markers[0])
            (output / "observation.json").write_text(json.dumps(obs, indent=2, sort_keys=True) + "\n")
            report.update(verify_observation(obs, result.returncode))
        except Exception as exc:
            report.update(error_type=type(exc).__name__, error=str(exc)[:500])
        finally:
            if cid is None and cidfile.exists():
                cid = cidfile.read_text().strip()
            if cid and re.fullmatch(r"[0-9a-f]{64}", cid):
                removed = command([recon.ENGINE, "rm", "-f", cid])
                absent = command([recon.ENGINE, "container", "ls", "-a", "--no-trunc", "--filter", "id=" + cid, "--format", "{{.ID}}"])
                report["cleanup_confirmed"] = not removed.returncode and not absent.returncode and not absent.stdout.strip()
                if not report["cleanup_confirmed"]:
                    report["status"] = "cleanup_uncertain"
    (output / "summary.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    result = run(parser.parse_args().output)
    raise SystemExit(0 if result["status"] == "focused_smoke_passed" else 1)
