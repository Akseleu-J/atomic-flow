//! MCP (Model Context Protocol) kernel contract: JSON-RPC 2.0 framing and
//! the server-side methods a B2B integration needs — initialize, tools/list,
//! tools/call — with strict validation and kernel tools mapped from
//! `af_llm::ToolSpec`. The stdio transport loop is a thin host concern; this
//! module owns the message contract.

use af_llm::ToolSpec;
use serde_json::{json, Value};

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct McpError(pub String);

pub type McpResult<T> = Result<T, McpError>;

/// Parse one JSON-RPC request; batch requests are refused (our server is
/// line-delimited single-call, matching the stdio transport).
pub fn parse_request(line: &str) -> McpResult<Value> {
    let value: Value = serde_json::from_str(line)
        .map_err(|error| McpError(format!("invalid JSON-RPC request: {error}")))?;
    if !value.is_object() {
        return Err(McpError("JSON-RPC request must be an object".into()));
    }
    if value.get("method").and_then(Value::as_str).is_none() {
        return Err(McpError("JSON-RPC request requires a method".into()));
    }
    let id_ok = value
        .get("id")
        .is_some_and(|id| id.is_string() || id.is_u64() || id.is_i64());
    if !id_ok {
        return Err(McpError(
            "JSON-RPC request requires a string or integer id".into(),
        ));
    }
    Ok(value)
}

pub fn response_ok(id: &Value, result: Value) -> String {
    serde_json::to_string(&json!({"jsonrpc": "2.0", "id": id, "result": result}))
        .expect("response is valid JSON")
}

pub fn response_error(id: &Value, code: i64, message: &str) -> String {
    serde_json::to_string(&json!({
        "jsonrpc": "2.0", "id": id,
        "error": {"code": code, "message": message},
    }))
    .expect("response is valid JSON")
}

/// The initialize handshake result (protocol version we speak).
pub fn initialize_result(server_name: &str) -> Value {
    json!({
        "protocolVersion": "2025-06-18",
        "capabilities": {"tools": {"listChanged": false}},
        "serverInfo": {"name": server_name, "version": env!("CARGO_PKG_VERSION")},
    })
}

/// tools/list result: kernel tools in MCP tool shape (inputSchema = the JSON
/// schema parameters we already advertise to models).
pub fn tools_list_result(tools: &[ToolSpec]) -> Value {
    json!({
        "tools": tools.iter().map(|tool| json!({
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.parameters,
        })).collect::<Vec<_>>(),
    })
}

/// tools/call: validate name and arguments, execute via the injected closure,
/// and wrap the result in MCP content blocks. Unknown tools are a JSON-RPC
/// error, never a panic; tool-level failures are `isError` content.
pub fn tools_call(
    params: &Value,
    tools: &[ToolSpec],
    execute: &dyn Fn(&str, &Value) -> Value,
) -> McpResult<Value> {
    let name = params
        .get("name")
        .and_then(Value::as_str)
        .ok_or_else(|| McpError("tools/call requires a tool name".into()))?;
    let Some(spec) = tools.iter().find(|tool| tool.name == name) else {
        return Err(McpError(format!("unknown tool {name}")));
    };
    let arguments = params
        .get("arguments")
        .cloned()
        .unwrap_or_else(|| json!({}));
    if !arguments.is_object() {
        return Err(McpError("tools/call arguments must be an object".into()));
    }
    // Required-property pre-check against the schema we advertised.
    if let Some(required) = spec.parameters.get("required").and_then(Value::as_array) {
        for property in required {
            let Some(property) = property.as_str() else {
                continue;
            };
            if arguments.get(property).is_none() {
                return Err(McpError(format!(
                    "tools/call missing required argument {property}"
                )));
            }
        }
    }
    let output = execute(name, &arguments);
    let is_error = output.get("error").is_some();
    Ok(json!({
        "content": [{"type": "text", "text": serde_json::to_string(&output).unwrap_or_default()}],
        "isError": is_error,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn probe_tools() -> Vec<ToolSpec> {
        vec![ToolSpec {
            name: "read_file".into(),
            description: "read a file".into(),
            parameters: json!({
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            }),
        }]
    }

    #[test]
    fn handshake_and_tools_list() {
        let request =
            parse_request(r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}"#).unwrap();
        let result = initialize_result("af");
        assert_eq!(result["protocolVersion"], json!("2025-06-18"));
        assert_eq!(request["id"], json!(1));
        let listed = tools_list_result(&probe_tools());
        assert_eq!(listed["tools"][0]["name"], json!("read_file"));
        assert_eq!(
            listed["tools"][0]["inputSchema"]["required"][0],
            json!("path")
        );
    }

    #[test]
    fn malformed_requests_are_refused() {
        assert!(parse_request("not json").is_err());
        assert!(parse_request("[1,2]").is_err(), "batches refused");
        assert!(
            parse_request(r#"{"jsonrpc":"2.0","id":1}"#).is_err(),
            "no method"
        );
        assert!(
            parse_request(r#"{"jsonrpc":"2.0","method":"x"}"#).is_err(),
            "missing id"
        );
        assert!(
            parse_request(r#"{"jsonrpc":"2.0","id":1.5,"method":"x"}"#).is_err(),
            "float id"
        );
    }

    #[test]
    fn tools_call_validates_and_wraps() {
        let execute = |_name: &str, arguments: &Value| json!({"content": arguments["path"].as_str().unwrap_or_default()});
        let ok = tools_call(
            &json!({"name": "read_file", "arguments": {"path": "a.py"}}),
            &probe_tools(),
            &execute,
        )
        .unwrap();
        assert_eq!(ok["isError"], json!(false));
        assert!(ok["content"][0]["text"].as_str().unwrap().contains("a.py"));
        // Unknown tool is an error result for the transport to map.
        assert!(tools_call(
            &json!({"name": "ghost", "arguments": {}}),
            &probe_tools(),
            &execute,
        )
        .is_err());
        // Missing required argument refused before execution.
        assert!(tools_call(&json!({"name": "read_file"}), &probe_tools(), &execute).is_err());
        // Tool-level failure becomes isError content, not a transport error.
        let failing = tools_call(
            &json!({"name": "read_file", "arguments": {"path": "x"}}),
            &probe_tools(),
            &|_name, _args| json!({"error": "not found"}),
        )
        .unwrap();
        assert_eq!(failing["isError"], json!(true));
    }

    #[test]
    fn error_response_shape() {
        let response = response_error(&json!(7), -32601, "method not found");
        let parsed: Value = serde_json::from_str(&response).unwrap();
        assert_eq!(parsed["error"]["code"], json!(-32601));
        assert_eq!(parsed["id"], json!(7));
    }
}
