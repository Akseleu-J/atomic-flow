//! Preserve a canonical Python reference vector while naming the older adapter gap.
use serde_json::{json, Value};
#[test]
fn python_reference_identity_bytes_symbols_and_lines_are_preserved() {
    let fixture: Value =
        serde_json::from_str(include_str!("fixtures/python-reference-v3.json")).unwrap();
    let actual = af_graph::adapter_ast::adapt_file(
        fixture["path"].as_str().unwrap(),
        fixture["utf8"].as_str().unwrap().as_bytes(),
    );
    let expected = &fixture["reference"];
    for (a, b) in actual["nodes"]
        .as_array()
        .unwrap()
        .iter()
        .zip(expected["nodes"].as_array().unwrap())
    {
        for key in [
            "id",
            "kind",
            "path",
            "source",
            "symbol",
            "start_line",
            "end_line",
        ] {
            assert_eq!(a[key], b[key], "{key}");
        }
    }
    assert_eq!(
        actual["nodes"].as_array().unwrap().len(),
        expected["nodes"].as_array().unwrap().len()
    );
    assert_eq!(
        actual["nodes"][1]["facts"]["signature"],
        expected["nodes"][1]["facts"]["signature"]
    );
    assert_eq!(actual["edges"][0]["id"], expected["edges"][0]["id"]);
    // Different extractor versions remain explicit: the old Rust Python adapter
    // has not implemented Python-v3 import binding/shadowing/type semantics.
    assert_eq!(
        expected["nodes"][0]["facts"]["extractor"],
        json!("python-ast/3")
    );
    assert_eq!(
        actual["nodes"][0]["facts"]["extractor"],
        json!("python-ast/1")
    );
}
