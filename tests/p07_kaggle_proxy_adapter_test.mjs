import test from 'node:test';
import assert from 'node:assert/strict';
import {runInNewContext} from 'node:vm';
import {MODEL, MAX_WIRE_BYTES, digest, processOne} from '../tools/p07_model_broker.mjs';
import {createKaggleProxy, MAX_RESPONSE_BYTES} from '../tools/p07_kaggle_proxy_adapter.mjs';

const NOW = 1700000000000;
const BASE = 'https://reviewed-proxy.example/session';
const TEST_AUTH = 'synthetic-fixture-only';
let count = 0;
test('fetch byte views from another VM realm decode without another request', async () => {
  const raw = JSON.stringify(payload());
  const bytes = runInNewContext('Uint8Array.from(' + JSON.stringify([...Buffer.from(raw)]) + ')');
  assert.equal(bytes instanceof Uint8Array, false);
  let reads = 0, calls = 0;
  const proxy = fixture(async () => {
    calls++;
    return {status: 200, redirected: false, headers: new Headers(), body: {getReader: () => ({
      read: async () => reads++ ? {done: true} : {done: false, value: bytes},
      releaseLock() {}, cancel: async () => {},
    })}};
  });
  const result = await proxy.complete(request());
  assert.equal(result.content, '{"answer":"ok"}');
  assert.equal(result.cost_usd, '0.012');
  assert.equal(calls, 1);
});
function request(changes = {}) {
  const req = {schema_version: 1, kind: 'p07_model_request', call_id: 'mq_' + (++count).toString(16).padStart(32, '0'),
    identity: {campaign_id: 'p07_paid', owner: 'akseley', arm: 'atomic', task_id: 'sympy_1',
      framework_sha: 'a'.repeat(40), workflow_sha: 'b'.repeat(40), profile_sha256: 'c'.repeat(64)},
    config: {model: MODEL, max_input_tokens: 1000, max_output_tokens: 128, temperature: null, max_retries: 0, ...changes},
    messages: [{role: 'user', content: 'public fixture'}]};
  return rehash(req);
}
function rehash(req) {
  req.messages_sha256 = digest(req.messages); delete req.request_sha256; req.request_sha256 = digest(req);
  return req;
}
function payload(usage = {prompt_tokens: 10, completion_tokens: 8, cost_usd: 0.012}) {
  return {model: MODEL, usage, choices: [{message: {role: 'assistant', content: '{"answer":"ok"}'}, finish_reason: 'stop'}]};
}
function fixture(fetchImpl, changes = {}) {
  return createKaggleProxy({baseUrl: BASE, apiKey: TEST_AUTH, expiresAt: NOW + 60000, now: () => NOW, fetchImpl, ...changes});
}
function response(value = payload(), options = {}) {
  return new Response(typeof value === 'string' ? value : JSON.stringify(value), {status: 200, ...options});
}
function rawCost(cost) {
  return '{"model":' + JSON.stringify(MODEL) + ',"usage":{"prompt_tokens":10,"completion_tokens":8,"cost_usd":'
    + cost + '},"choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}';
}

test('one exact Chat Completions request, process-only auth and no format negotiation', async () => {
  let calls = 0;
  const proxy = fixture(async (url, options) => {
    calls++;
    assert.equal(url, BASE + '/openapi/chat/completions');
    assert.equal(options.method, 'POST');
    assert.equal(options.redirect, 'error');
    assert.equal(options.headers.Authorization === 'Bearer ' + TEST_AUTH, true);
    assert.ok(options.signal instanceof AbortSignal);
    assert.deepEqual(JSON.parse(options.body), {model: MODEL, messages: [{role: 'user', content: 'public fixture'}], max_tokens: 128});
    assert.equal(options.body.includes(TEST_AUTH), false);
    return response();
  });
  const req = request(), result = await proxy.complete(req);
  assert.equal(calls, 1);
  assert.deepEqual(result, {content: '{"answer":"ok"}', tokens_in: 10, tokens_out: 8, cost_usd: '0.012',
    latency_ms: 0, truncated: false, cost_source: 'provider', resolved_backend: 'kaggle_model_proxy',
    resolved_model: MODEL, attempt_id: req.call_id});
  assert.deepEqual(Object.keys(proxy), ['complete']);
  assert.equal(JSON.stringify(proxy), '{}');
  await assert.rejects(proxy.complete(req), error => error.message === 'kaggle_proxy_request_refused');
  assert.equal(calls, 1);
});

