//! Byte-exact canonical JSON serialization matching the Python spec's
//! `json.dumps(document, sort_keys=True, ensure_ascii=False, indent=1) + "\n"`.
//! Parity between the Python draft and this port is checked against these
//! bytes, so the writer is hand-rolled instead of reusing serde_json pretty
//! printing (2-space indent, different escaping of `/`-adjacent cases).

use serde_json::Value;

pub fn canonical_bytes(value: &Value) -> Vec<u8> {
    let mut out = String::new();
    write_value(&mut out, value, 0);
    out.push('\n');
    out.into_bytes()
}

fn write_value(out: &mut String, value: &Value, depth: usize) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Number(number) => out.push_str(&number.to_string()),
        Value::String(text) => write_string(out, text),
        Value::Array(items) => {
            if items.is_empty() {
                out.push_str("[]");
                return;
            }
            out.push_str("[\n");
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push_str(",\n");
                }
                push_indent(out, depth + 1);
                write_value(out, item, depth + 1);
            }
            out.push('\n');
            push_indent(out, depth);
            out.push(']');
        }
        Value::Object(map) => {
            if map.is_empty() {
                out.push_str("{}");
                return;
            }
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            out.push_str("{\n");
            for (index, key) in keys.iter().enumerate() {
                if index > 0 {
                    out.push_str(",\n");
                }
                push_indent(out, depth + 1);
                write_string(out, key);
                out.push_str(": ");
                write_value(out, &map[*key], depth + 1);
            }
            out.push('\n');
            push_indent(out, depth);
            out.push('}');
        }
    }
}

fn push_indent(out: &mut String, depth: usize) {
    for _ in 0..depth {
        out.push(' ');
    }
}

fn write_string(out: &mut String, text: &str) {
    out.push('"');
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => {
                out.push_str(&format!("\\u{:04x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn matches_python_json_dumps_shape() {
        let value = json!({"b": 1, "a": {"z": [1, 2], "": "x\"y"}, "list": []});
        // Python: json.dumps(value, sort_keys=True, ensure_ascii=False, indent=1) + "\n"
        let expected = "{\n \"a\": {\n  \"\": \"x\\\"y\",\n  \"z\": [\n   1,\n   2\n  ]\n },\n \"b\": 1,\n \"list\": []\n}\n";
        assert_eq!(
            String::from_utf8(canonical_bytes(&value)).unwrap(),
            expected
        );
    }

    #[test]
    fn unicode_stays_raw_and_control_escaped() {
        let value = json!({"k": "héllo\n\u{1}"});
        let text = String::from_utf8(canonical_bytes(&value)).unwrap();
        assert!(text.contains("héllo\\n\\u0001"));
    }
}
