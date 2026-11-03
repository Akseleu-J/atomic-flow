import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { analyze } from "../dist/index.js";
const file = (path, source) => ({ path, source, sha256: createHash("sha256").update(source).digest("hex") });
const request = files => ({ root_id: "main", source_revision: "a".repeat(64), files });

test("compiler resolves admitted relative imports and direct aliases without repository execution", () => {
  const r = request([file("src/lib.ts", "export function refund() { return 1; }\n"),
    file("src/main.ts", "import {refund as perform} from './lib';\nexport function run() { return perform(); }\n")]);
  const result = analyze(r);
  assert.equal(result.verification, "not_assessed");
  assert.equal(result.root_id, r.root_id);
  assert.equal(result.source_revision, r.source_revision);
  assert.ok(result.edges.some(e => e.kind === "imports" && e.dst === "file:src/lib.ts"));
  assert.ok(result.edges.some(e => e.kind === "calls" && e.src === "sym:src/main.ts::run" && e.dst === "sym:src/lib.ts::refund"));
  assert.ok(result.nodes.every(n => n.source.digest === r.files.find(f => f.path === n.path).sha256));
});
test("unknown packages and dynamic calls stay unresolved", () => {
  const result = analyze(request([file("main.ts", "import {unknown} from 'unavailable'; unknown();\n")]));
  assert.equal(result.imports[0].record.resolution, "not_assessed");
  assert.equal(result.edges.filter(e => e.kind === "imports" || e.kind === "calls").length, 0);
  assert.ok(result.omissions.some(o => o.reason === "call_target_unresolved_or_ambiguous"));
});
test("exact source binding refuses hash drift, hidden extra config, duplicate paths and traversal", () => {
  const source = file("main.ts", "export const value = 1;\n");
  for (const value of [request([{ ...source, sha256: "b".repeat(64) }]), request([source, source]),
    request([{ ...source, path: "../main.ts" }]), request([{ ...source, path: "nested\\main.ts" }]),
    { ...request([source]), tsconfig: {} }, request([{ ...source, source: "x".repeat(1_000_001) }])]) {
    assert.throws(() => analyze(value));
  }
});
test("syntax failure preserves source identity but extracts no symbols", () => {
  const result = analyze(request([file("broken.ts", "export function broken( {\n")]));
  assert.equal(result.nodes.length, 1);
  assert.equal(result.nodes[0].facts.parse_ok, false);
  assert.ok(result.omissions.some(o => o.reason === "syntax_errors_no_symbols_extracted"));
});
test("source import side effects are not executed and CLI is strict JSON", () => {
  const r = request([file("main.ts", "throw new Error('must never execute'); export function f() {}\n")]);
  const result = spawnSync(process.execPath, ["bin/analyze.mjs"], { cwd: new URL("..", import.meta.url),
    input: JSON.stringify(r), encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).verification, "not_assessed");
  const bad = spawnSync(process.execPath, ["bin/analyze.mjs"], { cwd: new URL("..", import.meta.url), input: "{}\n{}", encoding: "utf8" });
  assert.equal(bad.status, 1);
  assert.equal(bad.stdout, "");
});
