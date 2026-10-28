import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {MODEL, canonical, digest, processOne} from '../tools/p07_model_broker.mjs';
import {createPaidCampaignLedger, nanoUsd, decimalUsd} from '../tools/p07_paid_campaign_ledger.mjs';

const NOW = 100000;
let sequence = 0;
function profile(changes = {}) {
  return {campaign_id: 'p07_paid_reviewed', model: MODEL, framework_sha: 'a'.repeat(40),
    workflow_sha: 'b'.repeat(40), profile_sha256: 'c'.repeat(64),
    wire_config: {model: MODEL, max_input_tokens: 1000, max_output_tokens: 128, temperature: null, max_retries: 0},
    total_budget_usd: '20', owner_caps: {akseley: '9.50', umirbayulgaisha: '10'},
    arm_budget_usd: '0.5', task_budget_usd: '1', reserve_per_call_usd: '0.1',
    max_physical_calls: 40, deadline_unix_ms: 200000, allowed_arms: ['atomic', 'baseline'],
    task_owners: {sympy_1: 'akseley', requests_2: 'umirbayulgaisha'}, ...changes};
}
const prior = () => ({total: '0.035611375', owners: {akseley: '0.035611375', umirbayulgaisha: '0'}});
function checkpointStore() {
  let current = null;
  return {
    writes: [],
    async read() { return structuredClone(current); },
    async compareAndSwap(expected, next) {
      assert.equal(canonical(expected), canonical(current), 'checkpoint CAS expected original state');
      current = structuredClone(next);
      this.writes.push(structuredClone(next));
      return structuredClone(next);
    },
  };
}
function request(p, changes = {}) {
  const req = {schema_version: 1, kind: 'p07_model_request',
    call_id: 'mq_' + (++sequence).toString(16).padStart(32, '0'),
    identity: {campaign_id: p.campaign_id, owner: 'akseley', arm: 'atomic', task_id: 'sympy_1',
      framework_sha: p.framework_sha, workflow_sha: p.workflow_sha, profile_sha256: p.profile_sha256, ...changes},
    config: {...p.wire_config}, messages: [{role: 'user', content: 'public benchmark'}]};
  return rehash(req);
}
function rehash(req) {
  req.messages_sha256 = digest(req.messages); delete req.request_sha256; req.request_sha256 = digest(req);
  return req;
}
function physical(req, cost = '0.05') {
  return {content: '{"text":"candidate"}', tokens_in: 10, tokens_out: 8, cost_usd: cost, latency_ms: 1,
    truncated: false, cost_source: 'provider', resolved_backend: 'kaggle_model_proxy', resolved_model: MODEL,
    attempt_id: req.call_id};
}
async function fixture(t, p = profile(), booked = prior()) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'p07-paid-ledger-test-'));
  t.after(() => fs.rm(directory, {recursive: true, force: true}));
  const checkpoints = checkpointStore();
  const adapter = await createPaidCampaignLedger({directory, profile: p, priorSpendUsd: booked, checkpointStore: checkpoints, now: () => NOW});
  const h = {directory, adapter, checkpoints, profile: p, sends: 0, packets: new Map()};
  h.queue = {async put_response(id, packet) {
    if (h.packets.has(id)) assert.equal(canonical(h.packets.get(id)), canonical(packet));
    h.packets.set(id, structuredClone(packet));
  }};
  h.proxy = {async complete(req) {
    h.sends++;
    const saved = JSON.parse(await fs.readFile(path.join(directory, 'campaign.json'), 'utf8'));
    assert.equal(saved.calls[req.call_id].broker.state, 'claimed');
    assert.equal(saved.calls[req.call_id].reservation.reserved_usd, p.reserve_per_call_usd);
    const checkpoint = await checkpoints.read();
    assert.equal(checkpoint.state_sha256, digest(saved));
    assert.equal(checkpoint.sequence, saved.checkpoint_sequence);
    return physical(req);
  }};
  h.run = req => processOne({request: req, queue: h.queue, proxy: h.proxy, ...h.adapter});
  return h;
}

