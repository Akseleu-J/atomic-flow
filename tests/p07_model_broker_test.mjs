import test from 'node:test';
import assert from 'node:assert/strict';
import {MODEL, MAX_WIRE_BYTES, canonical, digest, validateRequest, processOne} from '../tools/p07_model_broker.mjs';

function request(n = '0') {
  const req = {schema_version: 1, kind: 'p07_model_request', call_id: 'mq_' + n.repeat(32),
    identity: {campaign_id: 'p07', owner: 'akseley', arm: 'atomic', task_id: 'sympy_21614',
      framework_sha: 'a'.repeat(40), workflow_sha: 'b'.repeat(40), profile_sha256: 'c'.repeat(64)},
    config: {model: MODEL, max_input_tokens: 1000, max_output_tokens: 128, temperature: '0.1', max_retries: 0},
    messages: [{role: 'user', content: 'public benchmark 😀'}]};
  req.messages_sha256 = digest(req.messages);
  req.request_sha256 = digest(req);
  return req;
}

function physical(req) {
  return {content: '{"answer":"ok"}', tokens_in: 10, tokens_out: 8, cost_usd: '0.001', latency_ms: 1,
    truncated: false, cost_source: 'provider_reported', resolved_backend: 'kaggle_model_proxy',
    resolved_model: MODEL, attempt_id: req.call_id};
}

function harness() {
  const records = new Map(), packets = new Map(), events = [];
  let pending = Promise.resolve();
  const h = {records, packets, events, sends: 0, settles: 0};
  h.journal = {
    withLock(fn) { const result = pending.then(fn); pending = result.catch(() => {}); return result; },
    async get(id) { return structuredClone(records.get(id)); },
    async put(id, value) { events.push(value.state); records.set(id, structuredClone(value)); },
  };
  h.queue = {async put_response(id, packet) {
    if (packets.has(id)) assert.equal(canonical(packets.get(id)), canonical(packet));
    packets.set(id, structuredClone(packet));
  }};
  h.proxy = {async complete(req) { h.sends++; events.push('physical_send'); return physical(req); }};
  h.admit = async req => { events.push('reserve'); return {status: 'admitted', reservation_id: req.call_id}; };
  h.settle = async () => { h.settles++; events.push('settle'); return {status: 'settled'}; };
  h.run = req => processOne({request: req, queue: h.queue, proxy: h.proxy, journal: h.journal,
    admit: h.admit, settle: h.settle});
  return h;
}

test('canonical UTF-8 vector matches Python including lexical numeric keys', () => {
  const value = {z: 0, '2': 2, a: ['Привет 😀 é', true, null], '10': 1};
  assert.equal(canonical(value), '{"10":1,"2":2,"a":["Привет 😀 é",true,null],"z":0}');
  assert.equal(digest(value), 'c690a66b88d2841724c748c3d2209af0e8eb8179ac52c004eae5f398e56a28b3');
  for (const bad of [NaN, 1.2, 2**53, {'é': 1}, '\ud800']) assert.throws(() => canonical(bad));
});

test('reserve, claim, one physical send, settle then immutable response', async () => {
  const h = harness(), req = request();
  const result = await h.run(req);
  assert.deepEqual(result, {call_id: req.call_id, state: 'completed', sent: true});
  assert.deepEqual(h.events, ['admitting', 'reserve', 'claimed', 'physical_send', 'received', 'settle', 'completed']);
  assert.equal(h.packets.get(req.call_id).result.cost_usd, '0.001');
  const packet = h.packets.get(req.call_id), {response_sha256, ...body} = packet;
  assert.equal(response_sha256, digest(body));
  assert.equal(packet.identity_sha256, digest(req.identity));
  await h.run(req);
  assert.equal(h.sends, 1); assert.equal(h.settles, 1);
});

test('two concurrent deliveries share the durable single writer and send once', async () => {
  const h = harness(), req = request();
  await Promise.all([h.run(req), h.run(req)]);
  assert.equal(h.sends, 1); assert.equal(h.settles, 1);
});

