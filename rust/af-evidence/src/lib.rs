//! Bounded, data-only verification reporting and separate host execution receipts.
//!
//! Deserializing a report never grants source-read, check-execution, publication,
//! or mutation authority. Hosts must keep their actual policy/approval gates.
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;

pub const VERSION: u32 = 1;
pub const MAX_WIRE_BYTES: usize = 262_144;
pub const MAX_ITEMS: usize = 256;
pub const MAX_COUNT: u64 = 1_000_000;
pub const MAX_SOURCE_BYTES: usize = 16 * 1024 * 1024;

#[derive(Debug, thiserror::Error)]
pub enum EvidenceError {
    #[error("invalid evidence: {0}")]
    Invalid(&'static str),
    #[error("invalid evidence JSON: {0}")]
    Json(#[from] serde_json::Error),
    #[error("requirement checker unavailable: {0}")]
    CheckerUnavailable(String),
}
type Result<T> = std::result::Result<T, EvidenceError>;
fn require(ok: bool, why: &'static str) -> Result<()> {
    if ok {
        Ok(())
    } else {
        Err(EvidenceError::Invalid(why))
    }
}
fn text(s: &str, max: usize) -> bool {
    !s.is_empty() && s.len() <= max && !s.chars().any(char::is_control)
}
fn digest(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}
pub fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

/// Exact host-snapshot identities; client-provided values are only labels.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceBinding {
    pub workspace_id: String,
    pub source_revision: String,
    pub inventory_sha256: String,
    pub runtime_id: String,
}
impl SourceBinding {
    pub fn validate(&self) -> Result<()> {
        require(
            text(&self.workspace_id, 256)
                && text(&self.source_revision, 256)
                && digest(&self.inventory_sha256)
                && text(&self.runtime_id, 256),
            "source binding",
        )
    }
}
#[derive(Debug, Copy, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Assessment {
    NotAssessed,
    NotApplicable,
    Passed,
    Failed,
    Unavailable,
}
/// Integer denominators are explicit. No confidence/probability is implied.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CheckSummary {
    pub status: Assessment,
    pub checked: u64,
    pub passed: u64,
    pub failed: u64,
    pub denominator: u64,
}
impl Default for CheckSummary {
    fn default() -> Self {
        Self {
            status: Assessment::NotAssessed,
            checked: 0,
            passed: 0,
            failed: 0,
            denominator: 0,
        }
    }
}
impl CheckSummary {
    pub fn validate(&self) -> Result<()> {
        require(
            self.denominator <= MAX_COUNT
                && self.checked <= self.denominator
                && self.passed.checked_add(self.failed) == Some(self.checked),
            "counter bounds",
        )?;
        require(
            match self.status {
                Assessment::Passed => {
                    self.denominator > 0
                        && self.checked == self.denominator
                        && self.passed == self.checked
                }
                Assessment::Failed => self.checked > 0 && self.failed > 0,
                Assessment::NotAssessed | Assessment::NotApplicable | Assessment::Unavailable => {
                    self.checked == 0
                }
            },
            "assessment/counter mismatch",
        )
    }
}
#[derive(Debug, Copy, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OmissionReason {
    Stale,
    Denied,
    Unreadable,
    Unsupported,
    Truncated,
    Omitted,
    BudgetExcluded,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Omission {
    pub source_id: String,
    pub reason: OmissionReason,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EvidenceCoverage {
    pub requested: u64,
    pub delivered_complete: u64,
    pub delivered_partial: u64,
    pub omissions: Vec<Omission>,
}
impl EvidenceCoverage {
    fn validate(&self) -> Result<()> {
        let omitted = u64::try_from(self.omissions.len())
            .map_err(|_| EvidenceError::Invalid("omission count"))?;
        require(
            self.requested <= MAX_COUNT
                && self.omissions.len() <= MAX_ITEMS
                && self
                    .delivered_complete
                    .checked_add(self.delivered_partial)
                    .and_then(|n| n.checked_add(omitted))
                    == Some(self.requested),
            "coverage bounds",
        )?;
        require(
            self.omissions.iter().all(|o| text(&o.source_id, 1024))
                && self
                    .omissions
                    .iter()
                    .map(|o| &o.source_id)
                    .collect::<BTreeSet<_>>()
                    .len()
                    == self.omissions.len(),
            "omission source",
        )
    }
}
/// Byte offsets in the exact UTF-8 source; end is exclusive.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Citation {
    pub source_id: String,
    pub node_id: String,
    pub source_revision: String,
    pub source_sha256: String,
    pub start_byte: u64,
    pub end_byte: u64,
    pub quote: String,
    pub quote_sha256: String,
}
impl Citation {
    fn validate(&self) -> Result<()> {
        require(
            text(&self.source_id, 1024)
                && text(&self.node_id, 1024)
                && text(&self.source_revision, 256)
                && digest(&self.source_sha256)
                && digest(&self.quote_sha256)
                && !self.quote.is_empty()
                && self.quote.len() <= 16384
                && self.end_byte <= MAX_SOURCE_BYTES as u64
                && self.end_byte > self.start_byte
                && self.end_byte.checked_sub(self.start_byte) == Some(self.quote.len() as u64)
                && sha256(self.quote.as_bytes()) == self.quote_sha256,
            "citation shape/digest",
        )
    }
}
#[derive(Debug, Copy, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Entailment {
    NotAssessed,
    Supported,
    Contradicted,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Claim {
    pub claim_id: String,
    pub text: String,
    pub citation_indices: Vec<u32>,
    pub entailment: Entailment,
    /// Data-only record of an independent scoped checker, never authority.
    pub independent_checker: Option<String>,
}
#[derive(Debug, Copy, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Publication {
    NotRequested,
    NotAuthorized,
    Pending,
    Published,
    Failed,
    Unknown,
}
/// Public result DTO. Even a well-formed `passed` field is untrusted on input.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VerificationReport {
    pub schema_version: u32,
    pub binding: SourceBinding,
    pub identifier_hygiene: CheckSummary,
    pub citation_validity: CheckSummary,
    pub evidence_coverage: EvidenceCoverage,
    pub syntax: CheckSummary,
    pub tests: CheckSummary,
    pub requirements: CheckSummary,
    pub model_critique: CheckSummary,
    pub publication: Publication,
    pub citations: Vec<Citation>,
    pub claims: Vec<Claim>,
}
impl VerificationReport {
    pub fn not_assessed(binding: SourceBinding) -> Result<Self> {
        binding.validate()?;
        Ok(Self {
            schema_version: VERSION,
            binding,
            identifier_hygiene: CheckSummary::default(),
            citation_validity: CheckSummary::default(),
            evidence_coverage: EvidenceCoverage {
                requested: 0,
                delivered_complete: 0,
                delivered_partial: 0,
                omissions: vec![],
            },
            syntax: CheckSummary::default(),
            tests: CheckSummary::default(),
            requirements: CheckSummary::default(),
            model_critique: CheckSummary::default(),
            publication: Publication::NotAuthorized,
            citations: vec![],
            claims: vec![],
        })
    }
    pub fn validate(&self) -> Result<()> {
        require(self.schema_version == VERSION, "schema version")?;
        self.binding.validate()?;
        for summary in [
            &self.identifier_hygiene,
            &self.citation_validity,
            &self.syntax,
            &self.tests,
            &self.requirements,
            &self.model_critique,
        ] {
            summary.validate()?;
        }
        self.evidence_coverage.validate()?;
        require(
            self.citations.len() <= MAX_ITEMS && self.claims.len() <= MAX_ITEMS,
            "item limit",
        )?;
        for citation in &self.citations {
            citation.validate()?;
        }
        let mut ids = BTreeSet::new();
        for claim in &self.claims {
            require(
                text(&claim.claim_id, 256)
                    && ids.insert(&claim.claim_id)
                    && text(&claim.text, 8192)
                    && claim.citation_indices.len() <= MAX_ITEMS,
                "claim bounds/identity",
            )?;
            let unique: BTreeSet<_> = claim.citation_indices.iter().collect();
            require(
                unique.len() == claim.citation_indices.len()
                    && claim
                        .citation_indices
                        .iter()
                        .all(|i| (*i as usize) < self.citations.len()),
                "claim citation index",
            )?;
            require(
                match (&claim.entailment, &claim.independent_checker) {
                    (Entailment::NotAssessed, None) => true,
                    (Entailment::Supported | Entailment::Contradicted, Some(id)) => {
                        text(id, 256) && !claim.citation_indices.is_empty()
                    }
                    _ => false,
                },
                "entailment requires explicit independent scoped checker",
            )?;
        }
        Ok(())
    }
}
/// Parse directly into typed structs: serde rejects duplicate and unknown fields,
/// bool-as-int, fractions, overflow, NaN/Infinity, trailing data and invalid enums.
/// This is validation of data, NEVER validation of actual execution or authority.
pub fn parse_report(json: &str) -> Result<VerificationReport> {
    require(json.len() <= MAX_WIRE_BYTES, "wire byte limit")?;
    let report: VerificationReport = serde_json::from_str(json)?;
    report.validate()?;
    Ok(report)
}
/// Compact deterministic struct-field order; no floating-point fields exist.
pub fn report_json(report: &VerificationReport) -> Result<String> {
    report.validate()?;
    let value = serde_json::to_string(report)?;
    require(value.len() <= MAX_WIRE_BYTES, "wire byte limit")?;
    Ok(value)
}