test('exact nanoUSD arithmetic never uses floating point rounding', () => {
  assert.equal(nanoUsd('0.035611375'), 35611375n);
  assert.equal(nanoUsd('1.000000001') + nanoUsd('0.000000009'), 1000000010n);
  assert.equal(decimalUsd(1000000010n), '1.00000001');
  assert.equal(decimalUsd(0n), '0');
  for (const invalid of [0.1, '1e-9', '-1', '0.0000000001', '01', 'NaN', ' 1']) {
    assert.throws(() => nanoUsd(invalid));
  }
});

test('durable reserve precedes one send; original identity and prior spend survive settlement', async t => {
  const h = await fixture(t), req = request(h.profile);
  assert.equal((await h.run(req)).state, 'completed');
  const saved = await h.adapter.snapshot(), call = saved.calls[req.call_id];
  assert.equal(saved.known_actual_usd, '0.05');
  assert.equal(saved.known_total_including_prior_usd, '0.085611375');
  assert.equal(saved.unresolved_reserved_usd, '0');
  assert.equal(call.request_sha256, req.request_sha256);
  assert.deepEqual(call.identity, req.identity);
  assert.equal(call.reservation.owner, req.identity.owner);
  assert.equal(call.reservation.arm, req.identity.arm);
  assert.equal(call.reservation.task_id, req.identity.task_id);
  assert.equal(call.reservation.created_at_ms, NOW);
  assert.equal(call.settlement.cost_source, 'provider');
  assert.equal(call.settlement.result_sha256, digest(physical(req)));
  await h.run(req);
  assert.equal(h.sends, 1);
  assert.equal((await h.adapter.snapshot()).reserved_call_count, 1);
});

test('unresolved original outcome holds all later arms, owners and tasks', async t => {
  const h = await fixture(t), original = request(h.profile);
  h.proxy.complete = async () => { h.sends++; throw Error('physical timeout'); };
  assert.equal((await h.run(original)).state, 'unknown');
  const next = request(h.profile, {owner: 'umirbayulgaisha', task_id: 'requests_2', arm: 'baseline'});
  assert.equal((await h.run(next)).state, 'unknown');
  await h.run(original);
  assert.equal(h.sends, 1);
  const state = await h.adapter.snapshot();
  assert.equal(state.unresolved_reserved_usd, '0.1');
  assert.equal(state.calls[next.call_id].reservation, null);
});

test('received malformed content still settles physical usage', async t => {
  const h = await fixture(t), req = request(h.profile);
  h.proxy.complete = async req => { h.sends++; throw {usage_result: physical(req, '0.012345678')}; };
  assert.equal((await h.run(req)).state, 'completed');
  assert.equal(h.packets.get(req.call_id).error_code, 'response_shape');
  assert.equal((await h.adapter.snapshot()).known_actual_usd, '0.012345678');
  assert.equal(h.sends, 1);
});

test('actual cost over reservation is retained exactly and blocks subsequent admission', async t => {
  const h = await fixture(t), req = request(h.profile);
  h.proxy.complete = async req => { h.sends++; return physical(req, '0.100000001'); };
  assert.equal((await h.run(req)).state, 'completed');
  const state = await h.adapter.snapshot();
  assert.equal(state.known_actual_usd, '0.100000001');
  assert.ok(state.holds.includes('actual_cost_exceeded_reservation'));
  assert.equal((await h.run(request(h.profile, {arm: 'baseline'}))).state, 'unknown');
  assert.equal(h.sends, 1);
});

test('arm and task limits remain tied to original task across new call IDs', async t => {
  const h = await fixture(t, profile({arm_budget_usd: '0.1', task_budget_usd: '0.2'}));
  h.proxy.complete = async req => { h.sends++; return physical(req, '0.08'); };
  assert.equal((await h.run(request(h.profile))).state, 'completed');
  assert.equal((await h.run(request(h.profile))).state, 'refused');
  assert.equal((await h.run(request(h.profile, {arm: 'baseline'}))).state, 'completed');
  assert.equal((await h.run(request(h.profile, {arm: 'baseline'}))).state, 'refused');
  assert.equal(h.sends, 2);
  assert.equal((await h.adapter.snapshot()).known_actual_usd, '0.16');
});

