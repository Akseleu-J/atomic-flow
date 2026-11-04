"""Validate planning inventory completeness and references; grants no acceptance."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[2]

def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result

def main():
    registry = json.loads((ROOT / "docs/PRODUCTION_STATUS_2026-11-04.json").read_text(encoding="utf-8"), object_pairs_hook=unique)
    expected = {f"P{i:02}" for i in range(15)} | {f"E{i:02}" for i in range(9)}
    chapters = registry["chapters"]
    assert len(chapters) == 24 and {c["id"] for c in chapters} == expected
    assert registry["decision"] == "not_ready" and registry["provider_calls_this_pass"] == 0
    packages = {p["id"]: p for p in registry["next_packages"]}
    assert len(packages) == len(registry["next_packages"])
    for chapter in chapters:
        assert chapter["chapter_production_status"] == "open"
        assert chapter["gaps"] and chapter["next_package"] in packages
        assert all(ref in registry["evidence"] for ref in chapter["evidence_refs"])
        assert all((ROOT / path).is_file() for path in chapter["implementation_paths"])
    visiting, done = set(), set()
    def visit(key):
        assert key in packages and key not in visiting, "unknown dependency or cycle"
        if key in done:
            return
        visiting.add(key)
        assert set(packages[key]["chapters"]) <= expected
        for dependency in packages[key]["depends_on"]:
            visit(dependency)
        visiting.remove(key)
        done.add(key)
    for key in packages:
        visit(key)
    print(json.dumps({"chapters": len(chapters), "packages": len(packages), "decision": registry["decision"], "validation": "passed", "production_authorized": False}))

if __name__ == "__main__":
    main()