test('known refusal never sends or settles', async () => {
  const h = harness(); h.admit = async () => ({status: 'refused'});
  assert.equal((await h.run(request())).state, 'refused');
  assert.equal(h.sends, 0); assert.equal(h.settles, 0);
  await h.run(request()); assert.equal(h.sends, 0);
});

test('admission failure or invalid reservation stays unknown and is never replayed', async () => {
  for (const admit of [async () => { throw new Error('reservation uncertain'); }, async () => ({status: 'admitted'})]) {
    const h = harness(); h.admit = admit;
    assert.equal((await h.run(request())).state, 'unknown');
    h.admit = async () => ({status: 'admitted', reservation_id: 'new'});
    assert.equal((await h.run(request())).state, 'unknown');
    assert.equal(h.sends, 0);
  }
});

test('physical or settlement failure never resends and preserves received usage', async () => {
  for (const failure of ['physical', 'settle', 'cost']) {
    const h = harness(), req = request();
    if (failure === 'physical') h.proxy.complete = async () => { h.sends++; throw new Error('timeout'); };
    if (failure === 'settle') h.settle = async () => { throw new Error('journal unavailable'); };
    if (failure === 'cost') h.proxy.complete = async req => { h.sends++; return {...physical(req), cost_usd: null}; };
    assert.equal((await h.run(req)).state, 'unknown');
    await h.run(req); assert.equal(h.sends, 1);
    if (failure === 'settle') assert.equal(h.records.get(req.call_id).result.cost_usd, '0.001');
  }
});

test('unusable received content still settles its physical usage', async () => {
  const h = harness(), req = request();
  h.proxy.complete = async req => { h.sends++; throw {usage_result: physical(req)}; };
  assert.equal((await h.run(req)).state, 'completed');
  assert.equal(h.packets.get(req.call_id).error_code, 'response_shape');
  assert.equal(h.sends, 1); assert.equal(h.settles, 1);
});

test('response publication failure reuses settled packet without inference', async () => {
  const h = harness(), req = request(), put = h.queue.put_response;
  h.queue.put_response = async () => { throw new Error('GitHub unavailable'); };
  await assert.rejects(h.run(req));
  assert.equal(h.records.get(req.call_id).state, 'completed');
  h.queue.put_response = put;
  await h.run(req); assert.equal(h.sends, 1); assert.equal(h.settles, 1);
});

test('crash after reservation and before physical send preserves claim; no replay', async () => {
  const h = harness(), req = request(), put = h.journal.put;
  h.journal.put = async (id, record) => { await put(id, record); if (record.state === 'claimed') throw new Error('crash'); };
  await assert.rejects(h.run(req));
  h.journal.put = put;
  assert.equal((await h.run(req)).state, 'unknown'); assert.equal(h.sends, 0);
});

test('changed request with same call ID is refused', async () => {
  const h = harness(), req = request(); await h.run(req);
  const changed = request(); changed.messages[0].content = 'changed';
  changed.messages_sha256 = digest(changed.messages);
  delete changed.request_sha256; changed.request_sha256 = digest(changed);
  await assert.rejects(h.run(changed), /conflict/); assert.equal(h.sends, 1);
});

test('strict schema, runtime/model/owner and 1MiB request admission precede effects', async () => {
  for (const edit of [r => r.identity.owner = 'unknown', r => r.config.model = 'other',
    r => r.config.max_retries = 1, r => r.config.api_key = 'forbidden',
    r => r.messages[0].content = 'x'.repeat(MAX_WIRE_BYTES), r => r.request_sha256 = 'd'.repeat(64),
    r => r.config.temperature = '1x1']) {
    const h = harness(), req = request(); edit(req);
    assert.throws(() => validateRequest(req)); await assert.rejects(h.run(req));
    assert.equal(h.sends, 0); assert.equal(h.records.size, 0);
  }
});