test('fixed total and owner caps include explicitly booked prior spend', async t => {
  const p = profile({total_budget_usd: '1', owner_caps: {akseley: '1'}, arm_budget_usd: '0.5', task_budget_usd: '1',
    task_owners: {sympy_1: 'akseley'}});
  const h = await fixture(t, p, {total: '0.95', owners: {akseley: '0.95'}});
  assert.equal((await h.run(request(p))).state, 'refused');
  assert.equal(h.sends, 0);
  assert.equal((await h.adapter.snapshot()).known_total_including_prior_usd, '0.95');
});

test('deadline and global physical cap refuse before send', async t => {
  const expired = await fixture(t, profile({deadline_unix_ms: NOW}));
  assert.equal((await expired.run(request(expired.profile))).state, 'refused');
  assert.equal(expired.sends, 0);
  const capped = await fixture(t, profile({max_physical_calls: 1}));
  await capped.run(request(capped.profile));
  assert.equal((await capped.run(request(capped.profile, {arm: 'baseline'}))).state, 'refused');
  assert.equal(capped.sends, 1);
});

test('unknown owner, wrong cohort, arbitrary arm and profile/config swaps are refused', async t => {
  const h = await fixture(t);
  for (const change of [{owner: 'umirbayulgaisha'}, {task_id: 'unregistered_task'}, {arm: 'new_reset_arm'},
    {campaign_id: 'other'}, {framework_sha: 'd'.repeat(40)}, {workflow_sha: 'e'.repeat(40)},
    {profile_sha256: 'f'.repeat(64)}]) {
    assert.equal((await h.run(request(h.profile, change))).state, 'refused');
  }
  const wrong = request(h.profile); wrong.config.max_output_tokens = 256; rehash(wrong);
  assert.equal((await h.run(wrong)).state, 'refused');
  assert.equal(h.sends, 0);
});

test('settlement missing actual provider provenance stays unknown with reservation retained', async t => {
  const h = await fixture(t), req = request(h.profile);
  h.proxy.complete = async req => { h.sends++; return {...physical(req), cost_source: 'estimated'}; };
  assert.equal((await h.run(req)).state, 'unknown');
  const state = await h.adapter.snapshot();
  assert.equal(state.known_actual_usd, '0');
  assert.equal(state.unresolved_reserved_usd, '0.1');
  assert.equal(state.calls[req.call_id].settlement, null);
});

test('completion response publication can reconcile after reopen without inference', async t => {
  const h = await fixture(t), req = request(h.profile), put = h.queue.put_response;
  h.queue.put_response = async () => { throw Error('publication failed'); };
  await assert.rejects(h.run(req));
  h.queue.put_response = put;
  const reopened = await createPaidCampaignLedger({directory: h.directory, profile: h.profile, priorSpendUsd: prior(), checkpointStore: h.checkpoints, now: () => NOW});
  await processOne({request: req, queue: h.queue, proxy: {complete: async () => assert.fail('replayed provider')}, ...reopened});
  assert.equal(h.sends, 1);
  assert.equal(h.packets.get(req.call_id).state, 'completed');
  assert.equal((await reopened.snapshot()).known_actual_usd, '0.05');
});

test('campaign lock is exclusive, stale locks are not stolen, callbacks require lock ownership', async t => {
  const h = await fixture(t), second = await createPaidCampaignLedger({directory: h.directory,
    profile: h.profile, priorSpendUsd: prior(), checkpointStore: h.checkpoints, now: () => NOW});
  await assert.rejects(h.adapter.admit(request(h.profile)), /exclusive lock/);
  await assert.rejects(h.adapter.journal.get(request(h.profile).call_id), /exclusive lock/);
  await h.adapter.journal.withLock(async () => {
    await assert.rejects(second.snapshot(), /held or stale/);
    await assert.rejects(h.adapter.journal.withLock(async () => {}), /nested/);
  });
  await fs.writeFile(path.join(h.directory, 'campaign.lock'), 'stale-original-owner');
  await assert.rejects(h.adapter.snapshot(), /held or stale/);
  assert.equal(await fs.readFile(path.join(h.directory, 'campaign.lock'), 'utf8'), 'stale-original-owner');
});

