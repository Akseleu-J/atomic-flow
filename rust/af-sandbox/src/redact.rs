//! Mask credentials before source text is sent to a model provider
//! (byte-level port of `activation/redact.py`).

use regex::Regex;
use std::sync::OnceLock;

static BLOCK: OnceLock<Regex> = OnceLock::new();
static TOKENS: OnceLock<Regex> = OnceLock::new();
static LITERAL: OnceLock<Regex> = OnceLock::new();

fn block() -> &'static Regex {
    BLOCK.get_or_init(|| {
        Regex::new(r"(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----")
            .expect("static redact pattern")
    })
}

fn tokens() -> &'static Regex {
    TOKENS.get_or_init(|| {
        Regex::new(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|\bsk-[A-Za-z0-9_-]{20,}\b|\bgh[pousr]_[A-Za-z0-9]{36,}\b|\bxox[abprs]-[A-Za-z0-9-]{10,}")
            .expect("static redact pattern")
    })
}

fn literal() -> &'static Regex {
    LITERAL.get_or_init(|| {
        // No look-around and no backreferences in the `regex` crate: the
        // closing quote is matched separately and verified in the closure,
        // and the Python draft's `(?!\w*_env)` guard becomes a suffix check.
        Regex::new(r#"(?i)(\b(?:api[_-]?key|secret|token|password|passwd))(\w*)(\s*[:=]\s*)(['"])([^'"\s]{8,})(['"])"#)
            .expect("static redact pattern")
    })
}

fn blank_line_preserving(replacement_count: usize) -> String {
    format!("[REDACTED]{}", "\n".repeat(replacement_count))
}

pub fn redact(text: &str) -> String {
    let block_replaced = block().replace_all(text, |caps: &regex::Captures| {
        blank_line_preserving(caps[0].matches('\n').count())
    });
    let token_replaced = tokens().replace_all(&block_replaced, "[REDACTED]");
    literal()
        .replace_all(&token_replaced, |caps: &regex::Captures| {
            if caps[4] != caps[6] {
                return caps[0].to_string();
            }
            let suffix = caps[2].to_lowercase();
            if suffix.contains("_env") {
                caps[0].to_string()
            } else {
                format!("{}{}{}{}[REDACTED]{}", &caps[1], &caps[2], &caps[3], &caps[4], &caps[4])
            }
        })
        .into_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn masks_private_keys_preserving_line_count() {
        let text = "before\n-----BEGIN RSA PRIVATE KEY-----\nabc\ndef\n-----END RSA PRIVATE KEY-----\nafter";
        let out = redact(text);
        assert!(out.contains("before\n[REDACTED]\n\n\n\nafter"));
        assert!(!out.contains("PRIVATE"));
    }

    #[test]
    fn masks_known_token_shapes() {
        let out = redact(&format!(
            "key AKIAIOSFODNN7EXAMPLE and sk-abcdefghijklmnopqrstuvwx and ghp_{}",
            "a".repeat(36)
        ));
        assert!(!out.contains("AKIAIOSFODNN7EXAMPLE"));
        assert!(!out.contains("sk-abcdefghijklmnopqrstuvwx"));
        assert!(out.matches("[REDACTED]").count() == 3);
    }

    #[test]
    fn masks_literal_key_value_assignments() {
        let out = redact("api_key = 'supersecretvalue'\ntoken_env = 'keepme'\npassword: \"hunter2hunter2\"");
        assert!(!out.contains("supersecretvalue"));
        assert!(!out.contains("hunter2hunter2"));
        assert!(out.contains("token_env = 'keepme'"), "token_env must not match the (?!\\w*_env) guard");
    }
}