test('already normalized openapi base and optional reviewed temperature', async () => {
  const proxy = fixture(async (url, options) => {
    assert.equal(url, BASE + '/openapi/chat/completions');
    assert.equal(JSON.parse(options.body).temperature, 0.25);
    const body = payload(); body.choices[0].finish_reason = 'length';
    return response(body);
  }, {baseUrl: BASE + '/openapi/'});
  assert.equal((await proxy.complete(request({temperature: '0.25'}))).truncated, true);
});

test('matching cost and token aliases are reconciled exactly', async () => {
  const variants = [
    [{prompt_tokens: 1, completion_tokens: 2, cost_usd: 0}, '0'],
    [{input_tokens: 1, output_tokens: 2, total_cost_usd: 0.003}, '0.003'],
    [{prompt_tokens: 1, completion_tokens: 2, cost: 0.004}, '0.004'],
    [{prompt_tokens: 1, completion_tokens: 2, cost: {input_tokens_cost_nanodollars: 20, output_tokens_cost_nanodollars: 30}}, '0.00000005'],
    [{prompt_tokens: 1, completion_tokens: 2, input_tokens_cost_nanodollars: 40, output_tokens_cost_nanodollars: 60}, '0.0000001'],
    [{prompt_tokens: 1, completion_tokens: 2, cost: {input_tokens_cost_nanodollars: 12.5}, output_tokens_cost_nanodollars: 7.5}, '0.00000002'],
    [{prompt_tokens: 1, input_tokens: 1, completion_tokens: 2, output_tokens: 2,
      cost_usd: 0.005, total_cost_usd: 0.005, cost: 0.005}, '0.005'],
    [{prompt_tokens: 1, completion_tokens: 2, cost_usd: 0.00000002,
      input_tokens_cost_nanodollars: 12.5, output_tokens_cost_nanodollars: 7.5,
      cost: {input_tokens_cost_nanodollars: 12.5, output_tokens_cost_nanodollars: 7.5}}, '0.00000002'],
  ];
  for (const [usage, expected] of variants) {
    const proxy = fixture(async () => response(payload(usage)));
    assert.equal((await proxy.complete(request())).cost_usd, expected);
  }
});

test('conflicting or partial redundant usage is unknown, never a selected cheaper estimate', async () => {
  const variants = [
    {prompt_tokens: 1, completion_tokens: 2, cost_usd: 0.005, total_cost_usd: 3},
    {prompt_tokens: 1, completion_tokens: 2, cost_usd: 0.005, cost: 6},
    {prompt_tokens: 1, input_tokens: 2, completion_tokens: 2, cost_usd: 0.005},
    {prompt_tokens: 1, completion_tokens: 2, output_tokens: 1, cost_usd: 0.005},
    {prompt_tokens: 1, completion_tokens: 2, cost_usd: 0.00000002,
      input_tokens_cost_nanodollars: 12, output_tokens_cost_nanodollars: 9},
    {prompt_tokens: 1, completion_tokens: 2, cost_usd: 0.005, input_tokens_cost_nanodollars: 1},
    {prompt_tokens: 1, completion_tokens: 2, input_tokens_cost_nanodollars: 12,
      output_tokens_cost_nanodollars: 8, cost: {input_tokens_cost_nanodollars: 11}},
  ];
  for (const usage of variants) {
    let sends = 0;
    const proxy = fixture(async () => { sends++; return response(payload(usage)); });
    const req = request();
    await assert.rejects(proxy.complete(req), error => error.message === 'kaggle_proxy_usage_unknown'
      && error.request_outcome === 'unknown' && error.usage_result === undefined);
    await assert.rejects(proxy.complete(req), /kaggle_proxy_request_refused/);
    assert.equal(sends, 1);
  }
});

