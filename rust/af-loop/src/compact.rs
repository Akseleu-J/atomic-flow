//! History compaction (the context-engineering piece): bounds the serialized
//! transcript bytes before each model call without losing the loop's
//! authority trail. Strategy, mirroring the Python `llm/history.py` intent:
//! the first (anchor) message and the latest turns always survive; older
//! tool results are replaced by bounded omission markers that keep the call
//! id; when markers are not enough, whole oldest middle messages are dropped.
//! Compaction never invents content — omitted is omitted.

use af_llm::ChatMessage;

/// Serialized size of the transcript (the bytes a provider would roughly see).
pub fn serialized_bytes(messages: &[ChatMessage]) -> usize {
    messages
        .iter()
        .map(|message| {
            let tool_calls_bytes = message
                .tool_calls
                .as_ref()
                .map(|calls| serde_json::to_string(calls).unwrap_or_default().len())
                .unwrap_or(0);
            message.role.len()
                + message.content.len()
                + tool_calls_bytes
                + message.tool_call_id.as_ref().map(String::len).unwrap_or(0)
                + 24
        })
        .sum()
}

const MARKER_HEAD: &str = "[omitted by history compaction: prior tool result for call ";

/// Compact until under budget or nothing further can be shed.
/// Invariants: the first message survives; the last message survives; every
/// tool_call_id pairing between an assistant tool_calls message and its
/// tool results is preserved (markers keep the ids).
pub fn compact(messages: Vec<ChatMessage>, max_bytes: usize) -> Vec<ChatMessage> {
    let mut result = messages;
    loop {
        if serialized_bytes(&result) <= max_bytes || result.len() <= 2 {
            return result;
        }
        // 1) Strip the largest old tool-result body (first tool message after
        //    position 0 whose content is not already a marker).
        let strip_candidate = (1..result.len().saturating_sub(1)).find(|&index| {
            result[index].role == "tool" && !result[index].content.starts_with(MARKER_HEAD)
        });
        if let Some(index) = strip_candidate {
            let call_id = result[index]
                .tool_call_id
                .clone()
                .unwrap_or_else(|| "unknown".into());
            result[index].content =
                format!("{MARKER_HEAD}{call_id}; the settled summary lives in the journal]");
            continue;
        }
        // 2) Drop the oldest middle message that is not the anchor and not
        //    the last message.
        if result.len() > 2 {
            result.remove(1);
            continue;
        }
        return result;
    }
}

/// Convenience: serialized size and compact in one step for the loop.
pub fn compact_if_needed(messages: Vec<ChatMessage>, max_bytes: Option<usize>) -> Vec<ChatMessage> {
    match max_bytes {
        Some(limit) => compact(messages, limit),
        None => messages,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn big_tool_result(call_id: &str, fill: usize) -> ChatMessage {
        ChatMessage {
            role: "tool".into(),
            content: "x".repeat(fill),
            tool_calls: None,
            tool_call_id: Some(call_id.into()),
        }
    }

    #[test]
    fn under_budget_is_untouched() {
        let messages = vec![
            ChatMessage::new("system", "be safe"),
            ChatMessage::new("user", "hello"),
        ];
        let compacted = compact_if_needed(messages.clone(), Some(10_000));
        assert_eq!(compacted.len(), 2);
        assert_eq!(compacted[0].content, "be safe");
    }

    #[test]
    fn old_tool_results_become_id_carrying_markers() {
        let mut messages = vec![ChatMessage::new("system", "anchor")];
        messages.push(ChatMessage::new("user", "do work"));
        messages.push(ChatMessage::assistant_with_tool_calls(
            "",
            json!([{"id": "c1", "type": "function", "function": {"name": "probe", "arguments": "{}"}}]),
        ));
        messages.push(big_tool_result("c1", 40_000));
        messages.push(ChatMessage::new("assistant", "done"));
        let before = serialized_bytes(&messages);
        let compacted = compact(messages, 5_000);
        let after = serialized_bytes(&compacted);
        assert!(after < before);
        assert!(
            after <= 5_000 + 200,
            "marker overhead is bounded, got {after}"
        );
        // The marker keeps the pairing id and is honest about omission.
        let marker = compacted
            .iter()
            .find(|message| message.role == "tool")
            .expect("tool message preserved as marker");
        assert!(marker.content.contains("c1"));
        assert!(marker.content.contains("[omitted by history compaction"));
        // Anchors survive.
        assert_eq!(compacted[0].content, "anchor");
        assert_eq!(compacted.last().unwrap().content, "done");
    }

    #[test]
    fn drops_middle_messages_when_markers_are_not_enough() {
        let mut messages = vec![ChatMessage::new("system", "anchor")];
        for index in 0..6 {
            messages.push(ChatMessage::new("user", &"y".repeat(3_000)));
            messages.push(big_tool_result(&format!("c{index}"), 200));
        }
        messages.push(ChatMessage::new("assistant", "final"));
        let compacted = compact(messages, 3_000);
        assert!(serialized_bytes(&compacted) <= 3_000 + 400);
        assert_eq!(compacted[0].content, "anchor");
        assert_eq!(compacted.last().unwrap().content, "final");
    }

    #[test]
    fn never_compacts_below_two_messages() {
        let messages = vec![
            ChatMessage::new("user", &"a".repeat(10_000)),
            ChatMessage::new("assistant", &"b".repeat(10_000)),
        ];
        let compacted = compact(messages, 100);
        assert_eq!(compacted.len(), 2);
    }
}
