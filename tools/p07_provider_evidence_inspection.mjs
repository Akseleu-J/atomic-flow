/** Inspect an untrusted provider-evidence declaration without I/O.
 *
 * No provider receipt format, signature scheme, request lookup, billing API or
 * idempotency guarantee has been qualified for the current P07 endpoint. A hash
 * merely binds supplied bytes; an operator-supplied source or provider reference
 * is a claim, not verification. This code never authenticates, correlates or
 * settles an attempt and grants no authority to clear a hold or send a request.
 */
import {createHash} from 'node:crypto';
import {digest, validateRequest} from './p07_model_broker.mjs';

const HASH = /^[a-f0-9]{64}$/;
const TAG = /^[A-Za-z0-9_.@-]{1,128}$/;
const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value)
  && [Object.prototype, null].includes(Object.getPrototypeOf(value));
const exact = (value, keys) => plain(value) && Object.keys(value).sort().join('|') === [...keys].sort().join('|');
const sha = value => typeof value === 'string' && HASH.test(value);
const refused = () => new Error('provider_evidence_declaration_refused');

/** Structurally bind bounded evidence bytes to a caller-supplied request.
 * The returned digest is not an authorization or evidence of a physical call.
 * `source_label` and `provider_reference_sha256` are untrusted declarations;
 * even an externally issued-looking reference cannot be verified by this API.
 */
export function inspectProviderEvidence({request, declaration, evidenceBytes}) {
  try {
    validateRequest(request);
    if (!exact(declaration, ['schema_version', 'kind', 'call_id', 'request_sha256', 'identity_sha256',
      'owner', 'model', 'account_binding_sha256', 'source_label', 'provider_reference_sha256', 'evidence_sha256'])
        || declaration.schema_version !== 1 || declaration.kind !== 'p07_unverified_provider_evidence'
        || declaration.call_id !== request.call_id || declaration.request_sha256 !== request.request_sha256
        || declaration.identity_sha256 !== digest(request.identity) || declaration.owner !== request.identity.owner
        || declaration.model !== request.config.model || !sha(declaration.account_binding_sha256)
        || typeof declaration.source_label !== 'string' || !TAG.test(declaration.source_label)
        || !(declaration.provider_reference_sha256 === null || sha(declaration.provider_reference_sha256))
        || !sha(declaration.evidence_sha256) || !Buffer.isBuffer(evidenceBytes)
        || evidenceBytes.length < 1 || evidenceBytes.length > 1048576
        || createHash('sha256').update(evidenceBytes).digest('hex') !== declaration.evidence_sha256) throw refused();
    return {schema_version: 1, status: 'unverified_declaration_only', declaration_sha256: digest(declaration),
      evidence_sha256: declaration.evidence_sha256, source_authenticated: false, account_verified: false,
      provider_reference_verified: false, request_correlated: false, charge_verified: false, absence_verified: false,
      checkpoint_remote_verified: false, actor_authorized: false, can_resolve: false, can_settle: false,
      can_clear_hold: false, can_send: false, can_retry: false};
  } catch {
    throw refused();
  }
}