test('profile and historic-spend reopening cannot increase authority', async t => {
  const h = await fixture(t);
  for (const changed of [profile({arm_budget_usd: '0.6'}), profile({allowed_arms: ['atomic', 'baseline', 'critic_off']}),
    profile({task_owners: {...h.profile.task_owners, sympy_3: 'akseley'}})]) {
    await assert.rejects(createPaidCampaignLedger({directory: h.directory, profile: changed, priorSpendUsd: prior(), checkpointStore: h.checkpoints, now: () => NOW}));
  }
  await assert.rejects(createPaidCampaignLedger({directory: h.directory, profile: h.profile,
    priorSpendUsd: {total: '0', owners: {akseley: '0', umirbayulgaisha: '0'}}, checkpointStore: h.checkpoints, now: () => NOW}));
});

test('profile caps, precise decimals and all named owner prior entries are mandatory', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'p07-invalid-profile-test-'));
  t.after(() => fs.rm(directory, {recursive: true, force: true}));
  for (const changed of [profile({total_budget_usd: '20.000000001'}), profile({reserve_per_call_usd: '0'}),
    profile({owner_caps: {other: '1'}}), profile({allowed_arms: ['atomic', 'atomic']}),
    profile({task_owners: {sympy_1: 'other'}}), profile({model: 'other'}), profile({reserve_per_call_usd: '0.0000000001'})]) {
    await assert.rejects(createPaidCampaignLedger({directory, profile: changed, priorSpendUsd: prior(), checkpointStore: checkpointStore(), now: () => NOW}));
  }
  await assert.rejects(createPaidCampaignLedger({directory, profile: profile(),
    priorSpendUsd: {total: '0.035611375', owners: {akseley: '0.035611375'}}, checkpointStore: checkpointStore(), now: () => NOW}));
});

test('mutation of caller profile after construction cannot change approved authority', async t => {
  const p = profile(), h = await fixture(t, p);
  p.owner_caps.akseley = '20'; p.allowed_arms.push('extra'); p.task_owners.new_task = 'akseley';
  const req = request(profile(), {task_id: 'new_task', arm: 'extra'});
  assert.equal((await h.run(req)).state, 'refused');
  assert.equal(h.sends, 0);
});

test('settlement and original request conflicts fail closed', async t => {
  const h = await fixture(t), req = request(h.profile); await h.run(req);
  const changed = structuredClone(req); changed.messages[0].content = 'different'; rehash(changed);
  await assert.rejects(h.run(changed), /conflict/);
  await h.adapter.journal.withLock(async () => {
    await assert.rejects(h.adapter.settle({reservation_id: 'paid_' + req.call_id}, physical(req, '0.04'), req), /invalid|conflict/);
  });
  assert.equal((await h.adapter.snapshot()).known_actual_usd, '0.05');
});

test('durable checkpoint store is mandatory before initializing any paid ledger', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'p07-no-checkpoint-test-'));
  t.after(() => fs.rm(directory, {recursive: true, force: true}));
  for (const store of [undefined, {}, {read: async () => null}]) {
    await assert.rejects(createPaidCampaignLedger({directory, profile: profile(), priorSpendUsd: prior(),
      checkpointStore: store, now: () => NOW}), /checkpoint read\/CAS store required/);
  }
  assert.deepEqual(await fs.readdir(directory), []);
});

test('every checkpoint is hash-only, strictly sequenced and acknowledged before physical send', async t => {
  const h = await fixture(t), req = request(h.profile);
  let released = false;
  const original = h.checkpoints.compareAndSwap.bind(h.checkpoints);
  h.checkpoints.compareAndSwap = async (expected, next) => {
    const saved = JSON.parse(await fs.readFile(path.join(h.directory, 'campaign.json'), 'utf8'));
    assert.equal(next.state_sha256, digest(saved));
    assert.deepEqual(Object.keys(next).sort(), ['profile_fingerprint', 'schema_version', 'sequence', 'state_sha256']);
    assert.equal(next.sequence, (expected?.sequence ?? 0) + 1);
    const ack = await original(expected, next);
    if (saved.calls[req.call_id]?.broker.state === 'claimed') released = true;
    return ack;
  };
  const proxy = h.proxy.complete;
  h.proxy.complete = async req => { assert.equal(released, true); return proxy(req); };
  await h.run(req);
  assert.equal(h.sends, 1);
  const sequences = h.checkpoints.writes.map(value => value.sequence);
  assert.deepEqual(sequences, Array.from({length: sequences.length}, (_, index) => index + 1));
});

