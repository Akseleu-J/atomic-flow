import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { AfClient, AfHttpError, AfProtocolError, AfTransportError, WireError, parseVerificationReport } from '../dist/index.js';

const vectors = JSON.parse(await readFile(new URL('../../../rust/af-evidence/fixtures/wire-v1.json', import.meta.url), 'utf8'));
const baseline = JSON.parse(vectors.cases.find(c => c.name === 'empty_not_assessed').json);
const health = { ok: true, version: '0.1.0', kernel: 'atomic-flow-rust' };
const capabilities = { schema_version: 1, verification_schema_version: 1, authority: 'single_owner', requirement_checker: 'unavailable', verification_report_authorizes_execution: false, publication_recovery: 'host_cli_preview_digest', unknown_replay: false };
const json = value => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
const client = fetchImpl => new AfClient({ baseUrl: 'http://127.0.0.1:8787', fetchImpl });
const input = { backend: { provider_id: 'scripted', model: 'scripted' }, prompt: 'Explain\nthe code' };
const terminal = overrides => ({ type: 'final', ok: true, content: 'answer', tokens_in: 4, tokens_out: 1, cost_usd: 0, truncated: false, verification: 'not_assessed', verification_report: structuredClone(baseline), coverage_scope: 'selected_request_excerpts', sources: [], unknown: false, error: '', ...overrides });
const sse = events => new Response(events.map(e => `data: ${JSON.stringify(e)}\n\n`).join(''), { headers: { 'Content-Type': 'text/event-stream' } });
async function collect(iterable) { const events = []; for await (const item of iterable) events.push(item); return events; }

