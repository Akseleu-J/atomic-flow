/** Bounded JSON parsing. Reject duplicate keys before JSON.parse can erase them. */
export class AfProtocolError extends Error {
  constructor(message: string) { super(message); this.name = "AfProtocolError"; }
}
/** Field-level wire violation. Extends AfProtocolError so existing handlers keep working. */
export class WireError extends AfProtocolError {
  readonly field: string;
  constructor(field: string, message: string) {
    super(`${field}: ${message}`);
    this.name = "WireError";
    this.field = field;
  }
}
export const byteLength = (value: string): number => new TextEncoder().encode(value).length;
export function ensure(ok: unknown, message: string): asserts ok {
  if (!ok) throw new AfProtocolError(message);
}
export function record(value: unknown): Record<string, unknown> {
  ensure(value !== null && typeof value === "object" && !Array.isArray(value), "expected object");
  return value as Record<string, unknown>;
}
export function exact(value: unknown, keys: readonly string[]): Record<string, unknown> {
  const item = record(value);
  ensure(Object.keys(item).length === keys.length && keys.every(key => Object.hasOwn(item, key)), "unknown or missing fields");
  return item;
}
export function text(value: unknown, max = 256): string {
  ensure(typeof value === "string" && value.length > 0 && byteLength(value) <= max && !/[\u0000-\u001f\u007f-\u009f]/u.test(value), "invalid bounded text");
  return value;
}
export function uint(value: unknown, max = 1_000_000): number {
  ensure(typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && !Object.is(value, -0) && value <= max, "invalid unsigned integer");
  return value;
}
export function list(value: unknown, max = 256): unknown[] {
  ensure(Array.isArray(value) && value.length <= max, "invalid bounded array");
  return value;
}
export function strings(value: unknown, max = 256): string[] { return list(value, max).map(v => text(v)); }
export function oneOf<T extends string>(value: unknown, choices: readonly T[]): T {
  ensure(typeof value === "string" && choices.some(choice => choice === value), "invalid enum");
  return value as T;
}
export function sha256Label(value: unknown): string {
  ensure(typeof value === "string" && /^[a-f0-9]{64}$/.test(value), "invalid SHA256");
  return value;
}
export function bool(value: unknown): boolean { ensure(typeof value === "boolean", "expected boolean"); return value; }
export function literal<T extends string | boolean | number>(value: unknown, expected: T): T { ensure(value === expected, "unexpected protocol value"); return expected; }
export function nullableText(value: unknown, max = 256): string | null { return value === null ? null : text(value, max); }
/** Bounded label that may be empty: graph nodes emit "" when no symbol was extracted. */
export function label(value: unknown, max = 256): string {
  ensure(typeof value === "string" && value.length <= max && !/[\u0000-\u001f\u007f-\u009f]/u.test(value), "invalid bounded label");
  return value;
}
/** Required fields must be present; anything outside required/optional is a WireError. */
export function shape(value: unknown, required: readonly string[], optional: readonly string[] = []): Record<string, unknown> {
  const item = record(value);
  const unknownKeys = Object.keys(item).filter(key => !required.includes(key) && !optional.includes(key)).sort();
  if (unknownKeys.length > 0) throw new WireError(unknownKeys.join(","), "unknown fields");
  const missingKeys = required.filter(key => !Object.hasOwn(item, key)).sort();
  if (missingKeys.length > 0) throw new WireError(missingKeys.join(","), "missing required fields");
  return item;
}

