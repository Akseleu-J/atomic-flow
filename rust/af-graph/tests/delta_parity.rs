//! N04 parity evidence: one structural file delta must land the store on
//! exactly the same canonical bytes as a clean rebuild of the same inputs.
//! `perf.rs` counters alone never proved this equality; this test does.
use af_graph::delta::{apply_delta, fallback_rebuild, file_delta};
use af_graph::project::clean_build;
use af_graph::GraphStore;
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::path::PathBuf;

fn files_map(pairs: &[(&str, &[u8])]) -> BTreeMap<String, Vec<u8>> {
    pairs
        .iter()
        .map(|(path, bytes)| ((*path).to_string(), (*bytes).to_vec()))
        .collect()
}

fn file_source_digest(build: &Value, path: &str) -> String {
    build["nodes"]
        .as_array()
        .unwrap()
        .iter()
        .find(|node| node["kind"] == json!("file") && node["path"] == json!(path))
        .and_then(|node| node.pointer("/source/digest"))
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

// Compare both paths through the same persistence contract. clean_build emits
// adapter traversal order; GraphStore canonicalizes arrays by stable ID. Seed
// the independent store at the same base generation, then replace its whole
// graph from the final sources. No node/edge fields or lifecycle metadata are
// removed, and the exported schema and generation must also match.
fn assert_clean_rebuild_matches(
    store: &GraphStore,
    build0: &Value,
    final_files: &BTreeMap<String, Vec<u8>>,
) {
    let path = std::env::temp_dir().join(format!(
        "af-parity-clean-{}-{}.json",
        std::process::id(),
        store.node_count(),
    ));
    let _ = std::fs::remove_file(&path);
    let mut clean = GraphStore::open(&path).unwrap();
    clean
        .commit(
            0,
            build0["nodes"].as_array().unwrap(),
            build0["edges"].as_array().unwrap(),
            &[],
            &[],
        )
        .unwrap();
    fallback_rebuild(&mut clean, final_files, "independent parity rebuild").unwrap();
    let actual = store.export_canonical();
    let expected = clean.export_canonical();
    assert!(
        actual == expected,
        "delta result diverged from clean rebuild: {}",
        first_difference(&store.document(), &clean.document(), "")
            .unwrap_or_else(|| "canonical serialization differs".to_string())
    );
    let _ = std::fs::remove_file(&path);
    let _ = std::fs::remove_file(path.with_extension("json.lock"));
}

fn first_difference(actual: &Value, expected: &Value, path: &str) -> Option<String> {
    if actual == expected {
        return None;
    }
    match (actual, expected) {
        (Value::Object(a), Value::Object(b)) if a.keys().eq(b.keys()) => {
            a.iter().find_map(|(key, value)| {
                let escaped = key.replace('~', "~0").replace('/', "~1");
                first_difference(value, &b[key], &format!("{path}/{escaped}"))
            })
        }
        (Value::Array(a), Value::Array(b)) if a.len() == b.len() => a
            .iter()
            .zip(b)
            .enumerate()
            .find_map(|(index, (a, b))| first_difference(a, b, &format!("{path}/{index}"))),
        _ => Some(format!("{path}: actual={actual}, expected={expected}")),
    }
}

#[test]
fn one_file_delta_matches_a_clean_rebuild_byte_for_byte() {
    let old_a = b"def alpha():\n    return 1\n".as_slice();
    let module_b = b"from a import alpha\n\ndef beta():\n    return alpha() + 1\n".as_slice();
    let new_a = b"def alpha():\n    return 41\n\ndef gamma():\n    return 7\n".as_slice();

    let build0 = clean_build(&files_map(&[("a.py", old_a), ("b.py", module_b)])).unwrap();
    let store_path: PathBuf =
        std::env::temp_dir().join(format!("af-parity-{}-{}.json", std::process::id(), line!()));
    let _ = std::fs::remove_file(&store_path);
    let mut store = GraphStore::open(&store_path).unwrap();
    store
        .commit(
            store.generation(),
            build0["nodes"].as_array().unwrap(),
            build0["edges"].as_array().unwrap(),
            &[],
            &[],
        )
        .unwrap();
    assert_eq!(store.generation(), 1);

    // Structural delta path: replace a.py through the file delta.
    let old_digest = file_source_digest(&build0, "a.py");
    assert!(
        !old_digest.is_empty(),
        "clean build must carry source digests"
    );
    let delta = file_delta(
        "a.py",
        Some(new_a),
        Some(&old_digest),
        Some(store.generation()),
    )
    .unwrap();
    let report = apply_delta(&mut store, &delta).unwrap();
    assert_eq!(store.generation(), 2);
    // The relink set contains the changed file and its affected importer.
    assert_eq!(
        report["relinked_files"],
        json!(2),
        "a.py and b.py must relink"
    );

    // Clean rebuild path: the same final inputs from scratch.
    assert_clean_rebuild_matches(
        &store,
        &build0,
        &files_map(&[("a.py", new_a), ("b.py", module_b)]),
    );

    // Superseded base generation is refused (no silent rebase).
    let stale = file_delta("a.py", Some(new_a), Some(&old_digest), Some(0)).unwrap();
    let before_stale = store.export_canonical();
    assert!(apply_delta(&mut store, &stale).is_err());
    assert_eq!(store.export_canonical(), before_stale);
    let _ = std::fs::remove_file(&store_path);
    let _ = std::fs::remove_file(store_path.with_extension("json.lock"));
}

#[test]
fn delete_delta_matches_a_clean_rebuild_without_the_file() {
    let module_a = b"def alpha():\n    return 1\n".as_slice();
    let module_b = b"def beta():\n    return 2\n".as_slice();
    let build0 = clean_build(&files_map(&[("a.py", module_a), ("b.py", module_b)])).unwrap();
    let store_path: PathBuf =
        std::env::temp_dir().join(format!("af-parity-{}-{}.json", std::process::id(), line!()));
    let _ = std::fs::remove_file(&store_path);
    let mut store = GraphStore::open(&store_path).unwrap();
    store
        .commit(
            store.generation(),
            build0["nodes"].as_array().unwrap(),
            build0["edges"].as_array().unwrap(),
            &[],
            &[],
        )
        .unwrap();

    let old_digest = file_source_digest(&build0, "a.py");
    let delta = file_delta("a.py", None, Some(&old_digest), Some(store.generation())).unwrap();
    apply_delta(&mut store, &delta).unwrap();

    assert_clean_rebuild_matches(&store, &build0, &files_map(&[("b.py", module_b)]));
    let _ = std::fs::remove_file(&store_path);
    let _ = std::fs::remove_file(store_path.with_extension("json.lock"));
}
