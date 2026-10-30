"""Bounded public pylint6506 collection reconnaissance, not execution acceptance.

Runs the checkout's native collection and public conftest code only inside the
immutable, network-disabled Docker image. No hidden patches, model or provider.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
import zlib
from pathlib import Path

from atomic_flow.check_requests_execution import _bounded

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "swebench/sweb.eval.x86_64.pylint-dev_1776_pylint-6506@sha256:f2304d2a6461bc098bc872efc4474b34b54eec72f5a87bacc0509a88ba52fb2d"
BASE = "0a4204fd7555cfedd43f43017c94d24ef48244a5"
PYTHON = "/opt/miniconda3/envs/testbed/bin/python"
ENGINE = "/usr/bin/docker"
PUBLIC_TREE = ROOT / "benchmarks/stage4/p07-pylint6506-public-tree-v1.json"
PUBLIC_TREE_SHA256 = "ff3768f61bf6d2e02d78132ec46d558fa0fdc2c6ccb7a2c2a0af41f0302398cf"
VERIFY_SOURCE = r"""
import base64,hashlib,json,os,stat,sys,zlib
rows=json.loads(zlib.decompress(base64.b64decode(sys.argv[1])))
result=[]
for row in rows:
    relative=row['path']; path=os.path.join('/testbed',relative)
    try:
        if os.path.realpath(os.path.dirname(path)) != os.path.dirname(path): raise ValueError('linked ancestor')
        mode=os.lstat(path).st_mode
        if row['mode']=='120000' and stat.S_ISLNK(mode):
            raw=os.fsencode(os.readlink(path))
        elif row['mode'] in ('100644','100755') and stat.S_ISREG(mode) and os.path.realpath(path)==path:
            if bool(mode & 0o111) != (row['mode']=='100755'): raise ValueError('executable mode differs')
            with open(path,'rb') as stream: raw=stream.read(4*1024*1024+1)
        else:
            raise ValueError('unexpected file type or linked ancestor')
        actual=hashlib.sha1(('blob '+str(len(raw))+'\0').encode('ascii')+raw).hexdigest()
        if len(raw)!=row['size'] or actual!=row['sha']:
            result.append({'path':relative,'reason':'content_differs','actual':actual})
    except (OSError,ValueError):
        result.append({'path':relative,'reason':'missing_or_unexpected_type'})
print(json.dumps({'checked':len(rows),'differences':result},sort_keys=True))
"""
SOURCE = r"""
import json,os,sys
sys.dont_write_bytecode=True
import pytest,pylint
if (os.path.realpath(os.getcwd())!='/testbed' or sys.executable!='/opt/miniconda3/envs/testbed/bin/python'
        or list(sys.version_info[:3])!=[3,9,20] or os.path.realpath(pylint.__file__)!='/testbed/pylint/__init__.py'):
    raise SystemExit(125)
class Recorder:
    def __init__(self): self.items=[]; self.collection_errors=[]; self.collection_skips=[]; self.finished=False
    def pytest_collection_finish(self,session):
        self.items=[{'nodeid':item.nodeid,'path':os.path.relpath(str(item.path),'/testbed').replace(os.sep,'/')} for item in session.items]
    def pytest_collectreport(self,report):
        if report.failed:self.collection_errors.append(report.nodeid)
        if report.skipped:self.collection_skips.append(report.nodeid)
    def pytest_sessionfinish(self,session,exitstatus):self.finished=True
r=Recorder()
code=int(pytest.main(['--collect-only','-q','--benchmark-disable','tests'],plugins=[r]))
result={'runner':'pylint_public_collection_recon','python_version':list(sys.version_info[:3]),
        'pytest_version':pytest.__version__,'pylint_module':os.path.realpath(pylint.__file__),
        'items':r.items,'collection_errors':r.collection_errors,'collection_skips':r.collection_skips,
        'completed':0,'outcomes':{},'finished':r.finished,'exit_code':code}
