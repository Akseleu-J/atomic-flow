/** Fault injection only: GitHub transport and provider are in-memory fakes. */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {MODEL, canonical, digest, processOne} from '../tools/p07_model_broker.mjs';
import {createPaidCampaignLedger} from '../tools/p07_paid_campaign_ledger.mjs';
import {createGitHubCheckpointStore} from '../tools/p07_github_checkpoint_store.mjs';

const NOW = 100000;
const campaignId = 'p07_restart_fake';
const branch = 'codex/p07-model-queue-paid-20261028-01';
const profile = {campaign_id: campaignId, model: MODEL, framework_sha: 'a'.repeat(40),
  workflow_sha: 'b'.repeat(40), profile_sha256: 'c'.repeat(64),
  wire_config: {model: MODEL, max_input_tokens: 1000, max_output_tokens: 128, temperature: null, max_retries: 0},
  total_budget_usd: '2', owner_caps: {akseley: '1', umirbayulgaisha: '1'},
  arm_budget_usd: '0.5', task_budget_usd: '1', reserve_per_call_usd: '0.1',
  max_physical_calls: 40, deadline_unix_ms: 200000, allowed_arms: ['atomic', 'baseline'],
  task_owners: {sympy_1: 'akseley', requests_2: 'umirbayulgaisha'}};
const prior = {total: '0', owners: {akseley: '0', umirbayulgaisha: '0'}};
const blob = raw => createHash('sha1').update('blob ' + raw.length + '\0').update(raw).digest('hex');
function request(id, owner, task, arm) {
  const value = {schema_version: 1, kind: 'p07_model_request', call_id: 'mq_' + id.repeat(32),
    identity: {campaign_id: campaignId, owner, arm, task_id: task,
      framework_sha: profile.framework_sha, workflow_sha: profile.workflow_sha, profile_sha256: profile.profile_sha256},
    config: {...profile.wire_config}, messages: [{role: 'user', content: 'synthetic fixture'}]};
  value.messages_sha256 = digest(value.messages);
  value.request_sha256 = digest(value);
  return value;
}
function result(req) {
  return {content: 'synthetic fixture', tokens_in: 10, tokens_out: 8, cost_usd: '0.05', latency_ms: 1,
    truncated: false, cost_source: 'provider', resolved_backend: 'kaggle_model_proxy',
    resolved_model: MODEL, attempt_id: req.call_id};
}
function matchesStage(record, stage) {
  if (!record) return false;
  if (stage === 'admission_intent') return record.broker.state === 'admitting' && !record.reservation;
  if (stage === 'reservation') return record.broker.state === 'admitting' && !!record.reservation;
  if (stage === 'claim') return record.broker.state === 'claimed';
  if (stage === 'result') return record.broker.state === 'received' && !record.settlement;
  if (stage === 'settlement') return record.broker.state === 'received' && !!record.settlement;
  return stage === 'completed' && record.broker.state === 'completed';
}

