/** Pure simulation of a proposed UNKNOWN reconciliation intent comparison.
 *
 * There is no store, CAS, clock, I/O, lock or provider in this module. Both
 * snapshots come from the caller and are unverified. Returned data is a
 * design artifact only: it grants no authority to mutate a ledger, clear a
 * hold, settle, reconcile, send or retry. This is not a recovery protocol.
 */
import {canonical, digest} from './p07_model_broker.mjs';
import {inspectUnknownResolution} from './p07_unknown_resolution_contract.mjs';

const SHA = /^[a-f0-9]{64}$/;
const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value)
  && [Object.prototype, null].includes(Object.getPrototypeOf(value));
const exact = (value, keys) => plain(value) && Object.keys(value).sort().join('|') === [...keys].sort().join('|');
const invalid = () => new Error('unknown_intent_simulation_refused');

function validateSnapshot(value) {
  if (value === null) return;
  if (!exact(value, ['schema_version', 'kind', 'revision', 'original_anchor_sha256', 'proposal_sha256'])
      || value.schema_version !== 1 || value.kind !== 'p07_unknown_intent_simulation'
      || value.revision !== 1 || typeof value.original_anchor_sha256 !== 'string'
      || !SHA.test(value.original_anchor_sha256) || typeof value.proposal_sha256 !== 'string'
      || !SHA.test(value.proposal_sha256)) throw invalid();
}

/** Compare caller-supplied snapshots, without reading or writing a store.
 *
 * An identical proposal to an identical snapshot is a simulated no-op. A
 * different proposal for the same original call, including a different actor,
 * amount or evidence hash, fails closed. A stale expected snapshot also fails.
 * The snapshots and actor/provider evidence are not authenticated here.
 */
export function simulateUnknownIntent({binding, expectedSnapshot, observedSnapshot}) {
  try {
    const checked = inspectUnknownResolution(binding);
    validateSnapshot(expectedSnapshot);
    validateSnapshot(observedSnapshot);
    if (canonical(expectedSnapshot) !== canonical(observedSnapshot)) throw invalid();
    const request = binding.request;
    const anchor = {call_id: request.call_id, request_sha256: request.request_sha256,
      profile_fingerprint: binding.proposal.profile_fingerprint};
    const wanted = {schema_version: 1, kind: 'p07_unknown_intent_simulation', revision: 1,
      original_anchor_sha256: digest(anchor), proposal_sha256: checked.proposal_sha256};
    if (observedSnapshot !== null && canonical(observedSnapshot) !== canonical(wanted)) throw invalid();
    const operation = observedSnapshot === null ? 'would_create' : 'replay_noop';
    return {schema_version: 1, status: 'simulation_only_unverified', operation,
      expected_snapshot_sha256: expectedSnapshot === null ? null : digest(expectedSnapshot),
      observed_snapshot_sha256: observedSnapshot === null ? null : digest(observedSnapshot),
      proposed_snapshot: wanted, proposed_snapshot_sha256: digest(wanted),
      evidence_sha256: checked.evidence_sha256,
      actor_authorized: false, provider_evidence_authenticated: false, checkpoint_remote_verified: false,
      cas_performed: false, ledger_written: false, can_resolve: false, can_clear_hold: false,
      can_settle: false, can_send: false};
  } catch {
    throw invalid();
  }
}