export function parseJson(raw: string, maxBytes: number, integersOnly = false): unknown {
  ensure(byteLength(raw) <= maxBytes, "JSON byte limit exceeded");
  let pos = 0, nodes = 0;
  const ws = (): void => { while (/[ \t\r\n]/.test(raw[pos] ?? "x")) pos++; };
  const string = (): string => {
    ensure(raw[pos] === '"', "expected JSON string");
    const start = pos++;
    while (pos < raw.length) {
      const char = raw[pos++];
      if (char === '"') {
        const result: unknown = JSON.parse(raw.slice(start, pos));
        ensure(typeof result === "string" && !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(result), "invalid Unicode scalar");
        return result;
      }
      if (char === "\\") pos++;
    }
    throw new AfProtocolError("unterminated JSON string");
  };
  const value = (depth: number, integerContext = integersOnly): unknown => {
    ensure(depth <= 64 && ++nodes <= 100_000, "JSON complexity limit exceeded");
    ws(); const char = raw[pos];
    if (char === '"') return string();
    if (char === "{") {
      pos++; ws(); const result: Record<string, unknown> = Object.create(null);
      if (raw[pos] === "}") { pos++; return result; }
      while (true) {
        ws(); const key = string(); ws();
        ensure(!Object.hasOwn(result, key), "duplicate JSON key");
        ensure(raw[pos++] === ":", "expected colon"); result[key] = value(depth + 1, integerContext || key === "verification_report"); ws();
        if (raw[pos] === "}") { pos++; return result; }
        ensure(raw[pos++] === ",", "expected comma");
      }
    }
    if (char === "[") {
      pos++; ws(); const result: unknown[] = [];
      if (raw[pos] === "]") { pos++; return result; }
      while (true) {
        result.push(value(depth + 1, integerContext)); ws();
        if (raw[pos] === "]") { pos++; return result; }
        ensure(raw[pos++] === ",", "expected comma");
      }
    }
    for (const [token, output] of [["true", true], ["false", false], ["null", null]] as const) {
      if (raw.startsWith(token, pos)) { pos += token.length; return output; }
    }
    const token = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(raw.slice(pos))?.[0];
    ensure(token !== undefined, "invalid JSON value");
    if (integerContext) ensure(/^(?:0|[1-9]\d*)$/.test(token), "integer JSON lexeme required");
    const output = Number(token);
    ensure(Number.isFinite(output), "nonfinite JSON number");
    pos += token.length; return output;
  };
  try {
    const result = value(0); ws(); ensure(pos === raw.length, "trailing JSON data"); return result;
  } catch (error) {
    if (error instanceof AfProtocolError) throw error;
    throw new AfProtocolError("invalid JSON encoding");
  }
}

// ── Validated af serve response shapes ─────────────────────────────────────
// Every response a caller renders passes one of these validators first.
// Unknown/missing fields throw WireError naming the fields; bad values throw
// AfProtocolError. Nothing unvalidated reaches a typed surface.

export interface HealthDto { ok: true; version: string; kernel: "atomic-flow-rust" }
export function validateHealth(value: unknown): HealthDto {
  const r = shape(value, ["ok", "version", "kernel"]);
  return { ok: literal(r.ok, true), version: text(r.version), kernel: literal(r.kernel, "atomic-flow-rust") };
}

export interface SessionListItem { session_id: string; created_at: string | null; project: string | null }
export interface SessionListDto { ok: true; sessions: SessionListItem[] }
/** Items carry `session_id` or `id`; at least one bounded identifier is required. */
export function validateSessionList(value: unknown): SessionListDto {
  const r = shape(value, ["ok", "sessions"]);
  const sessions = list(r.sessions, 1000).map(entry => {
    const s = shape(entry, [], ["session_id", "id", "created_at", "project"]);
    const bySessionId = Object.hasOwn(s, "session_id");
    if (!bySessionId && !Object.hasOwn(s, "id")) throw new WireError("session_id", "missing session identifier");
    return {
      session_id: text(bySessionId ? s.session_id : s.id),
      created_at: Object.hasOwn(s, "created_at") ? nullableText(s.created_at, 128) : null,
      project: Object.hasOwn(s, "project") ? nullableText(s.project, 256) : null,
    };
  });
  return { ok: literal(r.ok, true), sessions };
}