test('lost, stale, truncated or modified local accounting cannot reopen against authoritative checkpoint', async t => {
  for (const damage of ['missing', 'stale', 'truncated', 'modified']) {
    const h = await fixture(t), target = path.join(h.directory, 'campaign.json');
    const earlier = await fs.readFile(target);
    await h.run(request(h.profile));
    if (damage === 'missing') await fs.unlink(target);
    else if (damage === 'stale') await fs.writeFile(target, earlier);
    else if (damage === 'truncated') await fs.writeFile(target, '{');
    else {
      const saved = JSON.parse(await fs.readFile(target, 'utf8'));
      saved.calls = {};
      await fs.writeFile(target, canonical(saved));
    }
    await assert.rejects(createPaidCampaignLedger({directory: h.directory, profile: h.profile,
      priorSpendUsd: prior(), checkpointStore: h.checkpoints, now: () => NOW}));
    assert.equal(h.sends, 1);
  }
});

test('existing local ledger cannot initialize an absent authoritative checkpoint', async t => {
  const h = await fixture(t);
  const fresh = checkpointStore();
  await assert.rejects(createPaidCampaignLedger({directory: h.directory, profile: h.profile,
    priorSpendUsd: prior(), checkpointStore: fresh, now: () => NOW}), /authoritative checkpoint differ/);
  assert.equal(fresh.writes.length, 0);
});

test('CAS conflict, unknown outcome and null or malformed acknowledgement latch before inference without retry', async t => {
  for (const fault of ['conflict', 'unknown', 'null', 'malformed']) {
    const h = await fixture(t), req = request(h.profile);
    const compare = h.checkpoints.compareAndSwap.bind(h.checkpoints);
    let attempts = 0;
    h.checkpoints.compareAndSwap = async (expected, next) => {
      attempts++;
      if (fault === 'conflict') throw Error('CAS original changed');
      const actual = await compare(expected, next);
      if (fault === 'unknown') throw Error('remote committed but response lost');
      if (fault === 'null') return null;
      return {...actual, sequence: actual.sequence + 1};
    };
    await assert.rejects(h.run(req), /checkpoint outcome unknown/);
    await assert.rejects(h.run(req), /checkpoint outcome unknown/);
    await assert.rejects(h.adapter.snapshot(), /checkpoint outcome unknown/);
    assert.equal(attempts, 1);
    assert.equal(h.sends, 0);
  }
});

test('checkpoint failure after reservation but before claimed acknowledgement prevents physical send', async t => {
  const h = await fixture(t), req = request(h.profile);
  const compare = h.checkpoints.compareAndSwap.bind(h.checkpoints);
  h.checkpoints.compareAndSwap = async (expected, next) => {
    const saved = JSON.parse(await fs.readFile(path.join(h.directory, 'campaign.json'), 'utf8'));
    if (saved.calls[req.call_id]?.broker.state === 'claimed') throw Error('durable claim not acknowledged');
    return compare(expected, next);
  };
  await assert.rejects(h.run(req), /checkpoint outcome unknown/);
  assert.equal(h.sends, 0);
  await assert.rejects(createPaidCampaignLedger({directory: h.directory, profile: h.profile,
    priorSpendUsd: prior(), checkpointStore: h.checkpoints, now: () => NOW}), /authoritative checkpoint differ/);
});

test('snapshot rereads authoritative checkpoint rather than trusting an in-memory copy', async t => {
  const h = await fixture(t), read = h.checkpoints.read.bind(h.checkpoints);
  let reads = 0;
  h.checkpoints.read = async () => { reads++; const current = await read(); return {...current, state_sha256: '0'.repeat(64)}; };
  await assert.rejects(h.adapter.snapshot(), /authoritative checkpoint differ/);
  assert.equal(reads, 1);
  assert.equal(h.sends, 0);
});

test('unknown checkpoint reads latch admission and are not retried automatically', async t => {
  const h = await fixture(t);
  let reads = 0;
  h.checkpoints.read = async () => { reads++; throw Error('replica unreachable'); };
  await assert.rejects(h.run(request(h.profile)), /checkpoint read unknown/);
  await assert.rejects(h.run(request(h.profile)), /checkpoint outcome unknown/);
  assert.equal(reads, 1);
  assert.equal(h.sends, 0);
});
