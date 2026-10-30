/** Synthetic, read-only UNKNOWN snapshot classification; no network/provider. */
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {MODEL, digest, processOne} from '../tools/p07_model_broker.mjs';
import {createPaidCampaignLedger} from '../tools/p07_paid_campaign_ledger.mjs';
import {classifyUnknownSnapshot} from '../tools/p07_unknown_snapshot_classifier.mjs';

function fixture(category) {
  const identity = {campaign_id: 'p07_test', owner: 'akseley', arm: 'atomic', task_id: 'task_1',
    framework_sha: 'a'.repeat(40), workflow_sha: 'b'.repeat(40), profile_sha256: 'c'.repeat(64)};
  const config = {model: MODEL, max_input_tokens: 1000, max_output_tokens: 128, temperature: null, max_retries: 0};
  const request = {schema_version: 1, kind: 'p07_model_request', call_id: 'mq_' + '1'.repeat(32),
    identity, config, messages: [{role: 'user', content: 'fixture'}]};
  request.messages_sha256 = digest(request.messages); request.request_sha256 = digest(request);
  const profile = {campaign_id: identity.campaign_id, framework_sha: identity.framework_sha,
    workflow_sha: identity.workflow_sha, profile_sha256: identity.profile_sha256, model: MODEL, wire_config: config,
    owner_caps: {akseley: '1'}, task_owners: {task_1: 'akseley'}, allowed_arms: ['atomic'],
    reserve_per_call_usd: '0.1', total_budget_usd: '2', arm_budget_usd: '0.5', task_budget_usd: '1',
    max_physical_calls: 40, deadline_unix_ms: 200000};
  const prior_spend_usd = {total: '0', owners: {akseley: '0'}};
  const profile_fingerprint = digest({profile, prior_spend_usd});
  const reservation = category === 'no_reservation_recorded' ? null : {reservation_id: 'paid_' + request.call_id,
    reserved_usd: '0.1', created_at_ms: 100, owner: identity.owner, arm: identity.arm, task_id: identity.task_id};
  const broker = {state: 'unknown', request_sha256: request.request_sha256};
  let settlement = null;
  if (category === 'settlement_recorded') {
    broker.result = {content: 'fixture', tokens_in: 10, tokens_out: 8, cost_usd: '0.05', latency_ms: 1,
      truncated: false, cost_source: 'provider', resolved_backend: 'kaggle_model_proxy',
      resolved_model: MODEL, attempt_id: request.call_id};
    settlement = {cost_usd: '0.05', tokens_in: 10, tokens_out: 8, cost_source: 'provider',
      result_sha256: digest(broker.result)};
  }
  const ledger = {schema_version: 1, profile, prior_spend_usd, profile_fingerprint, checkpoint_sequence: 7,
    holds: [], calls: {[request.call_id]: {call_id: request.call_id, request_sha256: request.request_sha256,
      identity, broker, reservation, settlement}}};
  const checkpoint = {schema_version: 1, sequence: 7, state_sha256: digest(ledger), profile_fingerprint};
  return {request, ledger, checkpoint};
}
const rebind = value => { value.checkpoint.state_sha256 = digest(value.ledger); };

test('three original snapshot classes are deterministic and never grant authority', () => {
  for (const category of ['no_reservation_recorded', 'reserved_unsettled', 'settlement_recorded']) {
    const input = fixture(category), before = structuredClone(input);
    const result = classifyUnknownSnapshot(input);
    assert.deepEqual(result, classifyUnknownSnapshot(input));
    assert.equal(result.category, category);
    for (const flag of ['provider_evidence_authenticated', 'checkpoint_remote_verified', 'actor_authorized',
      'ledger_written', 'can_resolve', 'can_clear_hold', 'can_settle', 'can_send']) assert.equal(result[flag], false);
    assert.deepEqual(input, before);
  }
});

test('rejects changed request, checkpoint, owner, identity, state or reservation', () => {
  const cases = [
    x => { x.request.messages[0].content = 'modified'; },
    x => { x.checkpoint.state_sha256 = 'f'.repeat(64); },
    x => { x.checkpoint.sequence++; },
    x => { x.ledger.holds.push('changed'); },
    x => { x.ledger.calls[x.request.call_id].identity.owner = 'umirbayulgaisha'; rebind(x); },
    x => { x.ledger.calls[x.request.call_id].broker.state = 'completed'; rebind(x); },
    x => { x.ledger.calls[x.request.call_id].reservation.owner = 'umirbayulgaisha'; rebind(x); },
    x => { x.ledger.calls[x.request.call_id].reservation.reserved_usd = '0.2'; rebind(x); },
    x => { x.ledger.calls[x.request.call_id].broker.reservation_id = 'paid_other'; rebind(x); },
    x => { x.ledger.calls[x.request.call_id].reservation.extra = true; rebind(x); },
  ];
  for (const mutate of cases) {
    const input = fixture('reserved_unsettled'); mutate(input);
    assert.throws(() => classifyUnknownSnapshot(input), /classification_refused/);
  }
});

