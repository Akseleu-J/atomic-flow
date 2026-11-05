"""Python-side smoke for af_core_py: the lab drives the Rust kernel."""
import json
import tempfile
from pathlib import Path

import af_core_py as af

# 1. version
version = json.loads(af.version())
assert version["kernel"] == "atomic-flow-rust", version
print("version:", version)

# 2. scripted backend ping (no spend)
ping = json.loads(af.backend_ping())
assert ping["ok"] and ping["content"] == "scripted-echo", ping
print("backend_ping:", ping)

# 3. graph build over a scratch root
root = Path(tempfile.mkdtemp(prefix="af-py-root-"))
(root / "lib.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
(root / "app.py").write_text("from lib import helper\n\ndef main():\n    return helper()\n", encoding="utf-8")
build = json.loads(af.graph_build(str(root)))
assert build["ok"] and build["files"] == 2 and build["nodes"] >= 3, build
print("graph_build:", build)

# 4. parity oracle: private-body edit keeps delta == clean
parity = json.loads(af.graph_parity(str(root), "lib.py", "def helper():\n    return 1  # changed\n"))
assert parity["parity"], parity
print("graph_parity: PASS")

# 5. sessions through the journal
home = Path(tempfile.mkdtemp(prefix="af-py-home-"))
import os
previous = os.environ.get("ATOMIC_FLOW_HOME")
os.environ["ATOMIC_FLOW_HOME"] = str(home)
session = json.loads(af.session_create(str(home / "proj"), "py-smoke"))
event = json.loads(af.session_append("py-smoke", "user_input", json.dumps({"text": "привет"}), None, None))
snapshot = json.loads(af.session_read("py-smoke"))
assert snapshot["events"] == 2 and not snapshot["unresolved_call_ids"], snapshot
print("session roundtrip:", snapshot)

# 6. sandbox grant + redacted read
registry_home = home / "sandbox"
os.environ["ATOMIC_FLOW_HOME"] = str(registry_home)
assert af.sandbox_grant("pyroot", str(root), ["lib.py"])
page = json.loads(af.sandbox_read("pyroot", "lib.py", 12000))
assert "return 1" in page["source"], page
# Secret in source is redacted before it ever leaves the kernel.
real_token = "ghp_" + "a" * 36
(root / "lib.py").write_text(f"token = '{real_token}'\n", encoding="utf-8")
page = json.loads(af.sandbox_read("pyroot", "lib.py", 12000))
assert "[REDACTED]" in page["source"] and real_token not in page["source"], page
print("sandbox read + redaction: PASS")

if previous:
    os.environ["ATOMIC_FLOW_HOME"] = previous
else:
    os.environ.pop("ATOMIC_FLOW_HOME", None)
print("PY-SMOKE PASS")