/// Non-deserializable host proof of the bytes actually delivered to the model.
/// Construct only at the trusted materializer after source/read-policy checks.
#[derive(Debug)]
pub struct DeliveredSource {
    source_id: String,
    node_id: String,
    revision: String,
    source_sha256: String,
    source_len: u64,
    start: u64,
    bytes: Vec<u8>,
}
impl DeliveredSource {
    pub fn from_host(
        source_id: &str,
        node_id: &str,
        revision: &str,
        full_current_source: &[u8],
        start: usize,
        end: usize,
    ) -> Result<Self> {
        require(
            text(source_id, 1024) && text(node_id, 1024) && text(revision, 256),
            "delivery identity",
        )?;
        require(
            full_current_source.len() <= MAX_SOURCE_BYTES
                && start < end
                && end <= full_current_source.len()
                && end - start <= MAX_WIRE_BYTES,
            "delivery bounds",
        )?;
        require(
            std::str::from_utf8(&full_current_source[start..end]).is_ok(),
            "delivery UTF-8 boundaries",
        )?;
        Ok(Self {
            source_id: source_id.into(),
            node_id: node_id.into(),
            revision: revision.into(),
            source_sha256: sha256(full_current_source),
            source_len: full_current_source.len() as u64,
            start: start as u64,
            bytes: full_current_source[start..end].to_vec(),
        })
    }
    pub fn is_complete(&self) -> bool {
        self.start == 0 && self.bytes.len() as u64 == self.source_len
    }
}
/// Exact quotes prove delivered source provenance only; they do not entail claims.
pub fn validate_citation(
    citation: &Citation,
    delivered: &DeliveredSource,
    current_revision: &str,
    current_source: &[u8],
) -> Result<()> {
    citation.validate()?;
    require(
        citation.source_id == delivered.source_id && citation.node_id == delivered.node_id,
        "citation source/node mismatch",
    )?;
    require(
        citation.source_revision == current_revision
            && delivered.revision == current_revision
            && citation.source_sha256 == sha256(current_source)
            && delivered.source_sha256 == citation.source_sha256,
        "stale source",
    )?;
    let relative = citation
        .start_byte
        .checked_sub(delivered.start)
        .ok_or(EvidenceError::Invalid("undelivered citation start"))?;
    let relative_end = citation
        .end_byte
        .checked_sub(delivered.start)
        .ok_or(EvidenceError::Invalid("undelivered citation end"))?;
    let start = usize::try_from(relative).map_err(|_| EvidenceError::Invalid("offset overflow"))?;
    let end =
        usize::try_from(relative_end).map_err(|_| EvidenceError::Invalid("offset overflow"))?;
    require(
        delivered.bytes.get(start..end) == Some(citation.quote.as_bytes()),
        "quote not in delivered span",
    )
}

