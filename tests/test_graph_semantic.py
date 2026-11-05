"""P03 deterministic semantic-layer regressions: layers/entries/tour/invalidation."""
from atomic_flow.graph.semantic import entry_points, invalidated, layers, tour

FILES = {
    "app.py": b"from lib import helper\n\n\ndef run():\n    return helper()\n",
    "lib.py": b"def helper():\n    return 1\n",
    "tools/util.py": b"def scrub(value):\n    return value\n",
    "tools/main.py": b"from tools.util import scrub\n\n\ndef main():\n    return scrub(1)\n",
}


def test_layers_group_by_top_package_deterministically():
    first, second = layers(FILES), layers(FILES)
    assert first == second
    assert first["groups"] == {".": ["app.py", "lib.py"], "tools": ["tools/main.py", "tools/util.py"]}
    assert set(first["source_digests"]) == set(FILES)


def test_entry_points_use_name_level_mentions_only():
    result = entry_points(FILES)
    assert "lib.py" not in result["entry_files"]        # mentioned by app.py
    assert "tools/util.py" not in result["entry_files"]  # mentioned by tools/main.py
    assert "app.py" in result["entry_files"] and "tools/main.py" in result["entry_files"]
    assert result["method"].startswith("name-level")


def test_tour_orders_entries_first_and_binds_digests():
    result = tour(FILES)
    positions = {step["file"]: step["position"] for step in result["steps"]}
    assert positions["app.py"] == 1 and positions["tools/main.py"] == 2
    assert "narrative" in result["note"]


def test_digest_change_invalidates_only_moved_files():
    artifact = tour(FILES)
    changed = dict(FILES, **{"lib.py": b"def helper():\n    return 2\n"})
    current = {node["path"]: node["source"]["digest"]
               for node in __import__("atomic_flow.graph.project", fromlist=["clean_build"]).clean_build(changed)["nodes"]
               if node["kind"] == "file"}
    assert invalidated(current, artifact) == ["lib.py"]
