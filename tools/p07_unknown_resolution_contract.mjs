/** Structural inspection of an UNKNOWN-resolution proposal only.
 *
 * This pure function neither authenticates any actor/provider evidence nor reads
 * a remote checkpoint, writes a ledger, clears a hold, settles a charge or
 * authorizes a physical send. Every successful result explicitly denies all
 * such authority. The caller must obtain and independently authenticate its
 * inputs; the provider's ability to furnish the asserted evidence is unknown.
 */
import {createHash} from 'node:crypto';
import {MAX_WIRE_BYTES, canonical, digest, validateRequest} from './p07_model_broker.mjs';
import {decimalUsd, nanoUsd} from './p07_paid_campaign_ledger.mjs';

const SHA = /^[a-f0-9]{64}$/;
const TAG = /^[A-Za-z0-9_.@-]{1,128}$/;
const MAX_LEDGER_BYTES = 32 * MAX_WIRE_BYTES;
const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value)
  && [Object.prototype, null].includes(Object.getPrototypeOf(value));
const exact = (value, keys) => plain(value) && Object.keys(value).sort().join('|') === [...keys].sort().join('|');
const sha = value => typeof value === 'string' && SHA.test(value);
const equal = (left, right) => canonical(left) === canonical(right);
const bad = () => new Error('unknown_resolution_declaration_refused');

/** Check a candidate against supplied original ledger/checkpoint snapshots.
 * `evidenceBytes` are hashed in memory and are not returned. SHA binding does
 * not imply authenticity, completeness, owner consent or provider correlation.
 */
