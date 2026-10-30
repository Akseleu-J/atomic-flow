/** Synthetic, read-only state comparison. No remote CAS or paid call. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {MODEL, digest} from '../tools/p07_model_broker.mjs';
import {simulateUnknownIntent} from '../tools/p07_unknown_intent_simulation.mjs';

const H = letter => letter.repeat(64);
function fixture() {
  const id = {campaign_id: 'p07_synthetic', owner: 'akseley', arm: 'atomic', task_id: 'task_1',
    framework_sha: 'a'.repeat(40), workflow_sha: 'b'.repeat(40), profile_sha256: H('c')};
  const config = {model: MODEL, max_input_tokens: 1000, max_output_tokens: 128, temperature: null, max_retries: 0};
  const request = {schema_version: 1, kind: 'p07_model_request', call_id: 'mq_' + '1'.repeat(32),
    identity: id, config, messages: [{role: 'user', content: 'synthetic only'}]};
  request.messages_sha256 = digest(request.messages); request.request_sha256 = digest(request);
  const profile = {campaign_id: id.campaign_id, framework_sha: id.framework_sha, workflow_sha: id.workflow_sha,
    profile_sha256: id.profile_sha256, model: MODEL, wire_config: config, owner_caps: {akseley: '1'},
    task_owners: {task_1: id.owner}, allowed_arms: ['atomic'], reserve_per_call_usd: '0.1',
    total_budget_usd: '2', arm_budget_usd: '0.5', task_budget_usd: '1', max_physical_calls: 40, deadline_unix_ms: 200000};
  const prior_spend_usd = {total: '0', owners: {akseley: '0'}};
  const profile_fingerprint = digest({profile, prior_spend_usd});
  const reservation = {reservation_id: 'paid_' + request.call_id, reserved_usd: '0.1', created_at_ms: 100,
    owner: id.owner, arm: id.arm, task_id: id.task_id};
  const ledger = {schema_version: 1, profile, prior_spend_usd, profile_fingerprint, checkpoint_sequence: 7,
    holds: [], calls: {[request.call_id]: {call_id: request.call_id, request_sha256: request.request_sha256,
      identity: id, reservation, settlement: null, broker: {state: 'unknown', request_sha256: request.request_sha256}}}};
  const checkpoint = {schema_version: 1, sequence: 7, state_sha256: digest(ledger), profile_fingerprint};
  const authority = {actor: 'synthetic_operator', owner: id.owner, account_binding_sha256: H('d'), authorization_sha256: H('e')};
  const evidenceBytes = Buffer.from('synthetic evidence, no provider attestation');
  const proposal = {schema_version: 1, kind: 'p07_unknown_resolution_proposal', call_id: request.call_id,
    request_sha256: request.request_sha256, identity_sha256: digest(id), profile_fingerprint,
    checkpoint_sequence: 7, state_sha256: checkpoint.state_sha256, reservation_id: reservation.reservation_id,
    actor: authority.actor, owner: id.owner, account_binding_sha256: authority.account_binding_sha256,
    authorization_sha256: authority.authorization_sha256, evidence_sha256: createHash('sha256').update(evidenceBytes).digest('hex'),
    proposed_outcome: 'provider_charge', proposed_cost_usd: '0.05'};
  return {request, ledger, checkpoint, authority, proposal, evidenceBytes};
}

test('create and exact replay are deterministic simulations and cannot authorize any effect', () => {
  const binding = fixture();
  const before = structuredClone(binding.ledger);
  const first = simulateUnknownIntent({binding, expectedSnapshot: null, observedSnapshot: null});
  assert.equal(first.operation, 'would_create');
  assert.equal(first.status, 'simulation_only_unverified');
  for (const flag of ['cas_performed', 'ledger_written', 'actor_authorized', 'provider_evidence_authenticated',
    'checkpoint_remote_verified', 'can_resolve', 'can_clear_hold', 'can_settle', 'can_send']) assert.equal(first[flag], false);
  const snapshot = structuredClone(first.proposed_snapshot);
  const replay = simulateUnknownIntent({binding, expectedSnapshot: snapshot, observedSnapshot: structuredClone(snapshot)});
  assert.equal(replay.operation, 'replay_noop');
  assert.equal(replay.proposed_snapshot_sha256, first.proposed_snapshot_sha256);
  assert.deepEqual(binding.ledger, before);
  assert.equal(JSON.stringify(first).includes(binding.evidenceBytes.toString()), false);
});

test('snapshot disagreement, stale creation and malformed snapshots refuse without mutation', () => {
  const binding = fixture();
  const state = simulateUnknownIntent({binding, expectedSnapshot: null, observedSnapshot: null}).proposed_snapshot;
  assert.throws(() => simulateUnknownIntent({binding, expectedSnapshot: null, observedSnapshot: state}), /simulation_refused/);
  assert.throws(() => simulateUnknownIntent({binding, expectedSnapshot: state, observedSnapshot: null}), /simulation_refused/);
  const changed = {...state, revision: 2};
  assert.throws(() => simulateUnknownIntent({binding, expectedSnapshot: changed, observedSnapshot: changed}), /simulation_refused/);
  const extra = {...state, authority: true};
  assert.throws(() => simulateUnknownIntent({binding, expectedSnapshot: extra, observedSnapshot: extra}), /simulation_refused/);
});

test('different evidence, actor, cost or outcome is a conflict for the same original call', () => {
  const original = fixture();
  const state = simulateUnknownIntent({binding: original, expectedSnapshot: null, observedSnapshot: null}).proposed_snapshot;
  for (const mutate of [
    binding => { binding.authority.actor = 'other_operator'; binding.proposal.actor = 'other_operator'; },
    binding => { binding.proposal.proposed_cost_usd = '0.06'; },
    binding => { binding.proposal.proposed_outcome = 'not_sent'; binding.proposal.proposed_cost_usd = null; },
    binding => { binding.evidenceBytes = Buffer.from('different synthetic evidence');
      binding.proposal.evidence_sha256 = createHash('sha256').update(binding.evidenceBytes).digest('hex'); },
  ]) {
    const candidate = fixture(); mutate(candidate);
    assert.throws(() => simulateUnknownIntent({binding: candidate, expectedSnapshot: state, observedSnapshot: state}), /simulation_refused/);
  }
});

test('tampered original or mismatched supplied checkpoint is refused before simulated comparison', () => {
  const binding = fixture();
  binding.ledger.holds.push('changed without matching checkpoint');
  assert.throws(() => simulateUnknownIntent({binding, expectedSnapshot: null, observedSnapshot: null}), /simulation_refused/);
});

test('simulation never invokes the network fetch surface', () => {
  const previous = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = () => { calls++; throw Error('network forbidden in simulation'); };
  try {
    const binding = fixture();
    const result = simulateUnknownIntent({binding, expectedSnapshot: null, observedSnapshot: null});
    assert.equal(result.cas_performed, false);
    assert.equal(result.ledger_written, false);
    assert.equal(calls, 0);
  } finally {
    globalThis.fetch = previous;
  }
});
