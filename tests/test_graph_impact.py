"""P04 context-pack regressions: reasons, ordering, byte cap, unknown goals."""
import pytest

from atomic_flow.graph.impact import context_pack

FILES = {
    "app.py": b"from lib import helper\n\n\ndef run():\n    return helper()\n",
    "lib.py": b"def helper():\n    return 1\n",
    "noise.py": b"VALUE = 1\n",
}


def test_pack_orders_goals_first_with_reasons_and_blast_radius():
    pack = context_pack(FILES, ["lib.py"])
    entries = [(e["file"], e["reason"]) for e in pack["entries"]]
    assert entries[0] == ("lib.py", "goal file")
    assert ("app.py", "name match: lib.helper") in entries or any(f == "app.py" and "name match" in r for f, r in entries)
    assert ("noise.py", "goal file") not in entries and all(f != "noise.py" for f, _ in entries)


def test_pack_respects_byte_cap_and_marks_truncation():
    pack = context_pack(FILES, ["app.py", "lib.py"], byte_cap=len(FILES["app.py"]) + 4)
    assert pack["included_bytes"] <= pack["byte_cap"]
    assert any(e.get("truncated") for e in pack["entries"])
    assert all("over byte cap" in e["reason"] for e in pack["entries"] if e.get("truncated"))


def test_unknown_goal_is_refused():
    with pytest.raises(ValueError, match="unknown goal files"):
        context_pack(FILES, ["missing.py"])
