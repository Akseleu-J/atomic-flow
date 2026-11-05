//! Host-only provider configuration and one durable owner for HTTP sends.
//! The browser selects an ID/model; it never selects an environment variable
//! or an arbitrary endpoint. No provider call is made by configuration reads.
use af_llm::{BackendOutcome, Cancel, ChatBackend, ChatRequest};
use serde_json::{json, Value};
use std::{io::Read, path::PathBuf};

pub const OWNER: &str = "dashboard";
pub fn ledger_path() -> PathBuf {
    af_sandbox::user_dir().join("budget").join("ledger.jsonl")
}

#[derive(Clone, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct Provider {
    id: String,
    label: String,
    base_url: String,
    api_key_env: String,
    models: Vec<String>,
    reservation_usd: String,
}
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct Config {
    schema_version: u32,
    providers: Vec<Provider>,
}

fn identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 80
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}
fn endpoint_allowed(value: &str) -> bool {
    if value.len() > 1024 || value.contains(['@', '?', '#', '\\']) {
        return false;
    }
    let Ok(uri) = value.parse::<axum::http::Uri>() else {
        return false;
    };
    uri.authority().is_some()
        && (uri.scheme_str() == Some("https")
            || uri.scheme_str() == Some("http")
                && matches!(uri.host(), Some("localhost" | "127.0.0.1" | "[::1]")))
}
fn decode(raw: &[u8]) -> Result<Vec<Provider>, &'static str> {
    if raw.len() > 65536 {
        return Err("provider configuration exceeds limit");
    }
    let config: Config =
        serde_json::from_slice(raw).map_err(|_| "invalid host provider configuration")?;
    if config.schema_version != 1 || config.providers.len() > 16 {
        return Err("unsupported host provider configuration");
    }
    let mut ids = std::collections::BTreeSet::new();
    for p in &config.providers {
        if !identifier(&p.id)
            || p.id == "scripted"
            || !ids.insert(&p.id)
            || p.label.is_empty()
            || p.label.len() > 100
            || !endpoint_allowed(&p.base_url)
            || p.api_key_env.is_empty()
            || p.api_key_env.len() > 96
            || !p
                .api_key_env
                .bytes()
                .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == b'_')
            || p.models.is_empty()
            || p.models.len() > 32
            || p.models.iter().any(|model| {
                model.is_empty() || model.len() > 128 || model.chars().any(char::is_control)
            })
            || af_budget::Money::from_usd_str(&p.reservation_usd)
                .map(|m| m.0 == 0)
                .unwrap_or(true)
        {
            return Err("invalid host provider configuration");
        }
    }
    Ok(config.providers)
}
fn configured() -> Result<Vec<Provider>, &'static str> {
    let path = std::env::var_os("AF_STUDIO_PROVIDER_CONFIG")
        .map(PathBuf::from)
        .unwrap_or_else(|| af_sandbox::user_dir().join("providers.json"));
    if !path.exists() {
        return Ok(Vec::new());
    }
    af_sandbox::registry::ensure_no_links(&path)
        .map_err(|_| "unsafe provider configuration path")?;
    if !path.is_file() {
        return Err("host provider configuration unavailable");
    }
    let mut raw = Vec::new();
    std::fs::File::open(path)
        .map_err(|_| "host provider configuration unavailable")?
        .take(65537)
        .read_to_end(&mut raw)
        .map_err(|_| "host provider configuration unreadable")?;
    decode(&raw)
}
pub fn catalogue() -> Value {
    let mut providers = vec![
        json!({"id":"scripted","label":"Локальный тест","models":["scripted"],
        "available":true,"kind":"scripted","accounting":"known_zero"}),
    ];
    let mut error = None;
    match configured() {
        Ok(config) => {
            for p in config {
                providers.push(json!({"id":p.id,"label":p.label,"models":p.models,
                "available":std::env::var(&p.api_key_env).is_ok_and(|v| !v.is_empty()),
                "kind":"openai-compatible","accounting":"durable_reservation"}));
            }
        }
        Err(reason) => error = Some(reason),
    }
    json!({"ok":true,"providers":providers,"error":error,"credentials_in_browser":false,
        "budget_enforced":true,"unknown_cost_policy":"hold_until_provider_evidence"})
}