test('raw monetary lexemes retain exact decimal precision and scientific notation', async () => {
  for (const [raw, expected] of [
    ['0.123456789012345678', '0.123456789012345678'], ['1e-9', '0.000000001'],
    ['1.234e-5', '0.00001234'], ['2E+2', '200'], ['-0', '0'],
  ]) {
    assert.equal((await fixture(async () => response(rawCost(raw))).complete(request())).cost_usd, expected);
  }
});

test('missing, malformed, partial or estimated usage never becomes known zero', async () => {
  const variants = [
    undefined, null, {}, {prompt_tokens: 1, completion_tokens: 2},
    {prompt_tokens: 1, completion_tokens: 2, cost_usd: null},
    {prompt_tokens: 1, completion_tokens: 2, cost_usd: '0.1'},
    {prompt_tokens: 1, completion_tokens: 2, cost_usd: true},
    {prompt_tokens: 1, completion_tokens: 2, cost_usd: -0.1},
    {prompt_tokens: 1, completion_tokens: 2, cost: {input_tokens_cost_nanodollars: 1}},
    {prompt_tokens: 1, completion_tokens: 2, input_tokens_cost_nanodollars: 1},
    {prompt_tokens: 1, completion_tokens: 2, cost: null},
    {prompt_tokens: true, completion_tokens: 2, cost_usd: 0},
    {prompt_tokens: 1.1, completion_tokens: 2, cost_usd: 0},
    {prompt_tokens: 1001, completion_tokens: 2, cost_usd: 0},
    {prompt_tokens: 1, completion_tokens: 129, cost_usd: 0},
    {prompt_tokens: 9007199254740992, completion_tokens: 2, cost_usd: 0},
    {prompt_tokens: null, input_tokens: 1, completion_tokens: 2, cost_usd: 0},
  ];
  for (const invalid of variants) {
    const body = payload(); body.usage = invalid;
    await assert.rejects(fixture(async () => response(body)).complete(request()),
      error => error.message === 'kaggle_proxy_usage_unknown' && error.usage_result === undefined);
  }
  await assert.rejects(fixture(async () => response(rawCost('1e999'))).complete(request()),
    error => error.message === 'kaggle_proxy_usage_unknown');
});

test('usage valid malformed choices preserve empty-content usage for broker shape settlement', async () => {
  const malformed = [
    body => delete body.choices, body => body.choices = [], body => body.choices[0].message.content = null,
    body => body.choices[0].message.content = [{text: 'not reviewed text protocol'}],
    body => body.choices[0].message.tool_calls = [{id: 'unreviewed'}],
    body => body.choices[0].finish_reason = null,
  ];
  for (const edit of malformed) {
    const body = payload(); edit(body);
    await assert.rejects(fixture(async () => response(body)).complete(request()), error => {
      assert.equal(error.message, 'kaggle_proxy_response_shape');
      assert.equal(error.usage_result.content, '');
      assert.equal(error.usage_result.cost_usd, '0.012');
      assert.equal(error.usage_result.cost_source, 'provider');
      return true;
    });
  }
});

test('processOne settles recoverable malformed-content usage then emits response_shape', async () => {
  const records = new Map(), req = request(), body = payload(); body.choices[0].message.content = null;
  let settled = 0, packet;
  const result = await processOne({request: req, proxy: fixture(async () => response(body)),
    journal: {withLock: async fn => fn(), get: async id => records.get(id), put: async (id, value) => records.set(id, value)},
    queue: {put_response: async (id, value) => { packet = value; }},
    admit: async () => ({status: 'admitted', reservation_id: 'fixed'}),
    settle: async (reservation, usage) => { settled++; assert.equal(usage.cost_usd, '0.012'); return {status: 'settled'}; }});
  assert.equal(result.state, 'completed');
  assert.equal(settled, 1);
  assert.equal(packet.error_code, 'response_shape');
});