for (const vector of vectors.cases) {
  test(`Rust frozen verification vector: ${vector.name}`, async () => {
    if (vector.valid) assert.deepEqual(await parseVerificationReport(vector.json), JSON.parse(vector.json));
    else await assert.rejects(parseVerificationReport(vector.json), AfProtocolError);
  });
}
test('reports reject noncanonical integers and nested escaped duplicate names', async () => {
  const raw = JSON.stringify(baseline);
  for (const token of ['-0', '0.0', '0e0', 'true', '9007199254740993']) {
    await assert.rejects(parseVerificationReport(raw.replace('"checked":0', `"checked":${token}`)), AfProtocolError);
  }
  await assert.rejects(parseVerificationReport(raw.replace('"workspace_id":"w1"', '"workspace_id":"w1","workspace_\\u0069d":"w2"')), AfProtocolError);
});
test('UTF8 byte limits, controls, invalid scalar and huge wire are refused', async () => {
  const data = structuredClone(baseline); data.binding.workspace_id = 'я'.repeat(129);
  await assert.rejects(parseVerificationReport(JSON.stringify(data)), AfProtocolError);
  data.binding.workspace_id = 'x\u0085'; await assert.rejects(parseVerificationReport(JSON.stringify(data)), AfProtocolError);
  data.binding.workspace_id = '\ud800'; await assert.rejects(parseVerificationReport(JSON.stringify(data)), AfProtocolError);
  await assert.rejects(parseVerificationReport(' '.repeat(262145)), AfProtocolError);
});
test('only HTTPS or loopback HTTP with no URL credentials/query/fragment', () => {
  for (const baseUrl of ['http://example.com', 'http://127.0.0.1.evil.test', 'https://u:p@example.com', 'https://example.com?x', 'https://example.com#x', 'https://example.com?', 'ftp://localhost']) {
    assert.throws(() => new AfClient({ baseUrl }), AfProtocolError);
  }
  for (const baseUrl of ['https://example.com', 'http://localhost:8787', 'http://127.0.0.1:8787', 'http://[::1]:8787']) new AfClient({ baseUrl });
});
test('bearer is header-only; redirects disabled; response checked at runtime', async () => {
  let captured;
  const sdk = new AfClient({ baseUrl: 'http://127.0.0.1:8787', token: 'process-secret', fetchImpl: async (url, init) => { captured = { url, init }; return json(health); } });
  await sdk.health();
  assert.equal(captured.url, 'http://127.0.0.1:8787/health');
  assert.equal(captured.init.headers.Authorization, 'Bearer process-secret');
  assert.equal(captured.init.redirect, 'error'); assert.equal(captured.init.credentials, 'omit');
  await assert.rejects(client(async () => json({ ...health, ok: 'true' })).health(), AfProtocolError);
  await assert.rejects(client(async () => json({ ...health, extra: 1 })).health(), AfProtocolError);
});
test('capabilities preserve unavailable checker and nonauthority semantics', async () => {
  assert.deepEqual(await client(async () => json(capabilities)).capabilities(), capabilities);
  await assert.rejects(client(async () => json({ ...capabilities, verification_report_authorizes_execution: true })).capabilities(), AfProtocolError);
});
test('session requests use root ID; snapshots require actual root/hold fields', async () => {
  let request;
  const sdk = client(async (url, init) => {
    request = { url, body: init.body && JSON.parse(init.body) };
    return json(url.endsWith('/v1/sessions') ? { ok: true, session_id: 's1', path: '/host/journal' } : { ok: true, session_id: 's1', events: 3, incomplete_tail: false, pending_call_ids: ['c1'], unresolved_call_ids: ['c2'], root_id: 'r1' });
  });
  assert.equal((await sdk.createSession('s1', 'r1')).session_id, 's1');
  assert.deepEqual(request.body, { id: 's1', root_id: 'r1' });
  const snapshot = await sdk.readSession('s1'); assert.equal(snapshot.root_id, 'r1'); assert.deepEqual(snapshot.unresolved_call_ids, ['c2']);
});
test('sessions list, budget snapshot and graph read are validated field-by-field', async () => {
  const sdk = client(async url => {
    if (url.endsWith('/v1/sessions')) return json({ ok: true, sessions: [{ session_id: 's1', created_at: null, project: null }, { id: 's2' }] });
    if (url.endsWith('/v1/budget')) return json({ state: 'hold', committed_usd: 'unknown', committed_basis: 'known_spend_plus_reservations', spent_usd_known: null, reserved_usd: '0.01', unknown_cost_present: true, available_usd: null, cap_usd: '1', calls: 3, send_hold: true, enforced: true, provider_mode: 'host_configured_or_scripted' });
    return json({ ok: true, root_id: 'main', files: [{ path: 'src/main.py', layer: 'entry', symbols: ['main', ''] }], nodes: 4, edges: 2 });
  });
  const sessions = await sdk.listSessions();
  assert.deepEqual(sessions.sessions, [{ session_id: 's1', created_at: null, project: null }, { session_id: 's2', created_at: null, project: null }]);
  const budget = await sdk.readBudget();
  assert.equal(budget.state, 'hold'); assert.equal(budget.committed_usd, 'unknown'); assert.equal(budget.calls, 3);
  const graph = await sdk.readGraph('main');
  assert.deepEqual(graph.files, [{ path: 'src/main.py', layer: 'entry', symbols: ['main', ''] }]); assert.equal(graph.unresolved, null);
  await assert.rejects(client(async () => json({ ok: true, sessions: [{}] })).listSessions(), WireError);
  await assert.rejects(client(async () => json({ ok: true, sessions: [{ session_id: 's1', extra: 1 }] })).listSessions(), WireError);
  await assert.rejects(client(async () => json({ state: 'ok', committed_usd: '1', cap_usd: '2', calls: 0, send_hold: false })).readBudget(), WireError);
  await assert.rejects(client(async () => json({ ok: true, root_id: 'r', files: [], nodes: 0, edges: 0, store: '/host/graph.json' })).readGraph('r'), WireError);
});
test('user input cannot request terminal events or arbitrary job-store paths', async () => {
  const requests = [];
  const sdk = client(async (url, init) => { requests.push({ url, body: init.body && JSON.parse(init.body) }); return json(url.endsWith('/v1/jobs') ? { ok: true, jobs: [], unresolved_unknown_ids: ['job1'] } : { event_type: 'user_input', seq: 2, payload: { text: 'hello' } }); });
  await sdk.appendUserInput('s1', { text: 'hello' });
  assert.deepEqual(requests[0].body, { event_type: 'user_input', payload: { text: 'hello' } });
  assert.equal(sdk.appendEvent, undefined);
  assert.deepEqual((await sdk.jobStatus()).unresolved_unknown_ids, ['job1']);
  assert.equal(requests[1].url, 'http://127.0.0.1:8787/v1/jobs');
});
test('edit request uses host provider and can never return verified success', async () => {
  let sent = 0;
  const sdk = client(async (_url, init) => { sent++; const body = JSON.parse(init.body); assert.deepEqual(body.backend, { provider_id: 'host1', model: 'm' }); return json({ ok: true, verified: false, refusal: {}, session_id: 's', edits: 1, verification: 'not_assessed', status: 'changed_unverified' }); });
  const edit = { backend: { provider_id: 'host1', model: 'm' }, root_id: 'r', file: 'a.rs', replacement: 'source\n', goal: 'fix\ncode', host_approved: true };
  assert.equal((await sdk.runEditJourney(edit)).verification, 'not_assessed');
  await assert.rejects(sdk.runEditJourney({ ...edit, backend: { base_url: 'https://evil', api_key_env: 'SECRET', model: 'm' } }), AfProtocolError);
  assert.equal(sent, 1);
  await assert.rejects(client(async () => json({ ok: true, verified: true, refusal: {}, session_id: 's', edits: 1, verification: 'not_assessed', status: 'changed_unverified' })).runEditJourney(edit), AfProtocolError);
});
test('JSON body limits apply without or with a false Content-Length and cancel reader', async () => {
  for (const contentLength of [undefined, '1', '9999999']) {
    let cancelled = false;
    const body = new ReadableStream({ pull(controller) { controller.enqueue(new TextEncoder().encode('x'.repeat(512))); }, cancel() { cancelled = true; } });
    const sdk = new AfClient({ baseUrl: 'http://localhost', maxResponseBytes: 128, fetchImpl: async () => new Response(body, { headers: { 'Content-Type': 'application/json', ...(contentLength ? { 'Content-Length': contentLength } : {}) } }) });
    await assert.rejects(sdk.health(), AfProtocolError); assert.equal(cancelled, true);
  }
});
test('invalid UTF8, wrong content type and duplicate JSON keys refused', async () => {
  await assert.rejects(client(async () => new Response(new Uint8Array([0xff]), { headers: { 'Content-Type': 'application/json' } })).health(), AfProtocolError);
  await assert.rejects(client(async () => new Response('{}')).health(), AfProtocolError);
  await assert.rejects(client(async () => new Response('{"ok":false,"ok":true,"version":"1","kernel":"atomic-flow-rust"}', { headers: { 'Content-Type': 'application/json' } })).health(), AfProtocolError);
});
test('HTTP rejection is typed and transport unknown is never retried', async () => {
  await assert.rejects(client(async () => new Response('private error', { status: 401 })).health(), error => error instanceof AfHttpError && error.status === 401 && !error.message.includes('private'));
  let calls = 0;
  const sdk = client(async () => { calls++; throw new Error('network lost after send'); });
  await assert.rejects(sdk.createSession('s1'), error => error instanceof AfTransportError && error.outcome === 'unknown' && error.retryAllowed === false);
  assert.equal(calls, 1);
});
test('stream projects deltas and data-only report with explicit selected coverage', async () => {
  let request;
  const sdk = client(async (_url, init) => { request = JSON.parse(init.body); return sse([{ type: 'delta', text: 'answer' }, terminal()]); });
  const events = await collect(sdk.chat(input));
  assert.equal(events.length, 2); assert.deepEqual(request.backend, input.backend); assert.equal(request.prompt, input.prompt);
  assert.equal(events[1].verification_report.requirements.status, 'not_assessed'); assert.equal(events[1].coverage_scope, 'selected_request_excerpts');
});
test('unknown/cancelled stream never reconnects or turns missing usage into zero', async () => {
  let calls = 0;
  const sdk = client(async () => { calls++; return sse([terminal({ type: 'error', ok: false, cost_usd: null, unknown: true, error: 'usage unresolved' })]); });
  const events = await collect(sdk.chat(input)); assert.equal(events[0].cost_usd, null); assert.equal(events[0].unknown, true); assert.equal(calls, 1);
  await assert.rejects(collect(client(async () => sse([terminal({ cost_usd: null })])).chat(input)), AfProtocolError);
  await assert.rejects(collect(client(async () => sse([{ type: 'delta', text: 'partial' }])).chat(input)), AfTransportError);
});
test('stream rejects old credential request before send and forged final DTO', async () => {
  let calls = 0;
  const sdk = client(async () => { calls++; return sse([terminal()]); });
  await assert.rejects(collect(sdk.chat({ ...input, backend: { model: 'm', base_url: 'https://evil', api_key_env: 'SECRET' } })), AfProtocolError);
  await assert.rejects(collect(sdk.chat({ ...input, backend: { provider_id: 'real', model: 'm' } })), AfProtocolError);
  assert.equal(calls, 0);
  await assert.rejects(collect(client(async () => sse([terminal({ verified: true })])).chat(input)), AfProtocolError);
});
test('closing stream aborts actual fetch and cancels receiver without another send', async () => {
  let signal, cancelled = false, calls = 0;
  const sdk = client(async (_url, init) => { signal = init.signal; calls++; return new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode('data: {"type":"delta","text":"a"}\n\n')); }, cancel() { cancelled = true; } }), { headers: { 'Content-Type': 'text/event-stream' } }); });
  for await (const event of sdk.chat(input)) { assert.equal(event.type, 'delta'); break; }
  assert.equal(signal.aborted, true); assert.equal(cancelled, true); assert.equal(calls, 1);
});
test('stream total/event bounds and nested integer lexical rules are enforced', async () => {
  const sdk = new AfClient({ baseUrl: 'http://localhost', maxResponseBytes: 32, fetchImpl: async () => sse([{ type: 'delta', text: 'a'.repeat(100) }]) });
  await assert.rejects(collect(sdk.chat(input)), AfProtocolError);
  const raw = `data: ${JSON.stringify(terminal()).replace('"checked":0', '"checked":0.0')}\n\n`;
  await assert.rejects(collect(client(async () => new Response(raw, { headers: { 'Content-Type': 'text/event-stream' } })).chat(input)), AfProtocolError);
});
