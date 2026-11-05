/** Browser-safe native single-owner API client. Reports are data, never grants. */
import { AfProtocolError, bool, byteLength, ensure, exact, list, literal, oneOf, parseJson, record, sha256Label, strings, text, uint, validateBudgetSnapshot, validateGraphRead, validateHealth, validateSessionList, validateSessionSummary } from "./wire.js";
import type { BudgetSnapshotDto, GraphReadDto, HealthDto, SessionListDto, SessionSummaryDto } from "./wire.js";
import { validateVerificationReport, type VerificationReport } from "./evidence.js";
export { AfProtocolError } from "./wire.js";
export { WireError, validateBudgetSnapshot, validateGraphRead, validateHealth, validateSessionList, validateSessionSummary } from "./wire.js";
export { parseVerificationReport } from "./evidence.js";
export type { BudgetSnapshotDto, GraphFileDto, GraphLayer, GraphReadDto, HealthDto, SessionListDto, SessionListItem, SessionSummaryDto } from "./wire.js";
export type { VerificationReport, SourceBinding, CheckSummary, Assessment, Citation, Claim, Omission, OmissionReason } from "./evidence.js";

export interface AfClientOptions { baseUrl: string; token?: string; fetchImpl?: typeof fetch; maxResponseBytes?: number; timeoutMs?: number }
export interface SessionCreated { ok: true; session_id: string; path: string }
/** Historical name kept: a validated session summary DTO. */
export type SessionSnapshot = SessionSummaryDto;
export interface Capabilities { schema_version: 1; verification_schema_version: 1; authority: "single_owner"; requirement_checker: "unavailable"; verification_report_authorizes_execution: false; publication_recovery: "host_cli_preview_digest"; unknown_replay: false }
export interface BackendSelection { provider_id: string; model: string }
export interface EditJourneyInput { backend: BackendSelection; root_id: string; file: string; replacement: string; goal: string; session_id?: string; host_approved: true }
export interface JourneysReport { ok: boolean; verified: false; refusal: Record<string, unknown>; session_id: string; edits: number; verification: "not_assessed"; status: "changed_unverified" | "answered_unverified" | "paused" }
export interface JobsStatus { ok: true; jobs: Record<string, unknown>[]; unresolved_unknown_ids: string[] }
export interface RequestContextSource { root_id: string; path: string; sha256: string; start_line: number; end_line: number; kind: "request_context"; verification: "not_assessed" }
export interface ChatInput { backend: BackendSelection; prompt: string; session_id?: string; root_id?: string }
export type ChatEvent = { type: "delta"; text: string } | {
  type: "final" | "error"; ok: boolean; content: string; tokens_in: number | null; tokens_out: number | null;
  cost_usd: number | null; truncated: boolean; verification: "not_assessed"; verification_report: VerificationReport;
  coverage_scope: "selected_request_excerpts"; sources: RequestContextSource[]; unknown: boolean; error: string;
} | { type: "error"; unknown: boolean; error: string; refused?: boolean; sent?: boolean };
export class AfHttpError extends Error {
  constructor(public readonly status: number) { super(`af serve HTTP ${status}`); this.name = "AfHttpError"; }
}
/** Unknown transport outcomes must be inspected on the host; never auto-retry. */
export class AfTransportError extends Error {
  readonly outcome = "unknown";
  readonly retryAllowed = false;
  constructor(message = "transport outcome unknown; inspect host state before continuing") { super(message); this.name = "AfTransportError"; }
}
function bodyText(value: unknown, max: number): string { ensure(typeof value === "string" && byteLength(value) <= max, "text byte limit"); return value; }
function selection(value: unknown): BackendSelection {
  const b = exact(value, ["provider_id", "model"]);
  return { provider_id: text(b.provider_id, 128), model: text(b.model, 128) };
}
function identifier(value: string): string { return encodeURIComponent(text(value)); }
function secureBase(value: string): string {
  const url = new URL(value);
  ensure(!url.username && !url.password && !url.search && !url.hash && !value.includes("?") && !value.includes("#"), "URL credentials/query/fragment forbidden");
  ensure(url.protocol === "https:" || url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname), "HTTPS or loopback HTTP required");
  return url.href.replace(/\/+$/, "");
}
async function boundedBody(response: Response, limit: number): Promise<string> {
  const length = response.headers.get("content-length");
  if (length !== null && (!/^\d+$/.test(length) || Number(length) > limit)) {
    await response.body?.cancel().catch(() => undefined); throw new AfProtocolError("response body limit");
  }
  ensure(response.body !== null, "missing response body");
  const reader = response.body.getReader();
  const parts: Uint8Array[] = []; let size = 0;
  try {
    while (true) { const next = await reader.read(); if (next.done) break; size += next.value.byteLength; ensure(size <= limit, "response body limit"); parts.push(next.value); }
  } finally { await reader.cancel().catch(() => undefined); reader.releaseLock(); }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const part of parts) { bytes.set(part, offset); offset += part.byteLength; }
  try { return new TextDecoder("utf-8", { fatal: true }).decode(bytes); } catch { throw new AfProtocolError("response is not UTF-8"); }
}
async function chatEvent(value: unknown): Promise<ChatEvent> {
  const item = record(value);
  if (item.type === "delta") { exact(item, ["type", "text"]); return { type: "delta", text: bodyText(item.text, 8192) }; }
  if (Object.hasOwn(item, "verification_report")) {
    exact(item, ["type", "ok", "content", "tokens_in", "tokens_out", "cost_usd", "truncated", "verification", "verification_report", "coverage_scope", "sources", "unknown", "error"]);
    const eventType = oneOf(item.type, ["final", "error"]), ok = bool(item.ok), unknown = bool(item.unknown);
    const cost = item.cost_usd;
    ensure(cost === null || typeof cost === "number" && Number.isFinite(cost) && cost >= 0, "invalid cost");
    ensure(eventType !== "final" || ok && !unknown && cost !== null, "final cannot hide unknown outcome");
    ensure(ok === (eventType === "final") && (cost !== null || unknown), "inconsistent terminal outcome");
    const sources = list(item.sources, 6).map(value => {
      const s = exact(value, ["root_id", "path", "sha256", "start_line", "end_line", "kind", "verification"]);
      return { root_id: text(s.root_id), path: text(s.path, 1024), sha256: sha256Label(s.sha256), start_line: uint(s.start_line), end_line: uint(s.end_line), kind: literal(s.kind, "request_context"), verification: literal(s.verification, "not_assessed") };
    });
    return { type: eventType, ok, content: bodyText(item.content, 128 * 1024), tokens_in: item.tokens_in === null ? null : uint(item.tokens_in, Number.MAX_SAFE_INTEGER), tokens_out: item.tokens_out === null ? null : uint(item.tokens_out, Number.MAX_SAFE_INTEGER), cost_usd: cost, truncated: bool(item.truncated), verification: literal(item.verification, "not_assessed"), verification_report: await validateVerificationReport(item.verification_report), coverage_scope: literal(item.coverage_scope, "selected_request_excerpts"), sources, unknown, error: bodyText(item.error, 8192) };
  }
  ensure(item.type === "error", "unsupported stream event");
  ensure(Object.keys(item).every(key => ["type", "error", "unknown", "refused", "sent"].includes(key)) && Object.hasOwn(item, "unknown"), "invalid error event");
  return { type: "error", unknown: bool(item.unknown), error: bodyText(item.error, 8192), ...(item.sent === undefined ? {} : { sent: bool(item.sent) }), ...(item.refused === undefined ? {} : { refused: bool(item.refused) }) };
}

