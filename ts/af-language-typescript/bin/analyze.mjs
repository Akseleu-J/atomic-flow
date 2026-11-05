#!/usr/bin/env node
// One JSON request/response per process. Never reads a repository or tsconfig.
import { analyze, MAX_REQUEST_BYTES } from "../dist/index.js";
let bytes = 0;
const chunks = [];
try {
  for await (const chunk of process.stdin) {
    bytes += chunk.length;
    if (bytes > MAX_REQUEST_BYTES) throw Error("request byte ceiling");
    chunks.push(chunk);
  }
  const text = new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks));
  const result = JSON.stringify(analyze(JSON.parse(text)));
  if (Buffer.byteLength(result) > 24_000_000) throw Error("response byte ceiling");
  process.stdout.write(result + "\n");
} catch (error) {
  process.stderr.write(JSON.stringify({ schema: "source-analysis-error/1", error: String(error).slice(0, 500) }) + "\n");
  process.exitCode = 1;
}