test('lost checkpoint acknowledgement at each paid transition remains fail-closed after reopen', async t => {
  for (const stage of ['admission_intent', 'reservation', 'claim', 'result', 'settlement', 'completed']) {
    const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'p07-restart-checkpoint-'));
    t.after(() => fs.rm(directory, {recursive: true, force: true}));
    const first = request('1', 'akseley', 'sympy_1', 'atomic');
    let remote = null, injected = false, sends = 0, faultWrites = 0;
    const github = async (_url, method, body) => {
      if (!method) return remote ? {status: 200, data: structuredClone(remote)} : {status: 404};
      assert.equal(method, 'PUT');
      assert.equal(body.branch, branch);
      if (remote ? body.sha !== remote.sha : Object.hasOwn(body, 'sha')) return {status: 409};
      const raw = Buffer.from(body.content, 'base64');
      const status = remote ? 200 : 201;
      remote = {type: 'file', encoding: 'base64', size: raw.length, content: raw.toString('base64'), sha: blob(raw)};
      const ledger = JSON.parse(await fs.readFile(path.join(directory, 'campaign.json'), 'utf8'));
      if (!injected && matchesStage(ledger.calls[first.call_id], stage)) {
        injected = true;
        faultWrites++;
        throw Error('synthetic acknowledgement lost after remote commit');
      }
      return {status, data: {content: {sha: remote.sha}, commit: {sha: 'd'.repeat(40)}}};
    };
    const store = createGitHubCheckpointStore({github, branch, campaignId});
    const adapter = await createPaidCampaignLedger({directory, profile, priorSpendUsd: prior, checkpointStore: store, now: () => NOW});
    const packets = new Map();
    const queue = {async put_response(id, packet) {
      if (packets.has(id)) assert.equal(canonical(packets.get(id)), canonical(packet));
      packets.set(id, structuredClone(packet));
    }};
    const proxy = {async complete(req) { sends++; return result(req); }};
    await assert.rejects(processOne({request: first, queue, proxy, ...adapter}), /checkpoint outcome unknown/);
    assert.equal(injected, true, stage);
    assert.equal(faultWrites, 1, stage);
    assert.equal(sends, ['result', 'settlement', 'completed'].includes(stage) ? 1 : 0, stage);
    await assert.rejects(processOne({request: first, queue, proxy, ...adapter}), /checkpoint outcome unknown/);
    const persisted = JSON.parse(await fs.readFile(path.join(directory, 'campaign.json'), 'utf8'));
    assert.equal((await store.read()).state_sha256, digest(persisted), stage);
    const restarted = await createPaidCampaignLedger({directory, profile, priorSpendUsd: prior, checkpointStore: store, now: () => NOW});
    const original = await processOne({request: first, queue, proxy, ...restarted});
    assert.equal(original.sent, false, stage);
    assert.equal(original.state, stage === 'completed' ? 'completed' : 'unknown', stage);
    if (stage !== 'completed') {
      const second = request('2', 'umirbayulgaisha', 'requests_2', 'baseline');
      const held = await processOne({request: second, queue, proxy, ...restarted});
      assert.equal(held.state, 'unknown', stage);
      assert.equal(held.sent, false, stage);
      const snapshot = await restarted.snapshot();
      assert.equal(snapshot.calls[first.call_id].broker.state, 'unknown', stage);
      assert.equal(snapshot.unresolved_reserved_usd, ['reservation', 'claim', 'result'].includes(stage) ? '0.1' : '0', stage);
      if (stage === 'settlement') assert.equal(snapshot.known_actual_usd, '0.05');
    }
    assert.equal(sends, ['result', 'settlement', 'completed'].includes(stage) ? 1 : 0, stage);
  }
});

test('uncommitted result checkpoint prevents reopen and never repeats a charged physical attempt', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'p07-restart-checkpoint-'));
  t.after(() => fs.rm(directory, {recursive: true, force: true}));
  const first = request('3', 'akseley', 'sympy_1', 'atomic');
  let remote = null, sends = 0, failures = 0;
  const github = async (_url, method, body) => {
    if (!method) return remote ? {status: 200, data: structuredClone(remote)} : {status: 404};
    const ledger = JSON.parse(await fs.readFile(path.join(directory, 'campaign.json'), 'utf8'));
    if (matchesStage(ledger.calls[first.call_id], 'result')) {
      failures++;
      throw Error('synthetic failure before remote commit');
    }
    if (remote ? body.sha !== remote.sha : Object.hasOwn(body, 'sha')) return {status: 409};
    const status = remote ? 200 : 201, raw = Buffer.from(body.content, 'base64');
    remote = {type: 'file', encoding: 'base64', size: raw.length, content: raw.toString('base64'), sha: blob(raw)};
    return {status, data: {content: {sha: remote.sha}, commit: {sha: 'd'.repeat(40)}}};
  };
  const store = createGitHubCheckpointStore({github, branch, campaignId});
  const adapter = await createPaidCampaignLedger({directory, profile, priorSpendUsd: prior, checkpointStore: store, now: () => NOW});
  const queue = {async put_response() { assert.fail('unknown checkpoint cannot publish'); }};
  const proxy = {async complete(req) { sends++; return result(req); }};
  await assert.rejects(processOne({request: first, queue, proxy, ...adapter}), /checkpoint outcome unknown/);
  await assert.rejects(processOne({request: first, queue, proxy, ...adapter}), /checkpoint outcome unknown/);
  const local = JSON.parse(await fs.readFile(path.join(directory, 'campaign.json'), 'utf8'));
  assert.notEqual((await store.read()).state_sha256, digest(local));
  await assert.rejects(createPaidCampaignLedger({directory, profile, priorSpendUsd: prior, checkpointStore: store, now: () => NOW}),
    /authoritative checkpoint differ/);
  assert.equal(failures, 1);
  assert.equal(sends, 1);
});

