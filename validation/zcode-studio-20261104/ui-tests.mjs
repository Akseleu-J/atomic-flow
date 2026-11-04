import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { Script, createContext } from "node:vm";
import { test } from "node:test";

const html = readFileSync(new URL("../../rust/af-cli/src/dashboard.html", import.meta.url), "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const source = script.split("/* TESTABLE_STUDIO_CORE_BEGIN */")[1].split("/* TESTABLE_STUDIO_CORE_END */")[0];
const sandbox = createContext({ TextDecoder });
new Script(source + "\nthis.core = StudioCore;").runInContext(sandbox);
const core = sandbox.core;
function bytes(text, width = 1024) {
  const raw = new TextEncoder().encode(text); let cursor = 0;
  return new ReadableStream({ pull(controller) { if (cursor === raw.length) { controller.close(); return; } controller.enqueue(raw.slice(cursor, cursor + width)); cursor += Math.min(width, raw.length - cursor); } });
}
const frame = value => "data: " + JSON.stringify(value) + "\n\n";

test("complete fragmented Unicode SSE has one terminal outcome without verified claims", async () => {
  const events = [];
  const terminal = await core.consumeSSE(bytes((frame({ type: "delta", text: "Привет <script>" }) + frame({ type: "final", ok: true, content: "Привет <script>" })).replaceAll("\n", "\r\n"), 1), e => events.push(e));
  assert.equal(events[0].text, "Привет <script>");
  assert.equal(core.outcome(terminal), "received");
  assert.equal(core.verification({ verified: true }).verified, false);
});

test("missing or partial final never becomes a successful response", async () => {
  await assert.rejects(core.consumeSSE(bytes(frame({ type: "delta", text: "partial" })), () => {}), /завершения/);
  await assert.rejects(core.consumeSSE(bytes('data: {"type":"final"'), () => {}), /посреди/);
  await assert.rejects(core.consumeSSE(bytes(frame({ type: "final" })), () => {}), /статуса/);
});

test("unknown, refusal and partial are distinct terminal states", () => {
  assert.equal(core.outcome({ type: "error", unknown: true, sent: true }), "hold");
  assert.equal(core.outcome({ type: "error", sent: false, error: "not sent" }), "refused");
  assert.equal(core.outcome({ type: "final", ok: true, truncated: true }), "partial");
  assert.equal(core.outcome({}), "unknown");
});

test("unbounded frames and output are refused and reader cancelled", async () => {
  let cancelled = false;
  const body = new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode("data: " + "a".repeat(core.LIMITS.frame + 1))); }, cancel() { cancelled = true; } });
  await assert.rejects(core.consumeSSE(body, () => {}), /предел/);
  assert.equal(cancelled, true);
  const chunk = frame({ type: "delta", text: "a".repeat(16000) });
  await assert.rejects(core.consumeSSE(bytes(chunk.repeat(17)), () => {}), /предел ответа/);
});

test("duplicate terminal and post-terminal deltas are rejected", async () => {
  for (const extra of [{ type: "final", ok: true }, { type: "delta", text: "after" }]) {
    await assert.rejects(core.consumeSSE(bytes(frame({ type: "final", ok: true }) + frame(extra)), () => {}), /после завершения/);
  }
});

test("malformed and unknown SSE frames cannot silently qualify", async () => {
  await assert.rejects(core.consumeSSE(bytes("data: {not json}\n\n"), () => {}));
  await assert.rejects(core.consumeSSE(bytes(frame({ type: "verified", ok: true })), () => {}), /Неизвестное/);
});

test("invalid receipts cannot be dropped to fabricate all-passed verification", () => {
  const valid = { name: "native tests", scope: "8 fixed tests", status: "passed", evidence_sha256: "a".repeat(64) };
  assert.equal(core.verification({ verified: true, checks: [valid, { name: "forged", status: "passed" }] }).verified, false);
  assert.equal(core.verification({ verified: true, checks: [valid] }).verified, true);
  assert.equal(core.verification({ verified: true }).published, false);
});

test("dashboard is self-contained, safe-text rendered and nonce-CSP compatible", () => {
  assert.doesNotThrow(() => new Script(script));
  assert.doesNotMatch(html, /\bon(?:click|input|change|load|error|keydown)\s*=/i);
  assert.doesNotMatch(script, /\.innerHTML|insertAdjacentHTML|new Function|\beval\(/);
  assert.doesNotMatch(html, /fonts\.googleapis|fonts\.gstatic|javascript:/);
  assert.doesNotMatch(script, /localStorage\.setItem\([^\n]*(?:token|backend)/i);
  assert.match(script, /session_id: state\.sessionId/);
  assert.match(script, /root_id: state\.rootId/);
  assert.match(html, /Снимок журнала/);
});
