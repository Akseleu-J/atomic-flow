/** Synthetic structure checks only: no real actor, network, provider or ledger I/O. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import * as fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {MODEL, digest, processOne} from '../tools/p07_model_broker.mjs';
import {createPaidCampaignLedger} from '../tools/p07_paid_campaign_ledger.mjs';
import {inspectUnknownResolution} from '../tools/p07_unknown_resolution_contract.mjs';

const H = letter => letter.repeat(64);
function fixture() {
  const id = {campaign_id: 'p07_synthetic', owner: 'akseley', arm: 'atomic', task_id: 'task_1',
    framework_sha: 'a'.repeat(40), workflow_sha: 'b'.repeat(40), profile_sha256: H('c')};
  const config = {model: MODEL, max_input_tokens: 1000, max_output_tokens: 128, temperature: null, max_retries: 0};
  const request = {schema_version: 1, kind: 'p07_model_request', call_id: 'mq_' + '1'.repeat(32),
    identity: id, config, messages: [{role: 'user', content: 'synthetic only'}]};
  request.messages_sha256 = digest(request.messages);
  request.request_sha256 = digest(request);
  const profile = {campaign_id: id.campaign_id, framework_sha: id.framework_sha, workflow_sha: id.workflow_sha,
    profile_sha256: id.profile_sha256, model: MODEL, wire_config: config, owner_caps: {akseley: '1'},
    task_owners: {task_1: 'akseley'}, allowed_arms: ['atomic'], reserve_per_call_usd: '0.1',
    total_budget_usd: '2', arm_budget_usd: '0.5', task_budget_usd: '1', max_physical_calls: 40,
    deadline_unix_ms: 200000};
  const prior_spend_usd = {total: '0', owners: {akseley: '0'}};
  const profile_fingerprint = digest({profile, prior_spend_usd});
  const reservation = {reservation_id: 'paid_' + request.call_id, reserved_usd: '0.1', created_at_ms: 100,
    owner: id.owner, arm: id.arm, task_id: id.task_id};
  const ledger = {schema_version: 1, profile, prior_spend_usd, profile_fingerprint, checkpoint_sequence: 7,
    holds: [], calls: {[request.call_id]: {call_id: request.call_id, request_sha256: request.request_sha256,
      identity: id, reservation, settlement: null, broker: {state: 'unknown', request_sha256: request.request_sha256}}}};
  const checkpoint = {schema_version: 1, sequence: 7, state_sha256: digest(ledger), profile_fingerprint};
  const authority = {actor: 'synthetic_operator', owner: id.owner, account_binding_sha256: H('d'), authorization_sha256: H('e')};
  const evidenceBytes = Buffer.from('synthetic evidence; no provider attestation');
  const proposal = {schema_version: 1, kind: 'p07_unknown_resolution_proposal', call_id: request.call_id,
    request_sha256: request.request_sha256, identity_sha256: digest(id), profile_fingerprint,
    checkpoint_sequence: 7, state_sha256: checkpoint.state_sha256, reservation_id: reservation.reservation_id,
    actor: authority.actor, owner: id.owner, account_binding_sha256: authority.account_binding_sha256,
    authorization_sha256: authority.authorization_sha256, evidence_sha256: createHash('sha256').update(evidenceBytes).digest('hex'),
    proposed_outcome: 'provider_charge', proposed_cost_usd: '0.05'};
  return {request, ledger, checkpoint, authority, proposal, evidenceBytes};
}

test('bound declaration is deterministic but grants no authorization or ledger action', () => {
  const value = fixture(), before = structuredClone(value.ledger);
  const first = inspectUnknownResolution(value), second = inspectUnknownResolution(value);
  assert.deepEqual(first, second);
  assert.equal(first.status, 'structurally_bound_unverified');
  for (const key of ['actor_authorized', 'provider_evidence_authenticated', 'checkpoint_remote_verified',
    'can_resolve', 'can_clear_hold', 'can_settle', 'can_send']) assert.equal(first[key], false);
  assert.deepEqual(value.ledger, before);
  assert.equal(JSON.stringify(first).includes('synthetic evidence'), false);
});

test('identity, actor, owner, account, evidence and checkpoint substitutions fail closed', () => {
  const mutations = [
    value => { value.proposal.call_id = 'mq_' + '2'.repeat(32); },
    value => { value.proposal.request_sha256 = H('f'); },
    value => { value.proposal.identity_sha256 = H('f'); },
    value => { value.proposal.actor = 'other_operator'; },
    value => { value.proposal.owner = 'umirbayulgaisha'; },
    value => { value.proposal.account_binding_sha256 = H('f'); },
    value => { value.proposal.authorization_sha256 = H('f'); },
    value => { value.authority.owner = 'umirbayulgaisha'; },
    value => { value.authority.actor = ''; },
    value => { value.authority.account_binding_sha256 = 'bad'; },
    value => { value.evidenceBytes = Buffer.from('different evidence'); },
    value => { value.evidenceBytes = Buffer.alloc(0); },
    value => { value.evidenceBytes = Buffer.alloc(1048577); },
    value => { value.checkpoint.state_sha256 = H('f'); },
    value => { value.checkpoint.sequence++; },
    value => { value.ledger.holds.push('tampered'); },
    value => { value.proposal.extra = 'unreviewed'; },
  ];
  for (const mutate of mutations) {
    const value = fixture(); mutate(value);
    assert.throws(() => inspectUnknownResolution(value), /unknown_resolution_declaration_refused/);
  }
});

test('rejects missing original reserve, other state and conflicting settled charges', () => {
  for (const change of [
    record => { record.reservation = null; },
    record => { record.reservation.reserved_usd = '0.2'; },
    record => { record.reservation.owner = 'umirbayulgaisha'; },
    record => { record.broker.state = 'completed'; },
    record => { record.settlement = {cost_usd: '0.05'}; },
  ]) {
    const value = fixture(); change(value.ledger.calls[value.request.call_id]);
    value.checkpoint.state_sha256 = digest(value.ledger);
    value.proposal.state_sha256 = value.checkpoint.state_sha256;
    assert.throws(() => inspectUnknownResolution(value), /unknown_resolution_declaration_refused/);
  }
});

test('candidate cost is canonical exact nanoUSD and unsent claims carry no fabricated zero', () => {
  for (const invalid of ['0.050', '-1', '0.0000000001', 0.05, null]) {
    const value = fixture(); value.proposal.proposed_cost_usd = invalid;
    assert.throws(() => inspectUnknownResolution(value), /unknown_resolution_declaration_refused/);
  }
  const unsent = fixture(); unsent.proposal.proposed_outcome = 'not_sent'; unsent.proposal.proposed_cost_usd = null;
  assert.equal(inspectUnknownResolution(unsent).can_resolve, false);
  unsent.proposal.proposed_cost_usd = '0';
  assert.throws(() => inspectUnknownResolution(unsent), /unknown_resolution_declaration_refused/);
});

test('real local ledger UNKNOWN snapshot binds to an in-memory checkpoint without resolving it', async t => {
  const value = fixture();
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'p07-unknown-declaration-'));
  t.after(() => fs.rm(directory, {recursive: true, force: true}));
  let remote = null, sends = 0;
  const checkpointStore = {
    async read() { return structuredClone(remote); },
    async compareAndSwap(expected, next) {
      assert.deepEqual(expected, remote);
      remote = structuredClone(next);
      return structuredClone(next);
    },
  };
  const adapter = await createPaidCampaignLedger({directory, profile: value.ledger.profile,
    priorSpendUsd: value.ledger.prior_spend_usd, checkpointStore, now: () => 100});
  const result = await processOne({request: value.request,
    queue: {async put_response() {}}, proxy: {async complete() { sends++; throw Error('synthetic unknown'); }}, ...adapter});
  assert.equal(result.state, 'unknown');
  value.ledger = JSON.parse(await fs.readFile(path.join(directory, 'campaign.json'), 'utf8'));
  value.checkpoint = structuredClone(remote);
  value.proposal.checkpoint_sequence = remote.sequence;
  value.proposal.state_sha256 = remote.state_sha256;
  const checked = inspectUnknownResolution(value);
  assert.equal(checked.can_resolve, false);
  const snapshot = await adapter.snapshot();
  assert.equal(snapshot.unresolved_reserved_usd, '0.1');
  assert.equal(snapshot.calls[value.request.call_id].broker.state, 'unknown');
  assert.equal(snapshot.calls[value.request.call_id].settlement, null);
  assert.equal(sends, 1);
});