export interface SessionSummaryDto {
  ok: true; session_id: string; events: number; incomplete_tail: boolean;
  pending_call_ids: string[]; unresolved_call_ids: string[]; root_id: string | null;
}
/** Call identifiers are strings on the host journal wire (journal call_id labels). */
export function validateSessionSummary(value: unknown): SessionSummaryDto {
  const r = shape(value, ["ok", "session_id", "events", "incomplete_tail", "pending_call_ids", "unresolved_call_ids", "root_id"]);
  return { ok: literal(r.ok, true), session_id: text(r.session_id), events: uint(r.events, Number.MAX_SAFE_INTEGER), incomplete_tail: bool(r.incomplete_tail), pending_call_ids: strings(r.pending_call_ids), unresolved_call_ids: strings(r.unresolved_call_ids), root_id: nullableText(r.root_id) };
}

/** Exact nanodollar decimal from af-budget Money::usd_string(); floats never cross the wire. */
function usdAmount(value: unknown): string {
  ensure(typeof value === "string" && value.length <= 32 && /^\d+(?:\.\d{1,9})?$/.test(value), "invalid USD decimal");
  return value;
}
function usdAmountOrUnknown(value: unknown): string { return value === "unknown" ? "unknown" : usdAmount(value); }

export type BudgetSnapshotDto = {
  state: "ok" | "hold";
  committed_usd: string; committed_basis: "known_spend_plus_reservations";
  spent_usd_known: string | null; reserved_usd: string | null; available_usd: string | null;
  unknown_cost_present: boolean; cap_usd: string; calls: number; send_hold: boolean;
  enforced: true; provider_mode: "host_configured_or_scripted";
} | {
  state: "missing" | "unreadable";
  enforced: true; provider_mode: "host_configured_or_scripted";
};
export function validateBudgetSnapshot(value: unknown): BudgetSnapshotDto {
  const item = record(value);
  if (!Object.hasOwn(item, "state")) throw new WireError("state", "missing required fields");
  const state = oneOf(item.state, ["ok", "hold", "missing", "unreadable"]);
  if (state === "missing" || state === "unreadable") {
    const r = shape(item, ["state", "enforced", "provider_mode"]);
    return { state, enforced: literal(r.enforced, true), provider_mode: literal(r.provider_mode, "host_configured_or_scripted") };
  }
  const r = shape(item, ["state", "committed_usd", "committed_basis", "spent_usd_known", "reserved_usd", "unknown_cost_present", "available_usd", "cap_usd", "calls", "send_hold", "enforced", "provider_mode"]);
  return {
    state,
    committed_usd: usdAmountOrUnknown(r.committed_usd),
    committed_basis: literal(r.committed_basis, "known_spend_plus_reservations"),
    spent_usd_known: r.spent_usd_known === null ? null : usdAmount(r.spent_usd_known),
    reserved_usd: r.reserved_usd === null ? null : usdAmount(r.reserved_usd),
    available_usd: r.available_usd === null ? null : usdAmount(r.available_usd),
    unknown_cost_present: bool(r.unknown_cost_present),
    cap_usd: usdAmount(r.cap_usd),
    calls: uint(r.calls),
    send_hold: bool(r.send_hold),
    enforced: literal(r.enforced, true),
    provider_mode: literal(r.provider_mode, "host_configured_or_scripted"),
  };
}

export type GraphLayer = "core" | "entry" | "test" | "doc" | "config";
export interface GraphFileDto { path: string; layer: GraphLayer; symbols: string[] }
export interface GraphReadDto { ok: true; root_id: string; files: GraphFileDto[]; nodes: number; edges: number; unresolved: number | null }
/** Read endpoint always sends files/nodes/edges; `unresolved` is validated when present. */
export function validateGraphRead(value: unknown): GraphReadDto {
  const r = shape(value, ["ok", "root_id", "files", "nodes", "edges"], ["unresolved"]);
  const files = list(r.files, 10000).map(entry => {
    const f = shape(entry, ["path", "layer", "symbols"]);
    return { path: text(f.path, 1024), layer: oneOf(f.layer, ["core", "entry", "test", "doc", "config"]), symbols: list(f.symbols, 512).map(v => label(v)) };
  });
  return {
    ok: literal(r.ok, true),
    root_id: text(r.root_id, 256),
    files,
    nodes: uint(r.nodes),
    edges: uint(r.edges),
    unresolved: Object.hasOwn(r, "unresolved") ? uint(r.unresolved) : null,
  };
}