export class AfClient {
  private readonly baseUrl: string;
  private readonly headers: Record<string, string>;
  private readonly fetchImpl: typeof fetch;
  private readonly maxResponseBytes: number;
  private readonly timeoutMs: number;
  constructor(options: AfClientOptions) {
    this.baseUrl = secureBase(options.baseUrl);
    this.headers = options.token === undefined ? {} : { Authorization: `Bearer ${text(options.token, 8192)}` };
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch;
    this.maxResponseBytes = options.maxResponseBytes === undefined ? 1_048_576 : uint(options.maxResponseBytes, 4_194_304);
    ensure(this.maxResponseBytes > 0, "positive response limit required");
    this.timeoutMs = options.timeoutMs === undefined ? 30_000 : uint(options.timeoutMs, 120_000);
    ensure(this.timeoutMs > 0, "positive timeout required");
  }
  private async request(path: string, init: RequestInit = {}): Promise<Response> {
    let response: Response;
    const timeout = AbortSignal.timeout(this.timeoutMs);
    const signal = init.signal ? AbortSignal.any([timeout, init.signal]) : timeout;
    try { response = await this.fetchImpl(`${this.baseUrl}${path}`, { ...init, signal, redirect: "error", cache: "no-store", credentials: "omit", headers: { "Content-Type": "application/json", ...this.headers, ...init.headers } }); }
    catch { throw new AfTransportError(); }
    if (!response.ok) { await response.body?.cancel().catch(() => undefined); throw new AfHttpError(response.status); }
    if (response.redirected) { await response.body?.cancel().catch(() => undefined); throw new AfProtocolError("redirected response refused"); }
    return response;
  }
  private async json(path: string, init?: RequestInit): Promise<unknown> {
    const response = await this.request(path, init);
    if (!/^application\/json(?:\s*;|$)/i.test(response.headers.get("content-type") ?? "")) { await response.body?.cancel().catch(() => undefined); throw new AfProtocolError("expected application/json"); }
    try { return parseJson(await boundedBody(response, this.maxResponseBytes), this.maxResponseBytes); }
    catch (error) { if (error instanceof AfProtocolError) throw error; throw new AfTransportError(); }
  }
  async health(): Promise<HealthDto> { return validateHealth(await this.json("/health")); }
  async capabilities(): Promise<Capabilities> {
    const r = exact(await this.json("/v1/capabilities"), ["schema_version", "verification_schema_version", "authority", "requirement_checker", "verification_report_authorizes_execution", "publication_recovery", "unknown_replay"]);
    return { schema_version: literal(r.schema_version, 1), verification_schema_version: literal(r.verification_schema_version, 1), authority: literal(r.authority, "single_owner"), requirement_checker: literal(r.requirement_checker, "unavailable"), verification_report_authorizes_execution: literal(r.verification_report_authorizes_execution, false), publication_recovery: literal(r.publication_recovery, "host_cli_preview_digest"), unknown_replay: literal(r.unknown_replay, false) };
  }
  async createSession(id?: string, rootId?: string): Promise<SessionCreated> {
    const body = { ...(id === undefined ? {} : { id: text(id) }), ...(rootId === undefined ? {} : { root_id: text(rootId) }) };
    const r = exact(await this.json("/v1/sessions", { method: "POST", body: JSON.stringify(body) }), ["ok", "session_id", "path"]);
    return { ok: literal(r.ok, true), session_id: text(r.session_id), path: text(r.path, 4096) };
  }
  async readSession(sessionId: string): Promise<SessionSnapshot> {
    return validateSessionSummary(await this.json(`/v1/sessions/${identifier(sessionId)}`));
  }
  /** Durable host journal list; entries are metadata without authority. */
  async listSessions(): Promise<SessionListDto> { return validateSessionList(await this.json("/v1/sessions")); }
  /** Browser-authored history is advisory user_input only. No terminal/approval events. */
  async appendUserInput(sessionId: string, payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    record(payload); const body = JSON.stringify({ event_type: "user_input", payload });
    ensure(byteLength(body) <= 262144, "request body limit");
    const r = record(await this.json(`/v1/sessions/${identifier(sessionId)}/events`, { method: "POST", body }));
    literal(r.event_type, "user_input"); uint(r.seq, Number.MAX_SAFE_INTEGER); record(r.payload);
    return r;
  }
  async runEditJourney(input: EditJourneyInput): Promise<JourneysReport> {
    const body = record(input); ensure(Object.keys(body).every(key => ["backend", "root_id", "file", "replacement", "goal", "session_id", "host_approved"].includes(key)), "unknown edit request field");
    const request = { backend: selection(input.backend), root_id: text(input.root_id), file: text(input.file, 512), replacement: bodyText(input.replacement, 512 * 1024), goal: bodyText(input.goal, 8192), host_approved: literal(input.host_approved, true), ...(input.session_id === undefined ? {} : { session_id: text(input.session_id) }) };
    ensure(request.goal.length > 0, "empty goal");
    const r = exact(await this.json("/v1/edit-journeys", { method: "POST", body: JSON.stringify(request) }), ["ok", "verified", "refusal", "session_id", "edits", "verification", "status"]);
    return { ok: bool(r.ok), verified: literal(r.verified, false), refusal: record(r.refusal), session_id: text(r.session_id), edits: uint(r.edits), verification: literal(r.verification, "not_assessed"), status: oneOf(r.status, ["changed_unverified", "answered_unverified", "paused"]) };
  }
  /** Server owns the job store; callers cannot supply a filesystem path. */
  async jobStatus(): Promise<JobsStatus> {
    const r = exact(await this.json("/v1/jobs"), ["ok", "jobs", "unresolved_unknown_ids"]);
    return { ok: literal(r.ok, true), jobs: list(r.jobs, 10000).map(record), unresolved_unknown_ids: strings(r.unresolved_unknown_ids, 10000) };
  }
  /** af-budget snapshot. Holds, reservations and unknown charges stay explicit. */
  async readBudget(): Promise<BudgetSnapshotDto> { return validateBudgetSnapshot(await this.json("/v1/budget")); }
  /** Structural graph for a granted root: layers and symbols, never a read proof. */
  async readGraph(rootId: string): Promise<GraphReadDto> { return validateGraphRead(await this.json(`/v1/graph/${identifier(rootId)}`)); }
  /** Single bounded stream. Disconnect/abort never auto-resubmits a model call. */
  async *chat(input: ChatInput, options: { signal?: AbortSignal } = {}): AsyncGenerator<ChatEvent> {
    const body = record(input); ensure(Object.keys(body).every(key => ["backend", "prompt", "session_id", "root_id"].includes(key)), "unknown chat request field");
    const backend = selection(input.backend), prompt = bodyText(input.prompt, 64 * 1024);
    ensure(prompt.length > 0, "empty prompt");
    ensure(backend.provider_id === "scripted" || input.session_id !== undefined, "host provider requires durable session");
    const request = { backend, prompt, ...(input.session_id === undefined ? {} : { session_id: text(input.session_id) }), ...(input.root_id === undefined ? {} : { root_id: text(input.root_id) }) };
    const abort = new AbortController(); const onAbort = (): void => abort.abort();
    if (options.signal?.aborted) abort.abort();
    options.signal?.addEventListener("abort", onAbort, { once: true });
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
    try {
      const response = await this.request("/v1/chat/stream", { method: "POST", body: JSON.stringify(request), signal: abort.signal });
      ensure(/^text\/event-stream(?:\s*;|$)/i.test(response.headers.get("content-type") ?? "") && response.body !== null, "expected SSE response");
      reader = response.body.getReader(); const decoder = new TextDecoder("utf-8", { fatal: true });
      let buffer = "", bytes = 0, count = 0;
      while (true) {
        const next = await reader.read();
        if (next.done) throw new AfTransportError("stream ended without a terminal outcome; do not replay");
        bytes += next.value.byteLength; ensure(bytes <= this.maxResponseBytes, "stream total byte limit");
        buffer += decoder.decode(next.value, { stream: true }); ensure(byteLength(buffer) <= 524288, "stream event byte limit");
        while (true) {
          const match = /\r?\n\r?\n/.exec(buffer); if (match === null) break;
          const frame = buffer.slice(0, match.index); buffer = buffer.slice(match.index + match[0].length);
          const data = frame.split(/\r?\n/).filter(line => line.startsWith("data:")).map(line => line.slice(5).replace(/^ /, "")).join("\n");
          if (!data) continue; ensure(++count <= 256, "stream event count limit");
          const event = await chatEvent(parseJson(data, 524288)); yield event;
          if (event.type !== "delta") return;
        }
      }
    } catch (error) {
      if (error instanceof AfProtocolError || error instanceof AfHttpError || error instanceof AfTransportError) throw error;
      throw new AfTransportError();
    } finally {
      abort.abort(); options.signal?.removeEventListener("abort", onAbort);
      await reader?.cancel().catch(() => undefined); reader?.releaseLock();
    }
  }
}