/// Exact obligation and immutable check inventory supplied by trusted host config.
#[derive(Debug, Clone)]
pub struct RequirementScope {
    pub task_id: String,
    pub requirement_id: String,
    pub requirement_contract_sha256: String,
    pub checker_id: String,
    pub binding: SourceBinding,
    pub check_inventory_sha256: String,
    pub expected_checks: u64,
}
impl RequirementScope {
    fn validate(&self) -> Result<()> {
        self.binding.validate()?;
        require(
            text(&self.task_id, 256)
                && text(&self.requirement_id, 256)
                && digest(&self.requirement_contract_sha256)
                && text(&self.checker_id, 256)
                && digest(&self.check_inventory_sha256)
                && self.expected_checks > 0
                && self.expected_checks <= MAX_COUNT,
            "requirement scope",
        )
    }
}
#[derive(Debug, Copy, Clone, PartialEq, Eq)]
pub enum CheckKind {
    IndependentBehavioral,
    HumanReviewed,
    SyntaxOnly,
    ModelCritique,
    GenericProcess,
}
/// Trusted adapter output. No Deserialize implementation: never map HTTP JSON here.
#[derive(Debug)]
pub struct CheckExecution {
    pub task_id: String,
    pub requirement_contract_sha256: String,
    pub checker_id: String,
    pub binding: SourceBinding,
    pub requirement_id: String,
    pub check_inventory_sha256: String,
    pub kind: CheckKind,
    pub completed: bool,
    pub checked: u64,
    pub passed: u64,
    pub failed: u64,
    pub skipped: u64,
    pub evidence_sha256: String,
}
/// A trusted host adapter must execute/observe the declared requirement check.
/// Implementations must pin the independent oracle/inventory and recheck source
/// freshness after execution. A callback/host implementation is a trust boundary,
/// not a sandbox or cryptographic attestation of an honest host.
pub trait HostRequirementChecker {
    fn execute(&mut self, scope: &RequirementScope) -> Result<CheckExecution>;
}
/// Explicit refusal adapter for environments without independent check execution.
pub struct UnavailableChecker;
impl HostRequirementChecker for UnavailableChecker {
    fn execute(&mut self, _: &RequirementScope) -> Result<CheckExecution> {
        Err(EvidenceError::CheckerUnavailable(
            "no independent requirement execution adapter installed".into(),
        ))
    }
}
#[derive(Debug)]
pub struct HostRequirementReceipt {
    scope: RequirementScope,
    evidence_sha256: String,
}
impl HostRequirementReceipt {
    pub fn requirement_id(&self) -> &str {
        &self.scope.requirement_id
    }
    pub fn evidence_sha256(&self) -> &str {
        &self.evidence_sha256
    }
}
/// Only this execution path mints a receipt. Generic exit-zero is insufficient.
pub fn run_requirement_check(
    scope: &RequirementScope,
    checker: &mut dyn HostRequirementChecker,
) -> Result<HostRequirementReceipt> {
    scope.validate()?;
    let result = checker.execute(scope)?;
    require(
        result.task_id == scope.task_id
            && result.requirement_contract_sha256 == scope.requirement_contract_sha256
            && result.checker_id == scope.checker_id
            && result.binding == scope.binding
            && result.requirement_id == scope.requirement_id
            && result.check_inventory_sha256 == scope.check_inventory_sha256,
        "execution scope mismatch",
    )?;
    require(
        matches!(
            result.kind,
            CheckKind::IndependentBehavioral | CheckKind::HumanReviewed
        ),
        "non-behavioral check",
    )?;
    require(
        result.completed
            && result.checked == scope.expected_checks
            && result.passed == result.checked
            && result.failed == 0
            && result.skipped == 0
            && digest(&result.evidence_sha256),
        "incomplete/failed requirement execution",
    )?;
    Ok(HostRequirementReceipt {
        scope: scope.clone(),
        evidence_sha256: result.evidence_sha256,
    })
}
#[derive(Debug, PartialEq, Eq)]
pub enum CompletionDecision {
    RequirementsSatisfied,
    NotAssessed,
}
/// Completion only, never publication approval. Caller must pass current host
/// scopes; no DTO, UUID, model verdict or persisted report can substitute receipts.
pub fn completion_decision(
    current: &[RequirementScope],
    receipts: &[HostRequirementReceipt],
) -> Result<CompletionDecision> {
    require(
        current.len() <= MAX_ITEMS && receipts.len() <= MAX_ITEMS,
        "completion item limit",
    )?;
    if current.is_empty() {
        return Ok(CompletionDecision::NotAssessed);
    }
    let mut ids = BTreeSet::new();
    for scope in current {
        scope.validate()?;
        require(
            scope.binding == current[0].binding && scope.task_id == current[0].task_id,
            "mixed completion task/source scope",
        )?;
        require(ids.insert(&scope.requirement_id), "duplicate requirement")?;
        let matching: Vec<_> = receipts
            .iter()
            .filter(|r| r.scope.requirement_id == scope.requirement_id)
            .collect();
        if matching.len() != 1 {
            return Ok(CompletionDecision::NotAssessed);
        }
        let old = &matching[0].scope;
        if old.binding != scope.binding
            || old.task_id != scope.task_id
            || old.requirement_contract_sha256 != scope.requirement_contract_sha256
            || old.checker_id != scope.checker_id
            || old.expected_checks != scope.expected_checks
            || old.check_inventory_sha256 != scope.check_inventory_sha256
        {
            return Ok(CompletionDecision::NotAssessed);
        }
    }
    Ok(CompletionDecision::RequirementsSatisfied)
}

#[cfg(test)]
mod tests;
