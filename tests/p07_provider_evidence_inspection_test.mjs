/** Negative evidence tests; all source names and evidence bytes are synthetic. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {MODEL, digest} from '../tools/p07_model_broker.mjs';
import {inspectProviderEvidence} from '../tools/p07_provider_evidence_inspection.mjs';

const H = c => c.repeat(64);
function fixture() {
  const identity = {campaign_id: 'p07_synthetic', owner: 'akseley', arm: 'atomic', task_id: 'task_1',
    framework_sha: 'a'.repeat(40), workflow_sha: 'b'.repeat(40), profile_sha256: H('c')};
  const request = {schema_version: 1, kind: 'p07_model_request', call_id: 'mq_' + '1'.repeat(32), identity,
    config: {model: MODEL, max_input_tokens: 1000, max_output_tokens: 128, temperature: null, max_retries: 0},
    messages: [{role: 'user', content: 'synthetic only'}]};
  request.messages_sha256 = digest(request.messages); request.request_sha256 = digest(request);
  const evidenceBytes = Buffer.from('fabricated source and provider receipt; not authenticated');
  const declaration = {schema_version: 1, kind: 'p07_unverified_provider_evidence', call_id: request.call_id,
    request_sha256: request.request_sha256, identity_sha256: digest(identity), owner: identity.owner, model: MODEL,
    account_binding_sha256: H('d'), source_label: 'purported_provider', provider_reference_sha256: H('e'),
    evidence_sha256: createHash('sha256').update(evidenceBytes).digest('hex')};
  return {request, declaration, evidenceBytes};
}

test('a complete forged claim remains unverified and grants no action', () => {
  const value = fixture();
  const before = structuredClone(value.declaration);
  const result = inspectProviderEvidence(value);
  assert.equal(result.status, 'unverified_declaration_only');
  for (const key of ['source_authenticated', 'account_verified', 'provider_reference_verified',
    'request_correlated', 'charge_verified', 'absence_verified', 'checkpoint_remote_verified',
    'actor_authorized', 'can_resolve', 'can_settle', 'can_clear_hold', 'can_send', 'can_retry']) {
    assert.equal(result[key], false, key);
  }
  assert.equal(JSON.stringify(result).includes(value.evidenceBytes.toString()), false);
  assert.deepEqual(value.declaration, before);
  assert.deepEqual(inspectProviderEvidence(value), result);
});

test('substituted identity, account, source or bytes fail the structural binding', () => {
  const cases = [
    x => { x.declaration.call_id = 'mq_' + '2'.repeat(32); },
    x => { x.declaration.request_sha256 = H('f'); },
    x => { x.declaration.identity_sha256 = H('f'); },
    x => { x.declaration.owner = 'umirbayulgaisha'; },
    x => { x.declaration.model = 'different_model'; },
    x => { x.declaration.account_binding_sha256 = 'malformed'; },
    x => { x.declaration.provider_reference_sha256 = 'not-a-hash'; },
    x => { x.declaration.source_label = 'unknown source with spaces'; },
    x => { x.declaration.evidence_sha256 = H('f'); },
    x => { x.evidenceBytes = Buffer.from('different'); },
    x => { x.evidenceBytes = Buffer.alloc(0); },
    x => { x.evidenceBytes = Buffer.alloc(1048577); },
    x => { x.declaration.source_authenticated = true; },
    x => { x.declaration.request_correlated = true; },
  ];
  for (const change of cases) {
    const value = fixture(); change(value);
    assert.throws(() => inspectProviderEvidence(value), /provider_evidence_declaration_refused/);
  }
});

test('local call id or same request alone cannot establish provider correlation', () => {
  const value = fixture();
  value.declaration.provider_reference_sha256 = null;
  const result = inspectProviderEvidence(value);
  assert.equal(result.request_correlated, false);
  assert.equal(result.provider_reference_verified, false);
  assert.equal(result.absence_verified, false);
  assert.equal(result.can_resolve, false);
});

test('independently forged account hash, absent attempt or zero-cost text is never an authorization', () => {
  for (const evidence of ['no matching call', 'cost_usd=0', 'attempt was never sent', 'fake-signed-receipt']) {
    const value = fixture();
    value.evidenceBytes = Buffer.from(evidence);
    value.declaration.evidence_sha256 = createHash('sha256').update(value.evidenceBytes).digest('hex');
    value.declaration.account_binding_sha256 = H('f');
    const result = inspectProviderEvidence(value);
    assert.equal(result.account_verified, false);
    assert.equal(result.charge_verified, false);
    assert.equal(result.absence_verified, false);
    assert.equal(result.can_settle, false);
  }
});

test('inspection does not call the network surface', () => {
  const saved = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = () => { calls++; throw Error('network prohibited'); };
  try {
    assert.equal(inspectProviderEvidence(fixture()).can_send, false);
    assert.equal(calls, 0);
  } finally { globalThis.fetch = saved; }
});
