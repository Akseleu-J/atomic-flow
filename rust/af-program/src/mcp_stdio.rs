//! MCP stdio transport loop (the missing half of the MCP surface): reads
//! line-delimited JSON-RPC from any `BufRead`, dispatches initialize /
//! tools/list / tools/call through the [`super::mcp`] contract against a
//! kernel tool registry, writes one response line per request. Unknown
//! methods get JSON-RPC error -32601; malformed lines get -32700 with a null
//! id (protocol rules) and the loop continues — a bad line never kills the
//! server.

use super::mcp;
use af_llm::ToolSpec;
use serde_json::{json, Value};
use std::io::Write;

/// One kernel tool exposed over MCP: spec + sync executor.
pub struct McpTool<'a> {
    pub spec: ToolSpec,
    pub execute: &'a dyn Fn(&Value) -> Value,
}

pub const METHOD_NOT_FOUND: i64 = -32601;
pub const PARSE_ERROR: i64 = -32700;

/// Dispatch one parsed request against the tool registry. Returns the
/// response line (ok or error) — the transport only writes it.
pub fn dispatch(request: &Value, tools: &[McpTool]) -> Option<String> {
    let id = request.get("id").cloned().unwrap_or(Value::Null);
    match request["method"].as_str().unwrap_or_default() {
        "initialize" => Some(mcp::response_ok(&id, mcp::initialize_result("af"))),
        "tools/list" => {
            let specs: Vec<ToolSpec> = tools.iter().map(|tool| tool.spec.clone()).collect();
            Some(mcp::response_ok(&id, mcp::tools_list_result(&specs)))
        }
        "tools/call" => {
            let params = request.get("params").cloned().unwrap_or_else(|| json!({}));
            match mcp::tools_call(&params, &specs_of(tools), &|name, arguments| {
                let Some(tool) = tools.iter().find(|tool| tool.spec.name == name) else {
                    return json!({"error": format!("unknown tool {name}")});
                };
                (tool.execute)(arguments)
            }) {
                Ok(result) => Some(mcp::response_ok(&id, result)),
                Err(error) => Some(mcp::response_error(&id, METHOD_NOT_FOUND, &error.0)),
            }
        }
        "ping" => Some(mcp::response_ok(&id, json!({}))),
        other => Some(mcp::response_error(
            &id,
            METHOD_NOT_FOUND,
            &format!("method not found: {other}"),
        )),
    }
}

fn specs_of(tools: &[McpTool]) -> Vec<ToolSpec> {
    tools.iter().map(|tool| tool.spec.clone()).collect()
}

/// The stdio loop: read requests until EOF, write one response line each.
/// Malformed lines produce a parse-error response with null id and the loop
/// continues — a bad line never kills the server.
pub fn run_stdio(
    input: &mut dyn std::io::BufRead,
    output: &mut dyn Write,
    tools: &[McpTool],
) -> std::io::Result<usize> {
    let mut served = 0usize;
    loop {
        let mut line = Vec::new();
        if input.read_until(b'\n', &mut line)? == 0 {
            return Ok(served);
        }
        let text = String::from_utf8_lossy(&line);
        let text = text.trim();
        if text.is_empty() {
            continue;
        }
        let response = match mcp::parse_request(text) {
            Ok(request) => dispatch(&request, tools).unwrap_or_default(),
            Err(error) => mcp::response_error(&Value::Null, PARSE_ERROR, &error.0),
        };
        output.write_all(response.as_bytes())?;
        output.write_all(b"\n")?;
        output.flush()?;
        served += 1;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn probe_tools() -> Vec<McpTool<'static>> {
        vec![McpTool {
            spec: ToolSpec {
                name: "kernel_version".into(),
                description: "kernel version".into(),
                parameters: json!({"type": "object"}),
            },
            execute: &|_arguments| json!({"version": "0.1.0"}),
        }]
    }

    fn run_lines(lines: &[&str]) -> Vec<Value> {
        let joined = lines.join("\n");
        let mut input = std::io::BufReader::new(joined.as_bytes());
        let tools = probe_tools();
        let mut output: Vec<u8> = Vec::new();
        let served = run_stdio(&mut input, &mut output, &tools).unwrap();
        let expected = lines.iter().filter(|line| !line.trim().is_empty()).count();
        assert_eq!(served, expected, "served counts dispatches, not raw lines");
        String::from_utf8(output)
            .unwrap()
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect()
    }

    #[test]
    fn stdio_loop_serves_the_full_handshake() {
        let responses = run_lines(&[
            r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}"#,
            r#"{"jsonrpc":"2.0","id":2,"method":"tools/list"}"#,
            r#"{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"kernel_version","arguments":{}}}"#,
        ]);
        assert_eq!(
            responses[0]["result"]["protocolVersion"],
            json!("2025-06-18")
        );
        assert_eq!(
            responses[1]["result"]["tools"][0]["name"],
            json!("kernel_version")
        );
        assert_eq!(
            responses[2]["result"]["content"][0]["text"],
            json!(r#"{"version":"0.1.0"}"#)
        );
        assert_eq!(responses[2]["result"]["isError"], json!(false));
    }

    #[test]
    fn unknown_method_and_bad_line_survive_the_loop() {
        let responses = run_lines(&[
            r#"{"jsonrpc":"2.0","id":1,"method":"nope"}"#,
            "not json at all",
            r#"{"jsonrpc":"2.0","id":2,"method":"ping"}"#,
        ]);
        assert_eq!(responses.len(), 3);
        assert_eq!(responses[0]["error"]["code"], json!(METHOD_NOT_FOUND));
        assert_eq!(responses[1]["error"]["code"], json!(PARSE_ERROR));
        assert_eq!(responses[1]["id"], json!(null), "bad line gets null id");
        assert_eq!(responses[2]["result"], json!({}), "the loop continued");
    }

    #[test]
    fn tool_failure_is_iserror_content_not_a_crash() {
        let tools = vec![McpTool {
            spec: ToolSpec {
                name: "fail".into(),
                description: String::new(),
                parameters: json!({"type": "object"}),
            },
            execute: &|_arguments| json!({"error": "boom"}),
        }];
        let mut input = std::io::BufReader::new(
            r#"{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"fail","arguments":{}}}"#.as_bytes(),
        );
        let mut output: Vec<u8> = Vec::new();
        run_stdio(&mut input, &mut output, &tools).unwrap();
        let response: Value = serde_json::from_slice(&output).unwrap();
        assert_eq!(response["result"]["isError"], json!(true));
    }

    #[test]
    fn empty_lines_are_skipped_not_dispatched() {
        let responses = run_lines(&["", r#"{"jsonrpc":"2.0","id":9,"method":"ping"}"#, ""]);
        assert_eq!(responses.len(), 1, "empty lines produce no responses");
        assert_eq!(responses[0]["id"], json!(9));
    }
}