print('\nP07_PUBLIC_COLLECTION='+json.dumps(result,sort_keys=True),flush=True)
raise SystemExit(code)
"""


def sha(value):
    return hashlib.sha256(value).hexdigest()


def parse_collection(stdout: str, exit_code: int):
    marker = "P07_PUBLIC_COLLECTION="
    lines = [line.removeprefix(marker) for line in stdout.splitlines() if line.startswith(marker)]
    if len(lines) != 1:
        raise ValueError("one complete collection marker required")
    observation = json.loads(lines[0])
    if (type(observation) is not dict or observation.get("finished") is not True
            or type(observation.get("exit_code")) is not int or observation["exit_code"] != exit_code
            or type(observation.get("items")) is not list or type(observation.get("collection_errors")) is not list
            or type(observation.get("collection_skips")) is not list):
        raise ValueError("collection session did not finish")
    return observation



def run(output: Path):
    """Observe collection in the fixed, restricted public image."""
    if output.exists() or os.name != "posix":
        raise ValueError("fresh output and Linux Docker host required")
    image = IMAGE
    output.mkdir(parents=True)
    report = {"status": "not_qualified", "scope": "public native collection reconnaissance only",
              "task_id": "pylint-dev__pylint-6506", "base_commit": BASE, "image": image,
              "provider_calls": 0, "test_execution": False, "taskrunner_wired": False,
              "ordinary_acceptance": False, "hidden_oracle": "not_run",
              "additional_source_files_audited": False, "tox_environment_equivalence_verified": False}
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C", "HOME": "/tmp"}
    with tempfile.TemporaryDirectory(prefix="p07-pylint-collection-") as temporary:
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
                "--workdir=/testbed", "--env=HOME=/tmp", "--entrypoint=/bin/sleep", image, "infinity"])
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
                    or config["User"] != "65534:65534" or host.get("Binds") or host.get("VolumesFrom")
                    or host.get("Mounts") or config.get("Volumes")
                    or host.get("Tmpfs") != {"/tmp": "rw,noexec,nosuid,size=134217728,mode=1777"}
                    or not isinstance(item.get("Mounts"), list) or len(item["Mounts"]) > 1
                    or any(m.get("Type") != "tmpfs" or m.get("Destination") != "/tmp" or m.get("Source")
                           for m in item["Mounts"])):
                raise ValueError("container isolation does not match fixed controls")
            inspected_image = control([ENGINE, "image", "inspect", image])
            image_info = json.loads(inspected_image.stdout)[0] if not inspected_image.returncode else {}
            if image_info.get("Id") != item["Image"] or IMAGE not in image_info.get("RepoDigests", []):
                raise ValueError("actual immutable image differs")
            (output / "image-inspect.json").write_bytes(inspected_image.stdout)
            head = control([ENGINE, "exec", "--user=0:0", cid, "git", "-c", "safe.directory=/testbed",
                            "-C", "/testbed", "rev-parse", "HEAD"])
            (output / "git-head.stdout").write_bytes(head.stdout)
            (output / "git-head.stderr").write_bytes(head.stderr)
            report["git_head_exit"] = head.returncode
            tree_raw = PUBLIC_TREE.read_bytes()
            if sha(tree_raw) != PUBLIC_TREE_SHA256:
                raise ValueError("public tree digest differs")
            tree = json.loads(tree_raw)
            if (tree.get("base_commit") != BASE or tree.get("repo") != "pylint-dev/pylint"
                    or len(tree.get("blobs", [])) != 2563
                    or len({x["path"] for x in tree["blobs"]}) != 2563
                    or any(x.get("mode") not in ("100644", "100755", "120000")
                           or not re.fullmatch(r"[a-f0-9]{40}", x.get("sha", ""))
                           or not isinstance(x.get("size"), int) or not 0 <= x["size"] <= 4194304
                           or x["path"].startswith("/") or any(y in ("", ".", "..") for y in x["path"].split("/"))
                           for x in tree["blobs"])):
                raise ValueError("public tree inventory differs")
            verify = control([ENGINE, "exec", "--user=65534:65534", cid, PYTHON, "-I", "-B", "-c", VERIFY_SOURCE,
                              base64.b64encode(zlib.compress(json.dumps(tree["blobs"], separators=(",", ":")).encode())).decode()], timeout=90)
            (output / "source-verification.stdout").write_bytes(verify.stdout)
            (output / "source-verification.stderr").write_bytes(verify.stderr)
            report["public_tree_sha256"] = sha(tree_raw)
            if verify.returncode:
                raise ValueError("public source byte audit failed to run")
            observation = json.loads(verify.stdout)
            report["source_verification"] = observation
            if observation.get("checked") != 2563 or observation.get("differences"):
                raise ValueError("image bytes do not match the public Git tree")
            dependency_code = ("import importlib.metadata as m,json; "
                               "print(json.dumps(sorted((d.metadata['Name'],d.version) "
                               "for d in m.distributions() if d.metadata.get('Name'))))")
            dependencies = control([ENGINE, "exec", "--user=65534:65534", "-w", "/tmp", cid, PYTHON,
                                    "-I", "-B", "-c", dependency_code], timeout=45)
            (output / "dependencies.stdout").write_bytes(dependencies.stdout)
            (output / "dependencies.stderr").write_bytes(dependencies.stderr)
            if dependencies.returncode:
                raise ValueError("private dependency inventory unavailable")
            report["dependency_inventory_sha256"] = sha(dependencies.stdout)
            (output / "container-inspect.json").write_bytes(inspected.stdout)
            code = SOURCE
            result = _bounded([ENGINE, "exec", "--user=65534:65534", "-w", "/testbed", cid, PYTHON, "-I", "-B", "-c", code],
                              home=home, timeout=300, limit=8_000_000, check=lambda: None)
            (output / "collection.stdout").write_bytes(result.stdout.encode())
            (output / "collection.stderr").write_bytes(result.stderr.encode())
            report.update(exit_code=result.returncode, timed_out=result.timed_out,
                          stdout_truncated=result.stdout_truncated, stderr_truncated=result.stderr_truncated,
                          stdout_sha256=sha(result.stdout.encode()), stderr_sha256=sha(result.stderr.encode()))
            if result.timed_out or result.stdout_truncated or result.stderr_truncated:
                raise ValueError("collection output incomplete")
            observation = parse_collection(result.stdout, result.returncode)
            (output / "collection.json").write_text(json.dumps(observation, indent=2, sort_keys=True) + "\n")
            nodes = [row["nodeid"] for row in observation["items"]]
            if len(nodes) != len(set(nodes)):
                raise ValueError("execution inventory is duplicate or incomplete")
            public_paths = {row["path"] for row in tree["blobs"]}
            collected_paths = {row["path"] for row in observation["items"]}
            status = ("collection_observed" if result.returncode == 0 and nodes
                      and not observation["collection_errors"] and not observation["collection_skips"]
                      and not (collected_paths - public_paths) else "collection_observed_with_errors")
            report.update(status=status, collected=len(observation["items"]),
                          collected_files=len(collected_paths), nonpublic_collected_files=sorted(collected_paths - public_paths),
                          collection_errors=observation["collection_errors"], collection_skips=observation["collection_skips"],
                          completed=observation.get("completed"), outcomes=observation.get("outcomes"))
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
