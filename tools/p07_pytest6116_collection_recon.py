"""Bounded public pytest6116 collection reconnaissance, not execution acceptance.

Runs the checkout's native collection and public conftest code only inside the
immutable, network-disabled Docker image. No hidden patches, model or provider.
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

from atomic_flow.check_requests_execution import _bounded

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "swebench/sweb.eval.x86_64.pytest-dev_1776_pytest-6116@sha256:5fb21e0b90b8df4b1da5876db062a0be7f601d12bce36ac6ae5c8a72b0005be9"
BASE = "e670ff76cbad80108bde9bab616b66771b8653cf"
PYTHON = "/opt/miniconda3/envs/testbed/bin/python"
ENGINE = "/usr/bin/docker"
PUBLIC_TREE = ROOT / "benchmarks/stage4/p07-pytest6116-public-tree-v1.json"
VERIFY_SOURCE = r'''
import hashlib,json,os,sys
rows=json.loads(sys.argv[1])
result=[]
for row in rows:
    relative=row['path']
    path=os.path.join('/testbed',relative)
    if os.path.realpath(path)!=path or not os.path.isfile(path):
        result.append({'path':relative,'reason':'missing_or_linked'})
        continue
    with open(path,'rb') as stream:raw=stream.read(4*1024*1024+1)
    actual=hashlib.sha1(('blob '+str(len(raw))+'\0').encode('ascii')+raw).hexdigest()
    if len(raw)!=row['size'] or actual!=row['sha']:
        result.append({'path':relative,'reason':'content_differs','actual':actual})
print(json.dumps({'checked':len(rows),'differences':result},sort_keys=True))
'''
SOURCE = r'''
import json,os,sys
sys.dont_write_bytecode=True
sys.path.insert(0,'/testbed/src')
import pytest,_pytest
if (os.path.realpath(os.getcwd())!='/testbed' or sys.executable!='/opt/miniconda3/envs/testbed/bin/python'
        or list(sys.version_info[:3])!=[3,9,20] or os.path.realpath(pytest.__file__)!='/testbed/src/pytest.py'
        or os.path.realpath(_pytest.__file__)!='/testbed/src/_pytest/__init__.py'):
    raise SystemExit(125)
class Recorder:
    def __init__(self):self.items=[];self.collection_errors=[];self.collection_skips=[];self.finished=False
    def pytest_collection_finish(self,session):
        self.items=[{'nodeid':item.nodeid,'path':os.path.relpath(str(item.fspath),'/testbed').replace(os.sep,'/')} for item in session.items]
    def pytest_collectreport(self,report):
        if report.failed:self.collection_errors.append(report.nodeid)
        if report.skipped:self.collection_skips.append(report.nodeid)
    def pytest_sessionfinish(self,session,exitstatus):self.finished=True
recorder=Recorder()
code=int(pytest.main(['--collect-only','-q','testing'],plugins=[recorder]))
result={'runner':'public_pytest_collection_recon','python_version':list(sys.version_info[:3]),
        'pytest_version':pytest.__version__,'pytest_module':os.path.realpath(pytest.__file__),
        'items':recorder.items,'collection_errors':recorder.collection_errors,
        'collection_skips':recorder.collection_skips,'finished':recorder.finished,'exit_code':code}
print('\nP07_PUBLIC_COLLECTION='+json.dumps(result,sort_keys=True),flush=True)
raise SystemExit(code)
'''


def sha(value):
    return hashlib.sha256(value).hexdigest()


def run(output: Path):
    if output.exists() or os.name != "posix":
        raise ValueError("fresh output and Linux Docker host required")
    output.mkdir(parents=True)
    report = {"status": "not_qualified", "scope": "public native collection reconnaissance only",
              "task_id": "pytest-dev__pytest-6116", "base_commit": BASE, "image": IMAGE,
              "provider_calls": 0, "test_execution": False, "taskrunner_wired": False,
              "ordinary_acceptance": False, "hidden_oracle": "not_run"}
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C", "HOME": "/tmp"}
    with tempfile.TemporaryDirectory(prefix="p07-pytest-collection-") as temporary:
        home = Path(temporary)
        cidfile = home / "container.cid"
        cid = None

        def control(args, timeout=60):
            return subprocess.run(args, env=env, capture_output=True, timeout=timeout, check=False)

        try:
            commit = control(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
            report["tested_commit"] = commit.stdout.decode().strip()
            if commit.returncode or not re.fullmatch(r"[a-f0-9]{40}", report["tested_commit"]):
                raise ValueError("exact tested commit unavailable")
            pull = control([ENGINE, "pull", IMAGE], timeout=240)
            (output / "pull.stdout").write_bytes(pull.stdout)
            (output / "pull.stderr").write_bytes(pull.stderr)
            if pull.returncode:
                raise ValueError("pinned image pull failed")
            created = control([ENGINE, "create", "--cidfile", str(cidfile), "--init", "--pull=never", "--network=none",
                "--read-only", "--user=65534:65534", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                "--memory=1g", "--cpus=2", "--pids-limit=128", "--tmpfs=/tmp:rw,noexec,nosuid,size=134217728,mode=1777",
                "--workdir=/testbed", "--env=HOME=/tmp", "--entrypoint=/bin/sleep", IMAGE, "infinity"])
            if created.returncode:
                raise ValueError("isolated container creation failed")
            cid = cidfile.read_text(encoding="ascii").strip()
            if not re.fullmatch(r"[a-f0-9]{64}", cid) or control([ENGINE, "start", cid]).returncode:
                raise ValueError("owned container start failed")
            inspected = control([ENGINE, "inspect", cid])
            if inspected.returncode:
                raise ValueError("container inspect failed")
            item = json.loads(inspected.stdout)[0]
            host, config = item["HostConfig"], item["Config"]
            if (item["Id"] != cid or host["NetworkMode"] != "none" or not host["ReadonlyRootfs"]
                    or host["Init"] is not True or host["Privileged"] or host["CapDrop"] != ["ALL"]
                    or host.get("CapAdd") or "no-new-privileges" not in host.get("SecurityOpt", [])
                    or host["Memory"] != 1024**3 or host["NanoCpus"] != 2_000_000_000 or host["PidsLimit"] != 128
                    or config["User"] != "65534:65534" or host.get("Binds") or host.get("VolumesFrom")):
                raise ValueError("container isolation does not match fixed controls")
            image = control([ENGINE, "image", "inspect", IMAGE])
            if image.returncode or json.loads(image.stdout)[0]["Id"] != item["Image"]:
                raise ValueError("actual immutable image differs")
            head = control([ENGINE, "exec", "--user=0:0", cid, "git", "-c", "safe.directory=/testbed",
                            "-C", "/testbed", "rev-parse", "HEAD"])
            (output / "git-head.stdout").write_bytes(head.stdout)
            (output / "git-head.stderr").write_bytes(head.stderr)
            report["git_head_exit"] = head.returncode
            tree_raw = PUBLIC_TREE.read_bytes()
            tree = json.loads(tree_raw)
            if (tree.get("base_commit") != BASE or tree.get("repo") != "pytest-dev/pytest"
                    or len(tree.get("blobs", [])) != 474):
                raise ValueError("public tree inventory differs")
            verify = control([ENGINE, "exec", "--user=65534:65534", cid, PYTHON, "-I", "-B", "-c", VERIFY_SOURCE,
                              json.dumps(tree["blobs"], separators=(",", ":"))], timeout=90)
            (output / "source-verification.stdout").write_bytes(verify.stdout)
            (output / "source-verification.stderr").write_bytes(verify.stderr)
            report["public_tree_sha256"] = sha(tree_raw)
            if verify.returncode:
                raise ValueError("public source byte audit failed to run")
            observation = json.loads(verify.stdout)
            report["source_verification"] = observation
            if observation.get("checked") != 474 or observation.get("differences"):
                raise ValueError("image bytes do not match the public Git tree")
            (output / "container-inspect.json").write_bytes(inspected.stdout)
            result = _bounded([ENGINE, "exec", "--user=65534:65534", "-w", "/testbed", cid, PYTHON, "-I", "-B", "-c", SOURCE],
                              home=home, timeout=180, limit=1_000_000, check=lambda: None)
            (output / "collection.stdout").write_bytes(result.stdout.encode())
            (output / "collection.stderr").write_bytes(result.stderr.encode())
            report.update(exit_code=result.returncode, timed_out=result.timed_out,
                          stdout_truncated=result.stdout_truncated, stderr_truncated=result.stderr_truncated,
                          stdout_sha256=sha(result.stdout.encode()), stderr_sha256=sha(result.stderr.encode()))
            lines = [line.removeprefix("P07_PUBLIC_COLLECTION:") for line in result.stdout.splitlines()
                     if line.startswith("P07_PUBLIC_COLLECTION:")]
            if len(lines) != 1 or result.timed_out or result.stdout_truncated or result.stderr_truncated:
                raise ValueError("collection output incomplete")
            observation = json.loads(lines[0])
            if not observation.get("finished") or observation.get("exit_code") != result.returncode:
                raise ValueError("collection session did not finish")
            (output / "collection.json").write_text(json.dumps(observation, indent=2, sort_keys=True) + "\n")
            public_paths = {row["path"] for row in tree["blobs"]}
            collected_paths = {row["path"] for row in observation["items"]}
            report.update(status="collection_observed", collected=len(observation["items"]),
                          collected_files=len(collected_paths), nonpublic_collected_files=sorted(collected_paths - public_paths),
                          collection_errors=observation["collection_errors"], collection_skips=observation["collection_skips"])
        except Exception as error:
            report.update(error_type=type(error).__name__, error=str(error)[:500])
        finally:
            if cid is None and cidfile.is_file():
                cid = cidfile.read_text(encoding="ascii").strip()
            if cid and re.fullmatch(r"[a-f0-9]{64}", cid):
                removed = control([ENGINE, "rm", "-f", cid])
                absent = control([ENGINE, "container", "ls", "-a", "--no-trunc", "--filter", "id=" + cid, "--format", "{{.ID}}"])
                report["cleanup"] = {"container_id": cid, "remove_exit": removed.returncode,
                                     "confirmed": absent.returncode == 0 and not absent.stdout.strip()}
            else:
                report["cleanup"] = {"container_id": cid, "confirmed": False}
            if not report["cleanup"]["confirmed"]:
                report["status"] = "cleanup_uncertain"
    (output / "summary.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    raise SystemExit(0 if run(options.output)["status"] == "collection_observed" else 1)