test('settlement requires original reservation and a matching stored provider result', () => {
  for (const mutate of [
    r => { r.reservation = null; },
    r => { r.settlement.cost_usd = '0.06'; },
    r => { r.settlement.tokens_out++; },
    r => { r.settlement.result_sha256 = 'f'.repeat(64); },
    r => { r.broker.result.attempt_id = 'mq_' + '2'.repeat(32); r.settlement.result_sha256 = digest(r.broker.result); },
    r => { r.broker.result.cost_source = 'estimated'; r.settlement.result_sha256 = digest(r.broker.result); },
  ]) {
    const input = fixture('settlement_recorded'); mutate(input.ledger.calls[input.request.call_id]); rebind(input);
    assert.throws(() => classifyUnknownSnapshot(input), /classification_refused/);
  }
});

test('real local UNKNOWN reservation classifies without writes, sends or hold release', async t => {
  const input = fixture('reserved_unsettled');
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'p07-unknown-classify-'));
  t.after(() => fs.rm(directory, {recursive: true, force: true}));
  let checkpoint = null, sends = 0;
  const checkpointStore = {async read() { return structuredClone(checkpoint); },
    async compareAndSwap(expected, next) { assert.deepEqual(expected, checkpoint); checkpoint = structuredClone(next); return structuredClone(next); }};
  const adapter = await createPaidCampaignLedger({directory, profile: input.ledger.profile,
    priorSpendUsd: input.ledger.prior_spend_usd, checkpointStore, now: () => 100});
  await processOne({request: input.request, queue: {async put_response() {}},
    proxy: {async complete() { sends++; throw Error('synthetic unknown'); }}, ...adapter});
  const file = path.join(directory, 'campaign.json'), before = await fs.readFile(file);
  const result = classifyUnknownSnapshot({request: input.request, ledger: JSON.parse(before), checkpoint});
  assert.equal(result.category, 'reserved_unsettled');
  assert.deepEqual(await fs.readFile(file), before);
  assert.equal((await adapter.snapshot()).unresolved_reserved_usd, '0.1');
  assert.equal(sends, 1);
});

test('classifies actual fault/restart snapshots without repeating a physical send', async t => {
  for (const [stage, category, expectedSends] of [
    ['admission_intent', 'no_reservation_recorded', 0],
    ['reservation', 'reserved_unsettled', 0],
    ['settlement', 'settlement_recorded', 1],
  ]) {
    const input = fixture('reserved_unsettled');
    const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'p07-classify-restart-'));
    t.after(() => fs.rm(directory, {recursive: true, force: true}));
    const file = path.join(directory, 'campaign.json');
    let remote = null, injected = false, sends = 0;
    const checkpointStore = {
      async read() { return structuredClone(remote); },
      async compareAndSwap(expected, next) {
        assert.deepEqual(expected, remote);
        remote = structuredClone(next);
        const ledger = JSON.parse(await fs.readFile(file, 'utf8'));
        const r = ledger.calls[input.request.call_id];
        const matches = r && ((stage === 'admission_intent' && r.broker.state === 'admitting' && !r.reservation)
          || (stage === 'reservation' && r.broker.state === 'admitting' && !!r.reservation)
          || (stage === 'settlement' && r.broker.state === 'received' && !!r.settlement));
        if (matches && !injected) { injected = true; throw Error('synthetic lost checkpoint acknowledgement'); }
        return structuredClone(next);
      },
    };
    const options = {directory, profile: input.ledger.profile, priorSpendUsd: input.ledger.prior_spend_usd,
      checkpointStore, now: () => 100};
    let adapter = await createPaidCampaignLedger(options);
    const queue = {async put_response() {}};
    const proxy = {async complete(req) { sends++; return {content: 'synthetic', tokens_in: 10, tokens_out: 8,
      cost_usd: '0.05', latency_ms: 1, truncated: false, cost_source: 'provider',
      resolved_backend: 'kaggle_model_proxy', resolved_model: MODEL, attempt_id: req.call_id}; }};
    await assert.rejects(processOne({request: input.request, queue, proxy, ...adapter}), /checkpoint outcome unknown/);
    assert.equal(injected, true);
    adapter = await createPaidCampaignLedger(options);
    const replay = await processOne({request: input.request, queue, proxy, ...adapter});
    assert.equal(replay.state, 'unknown'); assert.equal(replay.sent, false);
    const before = await fs.readFile(file);
    const result = classifyUnknownSnapshot({request: input.request, ledger: JSON.parse(before), checkpoint: remote});
    assert.equal(result.category, category, stage);
    assert.equal(result.can_send, false); assert.equal(result.can_resolve, false);
    assert.deepEqual(await fs.readFile(file), before);
    assert.equal(sends, expectedSends, stage);
  }
});
