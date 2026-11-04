/** Bounded JSON parsing. Reject duplicate keys before JSON.parse can erase them. */
export class AfProtocolError extends Error {
  constructor(message: string) { super(message); this.name = "AfProtocolError"; }
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
