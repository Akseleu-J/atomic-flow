import { byteLength, ensure, exact, list, oneOf, parseJson, sha256Label, text, uint } from "./wire.js";

export type Assessment = "not_assessed" | "not_applicable" | "passed" | "failed" | "unavailable";
export interface CheckSummary { status: Assessment; checked: number; passed: number; failed: number; denominator: number }
export interface SourceBinding { workspace_id: string; source_revision: string; inventory_sha256: string; runtime_id: string }
export type OmissionReason = "stale" | "denied" | "unreadable" | "unsupported" | "truncated" | "omitted" | "budget_excluded";
export interface Omission { source_id: string; reason: OmissionReason }
export interface Citation { source_id: string; node_id: string; source_revision: string; source_sha256: string; start_byte: number; end_byte: number; quote: string; quote_sha256: string }
export interface Claim { claim_id: string; text: string; citation_indices: number[]; entailment: "not_assessed" | "supported" | "contradicted"; independent_checker: string | null }
export interface VerificationReport {
  schema_version: 1; binding: SourceBinding;
  identifier_hygiene: CheckSummary; citation_validity: CheckSummary;
  evidence_coverage: { requested: number; delivered_complete: number; delivered_partial: number; omissions: Omission[] };
  syntax: CheckSummary; tests: CheckSummary; requirements: CheckSummary; model_critique: CheckSummary;
  publication: "not_requested" | "not_authorized" | "pending" | "published" | "failed" | "unknown";
  citations: Citation[]; claims: Claim[];
}
export const MAX_VERIFICATION_BYTES = 262_144;
function check(value: unknown): CheckSummary {
  const item = exact(value, ["status", "checked", "passed", "failed", "denominator"]);
  const status = oneOf(item.status, ["not_assessed", "not_applicable", "passed", "failed", "unavailable"]);
  const checked = uint(item.checked), passed = uint(item.passed), failed = uint(item.failed), denominator = uint(item.denominator);
  ensure(checked <= denominator && passed + failed === checked, "counter bounds");
  ensure(status === "passed" ? denominator > 0 && checked === denominator && passed === checked :
    status === "failed" ? checked > 0 && failed > 0 : checked === 0, "assessment/counter mismatch");
  return { status, checked, passed, failed, denominator };
}
async function quoteDigest(quote: string): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(quote));
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
}
/** Validates wire data only. Even passed/published reports grant NO runtime authority. */
export async function parseVerificationReport(raw: string): Promise<VerificationReport> {
  return validateVerificationReport(parseJson(raw, MAX_VERIFICATION_BYTES, true));
}
export async function validateVerificationReport(value: unknown): Promise<VerificationReport> {
  ensure(byteLength(JSON.stringify(value)) <= MAX_VERIFICATION_BYTES, "report byte limit");
  const r = exact(value, ["schema_version", "binding", "identifier_hygiene", "citation_validity", "evidence_coverage", "syntax", "tests", "requirements", "model_critique", "publication", "citations", "claims"]);
  ensure(r.schema_version === 1, "unsupported verification schema");
  const b = exact(r.binding, ["workspace_id", "source_revision", "inventory_sha256", "runtime_id"]);
  const binding = { workspace_id: text(b.workspace_id), source_revision: text(b.source_revision), inventory_sha256: sha256Label(b.inventory_sha256), runtime_id: text(b.runtime_id) };
  const e = exact(r.evidence_coverage, ["requested", "delivered_complete", "delivered_partial", "omissions"]);
  const omissions = list(e.omissions).map(value => {
    const o = exact(value, ["source_id", "reason"]);
    return { source_id: text(o.source_id, 1024), reason: oneOf(o.reason, ["stale", "denied", "unreadable", "unsupported", "truncated", "omitted", "budget_excluded"]) };
  });
  const evidence_coverage = { requested: uint(e.requested), delivered_complete: uint(e.delivered_complete), delivered_partial: uint(e.delivered_partial), omissions };
  ensure(evidence_coverage.delivered_complete + evidence_coverage.delivered_partial + omissions.length === evidence_coverage.requested && new Set(omissions.map(o => o.source_id)).size === omissions.length, "coverage bounds");
  const citations: Citation[] = [];
  for (const value of list(r.citations)) {
    const c = exact(value, ["source_id", "node_id", "source_revision", "source_sha256", "start_byte", "end_byte", "quote", "quote_sha256"]);
    ensure(typeof c.quote === "string" && c.quote.length > 0 && byteLength(c.quote) <= 16384, "quote bounds");
    const citation = { source_id: text(c.source_id, 1024), node_id: text(c.node_id, 1024), source_revision: text(c.source_revision), source_sha256: sha256Label(c.source_sha256), start_byte: uint(c.start_byte, 16 * 1024 * 1024), end_byte: uint(c.end_byte, 16 * 1024 * 1024), quote: c.quote, quote_sha256: sha256Label(c.quote_sha256) };
    ensure(citation.end_byte > citation.start_byte && citation.end_byte - citation.start_byte === byteLength(citation.quote) && await quoteDigest(citation.quote) === citation.quote_sha256, "citation shape/digest");
    citations.push(citation);
  }
  const ids = new Set<string>();
  const claims: Claim[] = list(r.claims).map(value => {
    const c = exact(value, ["claim_id", "text", "citation_indices", "entailment", "independent_checker"]);
    const claim_id = text(c.claim_id), claimText = text(c.text, 8192);
    ensure(!ids.has(claim_id), "duplicate claim id"); ids.add(claim_id);
    const citation_indices = list(c.citation_indices).map(value => uint(value, 0xffffffff));
    ensure(new Set(citation_indices).size === citation_indices.length && citation_indices.every(i => i < citations.length), "claim citation index");
    const entailment = oneOf(c.entailment, ["not_assessed", "supported", "contradicted"]);
    const independent_checker = c.independent_checker === null ? null : text(c.independent_checker);
    ensure(entailment === "not_assessed" ? independent_checker === null : independent_checker !== null && citation_indices.length > 0, "independent checker required");
    return { claim_id, text: claimText, citation_indices, entailment, independent_checker };
  });
  return { schema_version: 1, binding, identifier_hygiene: check(r.identifier_hygiene), citation_validity: check(r.citation_validity), evidence_coverage, syntax: check(r.syntax), tests: check(r.tests), requirements: check(r.requirements), model_critique: check(r.model_critique), publication: oneOf(r.publication, ["not_requested", "not_authorized", "pending", "published", "failed", "unknown"]), citations, claims };
}