pub fn select(
    id: Option<&str>,
    base_url: &str,
    browser_env: &str,
    model: &str,
    reservation_ceiling: Option<af_budget::Money>,
) -> Result<Box<dyn ChatBackend>, String> {
    if (id == Some("scripted") || id.is_none() && base_url == "scripted://local")
        && browser_env.is_empty()
    {
        return Ok(Box::new(af_llm::ScriptedBackend::echo()));
    }
    if !base_url.is_empty() || !browser_env.is_empty() {
        return Err(
            "select a host-configured provider ID; endpoint and credentials are host-owned".into(),
        );
    }
    let provider = configured()
        .map_err(str::to_string)?
        .into_iter()
        .find(|p| Some(p.id.as_str()) == id)
        .ok_or_else(|| "provider is not configured on this host".to_string())?;
    if !provider.models.iter().any(|m| m == model) {
        return Err("model is not approved for this provider".into());
    }
    if !std::env::var(&provider.api_key_env).is_ok_and(|v| !v.is_empty()) {
        return Err("provider credential is unavailable on host".into());
    }
    let amount = af_budget::Money::from_usd_str(&provider.reservation_usd)
        .map_err(|_| "invalid host reservation")?;
    if reservation_ceiling.is_some_and(|ceiling| amount.0 > ceiling.0) {
        return Err("host reservation exceeds the request ceiling".into());
    }
    let path = ledger_path();
    let state = af_budget::Ledger::open(&path, OWNER)
        .and_then(|l| l.snapshot())
        .map_err(|_| "create or inspect the durable budget before calling a provider")?;
    if state.has_hold() || state.send_checkpoint {
        return Err("budget HOLD: reconcile the original attempt before another send".into());
    }
    Ok(Box::new(AccountedBackend {
        backend: Box::new(af_llm::OpenAiBackend::new(
            &provider.id,
            &provider.base_url,
            &provider.api_key_env,
        )),
        ledger: path,
        amount,
        model: model.into(),
    }))
}