test('HTTP, redirects, fetch exceptions and body failures have constant sanitized errors and one send', async () => {
  for (const fetchImpl of [
    async () => response('private response body', {status: 401}),
    async () => response('private response body', {status: 429}),
    async () => response('private response body', {status: 500}),
    async () => ({status: 200, redirected: true}),
    async () => { throw Error(BASE + ' ' + TEST_AUTH + ' private response'); },
    async () => ({status: 200, body: {getReader() { throw Error('private body'); }}}),
  ]) {
    let calls = 0;
    const proxy = fixture(async (...args) => { calls++; return fetchImpl(...args); });
    await assert.rejects(proxy.complete(request()), error => error.message === 'kaggle_proxy_transport_or_response_unknown'
      && !error.message.includes(TEST_AUTH) && !error.message.includes(BASE) && error.request_outcome === 'unknown');
    assert.equal(calls, 1);
  }
});

test('response model must exactly bind the fixed request model', async () => {
  for (const model of [undefined, 'claude-sonnet-5-default', 'other']) {
    const body = payload(); body.model = model;
    await assert.rejects(fixture(async () => response(body)).complete(request()),
      error => error.message === 'kaggle_proxy_response_model_unknown');
  }
});

test('response bounds, invalid UTF-8, duplicate keys and malformed JSON are unknown', async () => {
  const tooLarge = new ReadableStream({start(controller) {
    controller.enqueue(new Uint8Array(MAX_RESPONSE_BYTES + 1)); controller.close();
  }});
  const responses = [
    new Response(tooLarge),
    response('{}', {headers: {'content-length': String(MAX_RESPONSE_BYTES + 1)}}),
    new Response(new Uint8Array([0xff])),
    response('{"usage":{},"usage":{}}'),
    response('{broken'),
    response('['.repeat(70) + ']'.repeat(70)),
  ];
  for (const item of responses) {
    await assert.rejects(fixture(async () => item).complete(request()),
      error => error.message === 'kaggle_proxy_transport_or_response_unknown');
  }
});

test('timeout aborts and never retries even when an injected fetch ignores cancellation', async () => {
  let calls = 0, signal;
  const proxy = fixture(async (url, options) => { calls++; signal = options.signal; return new Promise(() => {}); }, {timeoutMs: 2});
  await assert.rejects(proxy.complete(request()), error => error.message === 'kaggle_proxy_timeout_unknown');
  assert.equal(signal.aborted, true);
  assert.equal(calls, 1);
});

test('unsafe host URL, expired auth and invalid transport options refuse before fetch', () => {
  for (const baseUrl of [
    'http://reviewed-proxy.example', 'https://user:pass@reviewed-proxy.example',
    BASE + '?token=not-a-credential', BASE + '#fragment', BASE + '/v1',
    BASE + '/v1/chat/completions', BASE + '/chat/completions',
    BASE + '/%2e%2e/other', BASE + '/../other', 'https://reviewed-proxy.example:444',
  ]) {
    assert.throws(() => fixture(async () => assert.fail('fetch'), {baseUrl}),
      error => error.message === 'kaggle_proxy_host_configuration_refused');
  }
  for (const changes of [{apiKey: ''}, {apiKey: 'bad\nheader'}, {expiresAt: NOW}, {timeoutMs: 0},
    {timeoutMs: 120001}, {expiresAt: 'later'}]) {
    assert.throws(() => fixture(async () => assert.fail('fetch'), changes),
      error => error.message === 'kaggle_proxy_host_configuration_refused');
  }
});

test('request/schema/model/expiry errors before send have sanitized failures', async () => {
  let clock = NOW, calls = 0;
  const proxy = fixture(async () => { calls++; return response(); }, {now: () => clock});
  const wrong = request(); wrong.config.model = 'other'; rehash(wrong);
  await assert.rejects(proxy.complete(wrong), error => error.message === 'kaggle_proxy_request_refused');
  const huge = request(); huge.messages[0].content = 'x'.repeat(MAX_WIRE_BYTES); rehash(huge);
  await assert.rejects(proxy.complete(huge), error => error.message === 'kaggle_proxy_request_refused');
  clock = NOW + 60000;
  await assert.rejects(proxy.complete(request()), error => error.message === 'kaggle_proxy_request_refused');
  assert.equal(calls, 0);
});
