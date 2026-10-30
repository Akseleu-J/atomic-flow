/** Classify a caller-supplied UNKNOWN ledger snapshot; no I/O or authority.
 *
 * This checks structural bindings only. It does not validate the full ledger,
 * authenticate a checkpoint, prove a physical request was unsent or a charge
 * was actually billed, reconcile anything, clear a hold, or permit a send.
 */
import {canonical, digest, MODEL, validateRequest} from './p07_model_broker.mjs';
import {decimalUsd, nanoUsd} from './p07_paid_campaign_ledger.mjs';

const SHA = /^[a-f0-9]{64}$/;
const plain = x => x !== null && typeof x === 'object' && !Array.isArray(x)
  && Object.getPrototypeOf(x) === Object.prototype;
const exact = (x, keys) => plain(x) && canonical(Object.keys(x).sort()) === canonical([...keys].sort());
const same = (a, b) => canonical(a) === canonical(b);
const bad = () => new Error('unknown_snapshot_classification_refused');

export function classifyUnknownSnapshot({request, ledger, checkpoint}) {
  try {
    validateRequest(request);
    if (!exact(ledger, ['schema_version', 'profile_fingerprint', 'profile', 'prior_spend_usd',
      'calls', 'holds', 'checkpoint_sequence']) || ledger.schema_version !== 1
      || !plain(ledger.profile) || !plain(ledger.prior_spend_usd) || !plain(ledger.calls)
      || !Array.isArray(ledger.holds) || ledger.holds.some(x => typeof x !== 'string')
      || !Number.isSafeInteger(ledger.checkpoint_sequence) || ledger.checkpoint_sequence < 1
      || Buffer.byteLength(canonical(ledger)) > 32 * 1024 * 1024) throw bad();
    const p = ledger.profile, id = request.identity;
    const fingerprint = digest({profile: p, prior_spend_usd: ledger.prior_spend_usd});
    if (ledger.profile_fingerprint !== fingerprint || p.campaign_id !== id.campaign_id
      || p.framework_sha !== id.framework_sha || p.workflow_sha !== id.workflow_sha
      || p.profile_sha256 !== id.profile_sha256 || p.model !== request.config.model
      || !same(p.wire_config, request.config) || !plain(p.owner_caps) || !Object.hasOwn(p.owner_caps, id.owner)
      || !plain(p.task_owners) || p.task_owners[id.task_id] !== id.owner
      || !Array.isArray(p.allowed_arms) || !p.allowed_arms.includes(id.arm)) throw bad();
    if (!exact(checkpoint, ['schema_version', 'sequence', 'state_sha256', 'profile_fingerprint'])
      || checkpoint.schema_version !== 1 || checkpoint.sequence !== ledger.checkpoint_sequence
      || checkpoint.profile_fingerprint !== fingerprint || checkpoint.state_sha256 !== digest(ledger)) throw bad();
    const record = ledger.calls[request.call_id];
    if (!exact(record, ['call_id', 'request_sha256', 'identity', 'broker', 'reservation', 'settlement'])
      || record.call_id !== request.call_id || record.request_sha256 !== request.request_sha256
      || !same(record.identity, id) || !plain(record.broker) || record.broker.state !== 'unknown'
      || record.broker.request_sha256 !== request.request_sha256
      || (record.broker.identity !== undefined && !same(record.broker.identity, id))) throw bad();
    let category;
    if (record.reservation === null) {
      if (record.settlement !== null || record.broker.result !== undefined
        || record.broker.reservation_id !== undefined) throw bad();
      category = 'no_reservation_recorded';
    } else {
      const r = record.reservation;
      if (!exact(r, ['reservation_id', 'reserved_usd', 'created_at_ms', 'owner', 'arm', 'task_id'])
        || r.reservation_id !== 'paid_' + request.call_id || r.owner !== id.owner || r.arm !== id.arm
        || r.task_id !== id.task_id || !Number.isSafeInteger(r.created_at_ms) || r.created_at_ms < 1
        || nanoUsd(r.reserved_usd) !== nanoUsd(p.reserve_per_call_usd)
        || (record.broker.reservation_id !== undefined && record.broker.reservation_id !== r.reservation_id)) throw bad();
      if (record.settlement === null) category = 'reserved_unsettled';
      else {
        const s = record.settlement, result = record.broker.result;
        if (!exact(s, ['cost_usd', 'tokens_in', 'tokens_out', 'cost_source', 'result_sha256'])
          || !plain(result) || !SHA.test(s.result_sha256 || '') || s.result_sha256 !== digest(result)
          || decimalUsd(nanoUsd(s.cost_usd)) !== s.cost_usd || nanoUsd(result.cost_usd) !== nanoUsd(s.cost_usd)
          || s.cost_source !== 'provider' || result.cost_source !== 'provider'
          || result.attempt_id !== request.call_id || result.resolved_model !== MODEL
          || result.resolved_backend !== 'kaggle_model_proxy'
          || !Number.isSafeInteger(s.tokens_in) || s.tokens_in < 0 || s.tokens_in > request.config.max_input_tokens
          || !Number.isSafeInteger(s.tokens_out) || s.tokens_out < 0 || s.tokens_out > request.config.max_output_tokens
          || result.tokens_in !== s.tokens_in || result.tokens_out !== s.tokens_out) throw bad();
        category = 'settlement_recorded';
      }
    }
    return {schema_version: 1, status: 'structural_classification_unverified', category,
      checkpoint_sequence: checkpoint.sequence, state_sha256: checkpoint.state_sha256,
      provider_evidence_authenticated: false, checkpoint_remote_verified: false, actor_authorized: false,
      ledger_written: false, can_resolve: false, can_clear_hold: false, can_settle: false, can_send: false};
  } catch { throw bad(); }
}
