//! N04 parity evidence: one structural file delta must land the store on
//! exactly the same canonical bytes as a clean rebuild of the same inputs.
//! `perf.rs` counters alone never proved this equality; this test does.
use af_graph::canonical::canonical_bytes;
use af_graph::delta::{apply_delta, file_delta};
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

#[test]
fn one_file_delta_matches_a_clean_rebuild_byte_for_byte() {
    let old_a = b"def alpha():\n    return 1\n".as_slice();
    let module_b = b"from a import alpha\n\ndef beta():\n    return alpha() + 1\n".as_slice();
    let new_a = b"def alpha():\n    return 41\n\ndef gamma():\n    return 7\n".as_slice();

    let build0 = clean_build(&files_map(&[("a.py", old_a), ("b.py", module_b)])).unwrap();
    let store_path: PathBuf = std::env::temp_dir().join(format!(
        "af-parity-{}-{}.json",
        std::process::id(),
        line!()
    ));
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
    assert!(!old_digest.is_empty(), "clean build must carry source digests");
    let delta = file_delta(
        "a.py",
        Some(new_a),
        Some(&old_digest),
        Some(store.generation()),
    )
    .unwrap();
    let report = apply_delta(&mut store, &delta).unwrap();
    assert_eq!(store.generation(), 2);
    assert_eq!(report["relinked_files"], json!(1), "b.py must relink");

    // Clean rebuild path: the same final inputs from scratch.
    let build1 = clean_build(&files_map(&[("a.py", new_a), ("b.py", module_b)])).unwrap();

    let delta_view = json!({"nodes": store.nodes(), "edges": store.edges()});
    let clean_view = json!({
        "nodes": build1["nodes"].as_array().unwrap(),
        "edges": build1["edges"].as_array().unwrap(),
    });
    assert_eq!(
        canonical_bytes(&delta_view),
        canonical_bytes(&clean_view),
        "delta result diverged from clean rebuild"
    );

    // Superseded base generation is refused (no silent rebase).
    let stale = file_delta("a.py", Some(new_a), Some(&old_digest), Some(0)).unwrap();
    assert!(apply_delta(&mut store, &stale).is_err());
    let _ = std::fs::remove_file(&store_path);
}

#[test]
fn delete_delta_matches_a_clean_rebuild_without_the_file() {
    let module_a = b"def alpha():\n    return 1\n".as_slice();
    let module_b = b"def beta():\n    return 2\n".as_slice();
    let build0 = clean_build(&files_map(&[("a.py", module_a), ("b.py", module_b)])).unwrap();
    let store_path: PathBuf = std::env::temp_dir().join(format!(
        "af-parity-{}-{}.json",
        std::process::id(),
        line!()
    ));
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

    let build1 = clean_build(&files_map(&[("b.py", module_b)])).unwrap();
    let delta_view = json!({"nodes": store.nodes(), "edges": store.edges()});
    let clean_view = json!({
        "nodes": build1["nodes"].as_array().unwrap(),
        "edges": build1["edges"].as_array().unwrap(),
    });
    assert_eq!(
        canonical_bytes(&delta_view),
        canonical_bytes(&clean_view),
        "delete delta diverged from clean rebuild"
    );
    let _ = std::fs::remove_file(&store_path);
}