test('lost unknown publication survives reopen, retains reservation and blocks every owner', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'p07-restart-checkpoint-'));
  t.after(() => fs.rm(directory, {recursive: true, force: true}));
  const first = request('4', 'akseley', 'sympy_1', 'atomic');
  const second = request('5', 'umirbayulgaisha', 'requests_2', 'baseline');
  let remote = null, sends = 0, publishAttempts = 0;
  const github = async (_url, method, body) => {
    if (!method) return remote ? {status: 200, data: structuredClone(remote)} : {status: 404};
    assert.equal(method, 'PUT');
    assert.equal(body.branch, branch);
    if (remote ? body.sha !== remote.sha : Object.hasOwn(body, 'sha')) return {status: 409};
    const status = remote ? 200 : 201, raw = Buffer.from(body.content, 'base64');
    remote = {type: 'file', encoding: 'base64', size: raw.length, content: raw.toString('base64'), sha: blob(raw)};
    return {status, data: {content: {sha: remote.sha}, commit: {sha: 'd'.repeat(40)}}};
  };
  const store = createGitHubCheckpointStore({github, branch, campaignId});
  const adapter = await createPaidCampaignLedger({directory, profile, priorSpendUsd: prior, checkpointStore: store, now: () => NOW});
  const proxy = {async complete() { sends++; throw Error('synthetic unknown physical outcome'); }};
  const unavailableQueue = {async put_response() { publishAttempts++; throw Error('synthetic publication unavailable'); }};
  await assert.rejects(processOne({request: first, queue: unavailableQueue, proxy, ...adapter}), /publication unavailable/);
  assert.equal(sends, 1);
  assert.equal(publishAttempts, 1);
  const persisted = JSON.parse(await fs.readFile(path.join(directory, 'campaign.json'), 'utf8'));
  assert.equal((await store.read()).state_sha256, digest(persisted));
  assert.equal(persisted.calls[first.call_id].broker.state, 'unknown');
  assert.equal(persisted.calls[first.call_id].reservation.reserved_usd, '0.1');
  const restarted = await createPaidCampaignLedger({directory, profile, priorSpendUsd: prior, checkpointStore: store, now: () => NOW});
  const packets = new Map();
  const queue = {async put_response(id, packet) { packets.set(id, structuredClone(packet)); }};
  const retried = await processOne({request: first, queue, proxy, ...restarted});
  assert.equal(retried.state, 'unknown');
  assert.equal(retried.sent, false);
  assert.equal(packets.get(first.call_id).state, 'unknown');
  const held = await processOne({request: second, queue, proxy, ...restarted});
  assert.equal(held.state, 'unknown');
  assert.equal(held.sent, false);
  const snapshot = await restarted.snapshot();
  assert.equal(snapshot.unresolved_reserved_usd, '0.1');
  assert.equal(snapshot.known_actual_usd, '0');
  assert.equal(snapshot.calls[first.call_id].settlement, null);
  assert.equal(sends, 1);
});
