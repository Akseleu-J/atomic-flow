# TypeScript source-analysis sidecar

Source-only compiler analysis using exactly TypeScript **5.8.3**. This is a
narrow language capability, not runtime execution, type correctness, Verified
evidence or full project semantic parity. See
[`SOURCE_ANALYSIS.md`](../../rust/af-graph/SOURCE_ANALYSIS.md) for shared contract,
omissions and host admission requirements.

Install only pinned dependencies without repository lifecycle scripts:

```sh
npm ci --ignore-scripts
npm run build
npm test
```

Run `node bin/analyze.mjs` with one JSON document on stdin:

```json
{"root_id":"main","source_revision":"<host snapshot SHA256>","files":[{"path":"src/main.ts","source":"export function run() {}\n","sha256":"<exact UTF8 source SHA256>"}]}
```

It emits one `source-analysis/1` document or exits 1 with a bounded structured
error on stderr. No source path is opened, no tsconfig/package script is loaded,
no JavaScript is emitted or executed. Relative import candidates must be among
the supplied files. Unknown package/type/dynamic targets remain omissions.
JSON parsing/output limits do not isolate the process: the host must supply its
own OS resource, deadline, cancellation and admitted-input controls.