struct AccountedBackend {
    backend: Box<dyn ChatBackend>,
    ledger: PathBuf,
    amount: af_budget::Money,
    model: String,
}
impl AccountedBackend {
    fn run(
        &self,
        request: &ChatRequest,
        cancel: &Cancel,
        stream: Option<&mut dyn FnMut(&str)>,
    ) -> BackendOutcome {
        let refused = |reason: &str| BackendOutcome::Refused {
            sent: false,
            reason: reason.into(),
        };
        if cancel.is_cancelled() {
            return refused("cancelled before budget admission");
        }
        if request.model != self.model
            || request.max_output_tokens.is_none_or(|n| n == 0 || n > 4096)
            || request
                .messages
                .iter()
                .map(|m| m.content.len())
                .sum::<usize>()
                > 64 * 1024
        {
            return refused("request exceeds host provider scope");
        }
        let Some(attempt) = request.host_attempt.as_ref() else {
            return refused("paid sends require a host journal attempt binding");
        };
        if !identifier(&attempt.call_id) || !identifier(&attempt.session_id) {
            return refused("invalid host attempt binding");
        }
        let call = attempt.call_id.clone();
        let task = attempt.session_id.clone();
        let Ok(mut ledger) = af_budget::Ledger::open(&self.ledger, OWNER) else {
            return refused("durable budget unavailable");
        };
        if ledger.reserve(&call, &task, self.amount).is_err() {
            return refused("budget admission refused; inspect cap and holds");
        }
        if cancel.is_cancelled() {
            if ledger.release_not_sent(&call, &task).is_err() {
                return refused("pre-send reservation release failed; inspect budget");
            }
            return refused("cancelled before send");
        }
        if ledger.begin_send(&call, &task).is_err() {
            return refused("send checkpoint refused; inspect budget before retrying");
        }
        let outcome = match stream {
            Some(callback) => self.backend.chat_stream(request, cancel, callback),
            None => self.backend.chat(request, cancel),
        };
        match &outcome {
            BackendOutcome::Completed(result) => {
                let actual = result.usage.cost_usd.and_then(cost_money);
                if let Some(actual) = actual {
                    if ledger.settle(&call, &task, actual).is_ok() {
                        return outcome;
                    }
                }
                let _ = ledger.mark_unknown(&call, &task); // Sent checkpoint remains even if this write fails.
                BackendOutcome::Unknown { sent: true, detail: format!("provider response received but cost/settlement is unresolved; call {call} is on HOLD") }
            }
            BackendOutcome::Refused { sent: false, .. } => {
                if ledger.settle(&call, &task, af_budget::Money(0)).is_ok() {
                    outcome
                } else {
                    BackendOutcome::Unknown {
                        sent: false,
                        detail:
                            "pre-inference refusal observed but budget settlement is unresolved"
                                .into(),
                    }
                }
            }
            _ => {
                let _ = ledger.mark_unknown(&call, &task);
                BackendOutcome::Unknown { sent: true, detail: format!("provider attempt unresolved; call {call} is on HOLD and must not be replayed") }
            }
        }
    }
}
fn cost_money(cost: f64) -> Option<af_budget::Money> {
    let scaled = (cost * af_budget::NANODOLLARS_PER_USD as f64).ceil();
    (cost.is_finite() && cost >= 0.0 && scaled.is_finite() && scaled < u64::MAX as f64)
        .then_some(af_budget::Money(scaled as u64))
}
impl ChatBackend for AccountedBackend {
    fn name(&self) -> &str {
        self.backend.name()
    }
    fn chat(&self, request: &ChatRequest, cancel: &Cancel) -> BackendOutcome {
        self.run(request, cancel, None)
    }
    fn chat_stream(
        &self,
        request: &ChatRequest,
        cancel: &Cancel,
        callback: &mut dyn FnMut(&str),
    ) -> BackendOutcome {
        self.run(request, cancel, Some(callback))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;
    fn ledger() -> PathBuf {
        let directory = std::env::temp_dir().join(format!("af-provider-{}", af_entropy::token()));
        std::fs::create_dir(&directory).unwrap();
        let path = directory.join("ledger.jsonl");
        af_budget::Ledger::create(&path, OWNER, af_budget::Money(100_000_000), 16).unwrap();
        path
    }
    fn configured_backend(path: &Path, cost: Option<f64>) -> AccountedBackend {
        AccountedBackend {
            backend: Box::new(af_llm::ScriptedBackend::new(
                "provider",
                vec![BackendOutcome::Completed(af_llm::ChatResult {
                    content: "recorded answer".into(),
                    usage: af_llm::Usage {
                        tokens_in: Some(1),
                        tokens_out: Some(1),
                        cost_usd: cost,
                        latency_ms: Some(1),
                    },
                    truncated: false,
                    tool_calls: vec![],
                })],
            )),
            ledger: path.into(),
            amount: af_budget::Money(10_000_000),
            model: "model".into(),
        }
    }
    #[test]
    fn provider_send_is_durably_settled_and_missing_cost_latches_hold() {
        for cost in [Some(0.001), None] {
            let path = ledger();
            let backend = configured_backend(&path, cost);
            let mut request =
                ChatRequest::new("model", vec![af_llm::ChatMessage::new("user", "hello")]);
            request.max_output_tokens = Some(8);
            request.host_attempt = Some(af_llm::HostAttempt {
                session_id: "session1".into(),
                call_id: "call1".into(),
            });
            let result = backend.chat(&request, &Cancel::new());
            let snapshot = af_budget::Ledger::open(&path, OWNER)
                .unwrap()
                .snapshot()
                .unwrap();
            assert_eq!(snapshot.has_hold(), cost.is_none());
            assert_eq!(
                matches!(result, BackendOutcome::Completed(_)),
                cost.is_some()
            );
            if cost.is_none() {
                assert!(matches!(
                    backend.chat(&request, &Cancel::new()),
                    BackendOutcome::Refused { sent: false, .. }
                ));
            }
            std::fs::remove_dir_all(path.parent().unwrap()).unwrap();
        }
    }
    #[test]
    fn cancelled_and_wrong_model_calls_never_create_a_send() {
        let path = ledger();
        let backend = configured_backend(&path, Some(0.0));
        let request = ChatRequest::new("wrong", vec![]);
        assert!(matches!(
            backend.chat(&request, &Cancel::new()),
            BackendOutcome::Refused { sent: false, .. }
        ));
        assert!(af_budget::Ledger::open(&path, OWNER)
            .unwrap()
            .snapshot()
            .unwrap()
            .calls
            .is_empty());
        std::fs::remove_dir_all(path.parent().unwrap()).unwrap();
    }
    #[test]
    fn host_configuration_rejects_credential_urls_and_plaintext_remote_transport() {
        for value in [
            "https://user:secret@example.com/v1",
            "http://example.com/v1",
            "https://example.com/v1?token=x",
            "file:///tmp/key",
        ] {
            assert!(!endpoint_allowed(value));
        }
        assert!(endpoint_allowed("https://example.com/v1"));
        assert!(endpoint_allowed("http://127.0.0.1:11434/v1"));
        assert!(cost_money(f64::NAN).is_none());
        assert!(cost_money(-1.0).is_none());
    }
}