export function inspectUnknownResolution({request, ledger, checkpoint, authority, proposal, evidenceBytes}) {
  try {
    validateRequest(request);
    if (!exact(ledger, ['schema_version', 'profile_fingerprint', 'profile', 'prior_spend_usd',
      'calls', 'holds', 'checkpoint_sequence']) || ledger.schema_version !== 1 || !plain(ledger.profile)
        || !plain(ledger.prior_spend_usd) || !plain(ledger.calls) || !Array.isArray(ledger.holds)
        || ledger.holds.some(value => typeof value !== 'string')
        || !Number.isSafeInteger(ledger.checkpoint_sequence) || ledger.checkpoint_sequence < 1
        || Buffer.byteLength(canonical(ledger)) > MAX_LEDGER_BYTES) throw bad();
    const profile = ledger.profile, id = request.identity;
    if (!exact(profile, ['campaign_id', 'model', 'framework_sha', 'workflow_sha', 'profile_sha256', 'wire_config',
      'total_budget_usd', 'owner_caps', 'arm_budget_usd', 'task_budget_usd', 'reserve_per_call_usd',
      'max_physical_calls', 'deadline_unix_ms', 'allowed_arms', 'task_owners'])
        || !exact(ledger.prior_spend_usd, ['total', 'owners']) || !plain(ledger.prior_spend_usd.owners)
        || !Number.isSafeInteger(profile.max_physical_calls) || profile.max_physical_calls < 1 || profile.max_physical_calls > 10000
        || Object.keys(ledger.calls).length > profile.max_physical_calls + 100
        || !Number.isSafeInteger(profile.deadline_unix_ms) || profile.deadline_unix_ms < 1) throw bad();
    const fingerprint = digest({profile, prior_spend_usd: ledger.prior_spend_usd});
    if (ledger.profile_fingerprint !== fingerprint || profile.campaign_id !== id.campaign_id
        || profile.framework_sha !== id.framework_sha || profile.workflow_sha !== id.workflow_sha
        || profile.profile_sha256 !== id.profile_sha256 || profile.model !== request.config.model
        || !equal(profile.wire_config, request.config) || !plain(profile.owner_caps)
        || !Object.hasOwn(profile.owner_caps, id.owner) || !plain(profile.task_owners)
        || profile.task_owners[id.task_id] !== id.owner || !Array.isArray(profile.allowed_arms)
        || !profile.allowed_arms.includes(id.arm)) throw bad();
    const ownerNames = Object.keys(profile.owner_caps).sort();
    const total = nanoUsd(profile.total_budget_usd), arm = nanoUsd(profile.arm_budget_usd);
    const task = nanoUsd(profile.task_budget_usd), reserve = nanoUsd(profile.reserve_per_call_usd);
    if (!ownerNames.length || !equal(ownerNames, Object.keys(ledger.prior_spend_usd.owners).sort())
        || ownerNames.some(owner => !['akseley', 'umirbayulgaisha'].includes(owner))
        || !profile.allowed_arms.length || new Set(profile.allowed_arms).size !== profile.allowed_arms.length
        || total <= 0n || total > 20_000_000_000n || reserve <= 0n || arm < reserve || task < arm || total < task
        || ownerNames.some(owner => nanoUsd(profile.owner_caps[owner]) < task || nanoUsd(profile.owner_caps[owner]) > total)
        || ownerNames.reduce((sum, owner) => sum + nanoUsd(profile.owner_caps[owner]), 0n) > total
        || nanoUsd(ledger.prior_spend_usd.total) > total
        || ownerNames.some(owner => nanoUsd(ledger.prior_spend_usd.owners[owner]) > nanoUsd(profile.owner_caps[owner]))
        || ownerNames.reduce((sum, owner) => sum + nanoUsd(ledger.prior_spend_usd.owners[owner]), 0n) > nanoUsd(ledger.prior_spend_usd.total)) throw bad();
    if (!exact(checkpoint, ['schema_version', 'sequence', 'state_sha256', 'profile_fingerprint'])
        || checkpoint.schema_version !== 1 || checkpoint.sequence !== ledger.checkpoint_sequence
        || !sha(checkpoint.state_sha256) || checkpoint.state_sha256 !== digest(ledger)
        || checkpoint.profile_fingerprint !== fingerprint) throw bad();
    const record = ledger.calls[request.call_id];
    if (!plain(record) || record.call_id !== request.call_id || record.request_sha256 !== request.request_sha256
        || !equal(record.identity, id) || !plain(record.broker) || record.broker.state !== 'unknown'
        || record.broker.request_sha256 !== request.request_sha256 || record.settlement !== null
        || !exact(record.reservation, ['reservation_id', 'reserved_usd', 'created_at_ms', 'owner', 'arm', 'task_id'])
        || record.reservation.reservation_id !== 'paid_' + request.call_id || record.reservation.owner !== id.owner
        || record.reservation.arm !== id.arm || record.reservation.task_id !== id.task_id
        || !Number.isSafeInteger(record.reservation.created_at_ms) || record.reservation.created_at_ms < 1
        || nanoUsd(record.reservation.reserved_usd) !== nanoUsd(profile.reserve_per_call_usd)) throw bad();
    if (!exact(authority, ['actor', 'owner', 'account_binding_sha256', 'authorization_sha256'])
        || typeof authority.actor !== 'string' || !TAG.test(authority.actor) || authority.owner !== id.owner
        || !sha(authority.account_binding_sha256) || !sha(authority.authorization_sha256)) throw bad();
    const keys = ['schema_version', 'kind', 'call_id', 'request_sha256', 'identity_sha256', 'profile_fingerprint',
      'checkpoint_sequence', 'state_sha256', 'reservation_id', 'actor', 'owner', 'account_binding_sha256',
      'authorization_sha256', 'evidence_sha256', 'proposed_outcome', 'proposed_cost_usd'];
    if (!exact(proposal, keys) || proposal.schema_version !== 1 || proposal.kind !== 'p07_unknown_resolution_proposal'
        || proposal.call_id !== request.call_id || proposal.request_sha256 !== request.request_sha256
        || proposal.identity_sha256 !== digest(id) || proposal.profile_fingerprint !== fingerprint
        || proposal.checkpoint_sequence !== checkpoint.sequence || proposal.state_sha256 !== checkpoint.state_sha256
        || proposal.reservation_id !== record.reservation.reservation_id || proposal.actor !== authority.actor
        || proposal.owner !== id.owner || proposal.account_binding_sha256 !== authority.account_binding_sha256
        || proposal.authorization_sha256 !== authority.authorization_sha256 || !sha(proposal.evidence_sha256)
        || !Buffer.isBuffer(evidenceBytes) || evidenceBytes.length < 1 || evidenceBytes.length > 1048576
        || createHash('sha256').update(evidenceBytes).digest('hex') !== proposal.evidence_sha256) throw bad();
    if (proposal.proposed_outcome === 'provider_charge') {
      if (decimalUsd(nanoUsd(proposal.proposed_cost_usd)) !== proposal.proposed_cost_usd) throw bad();
    } else if (proposal.proposed_outcome === 'not_sent') {
      if (proposal.proposed_cost_usd !== null) throw bad();
    } else throw bad();
    return {schema_version: 1, status: 'structurally_bound_unverified', proposal_sha256: digest(proposal),
      evidence_sha256: proposal.evidence_sha256, checkpoint_sequence: checkpoint.sequence,
      actor_authorized: false, provider_evidence_authenticated: false, checkpoint_remote_verified: false,
      can_resolve: false, can_clear_hold: false, can_settle: false, can_send: false};
  } catch {
    throw bad();
  }
}
