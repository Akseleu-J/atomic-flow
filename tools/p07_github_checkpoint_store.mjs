/** Immutable-profile GitHub checkpoint CAS transport. All repo-bound I/O is injected.
 * A GitHub acknowledgement is an integrity receipt, not independent attestation.
 * Network/HTTP uncertainty is never retried here.
 */
import {createHash} from 'node:crypto';

const MAX_BYTES = 16384;
const BRANCH = /^codex\/p07-model-queue-paid-[0-9]{8}-[0-9]{2}$/;
const HASH = /^[a-f0-9]{64}$/;
const GIT_HASH = /^[a-f0-9]{40}$/;
const KEYS = ['profile_fingerprint', 'schema_version', 'sequence', 'state_sha256'];
const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value)
  && [Object.prototype, null].includes(Object.getPrototypeOf(value));
function error() { return new Error('paid_checkpoint_operation_unresolved'); }

function checkpoint(value) {
  if (!plain(value) || Object.keys(value).sort().join('|') !== KEYS.join('|')
      || value.schema_version !== 1 || typeof value.schema_version !== 'number'
      || !Number.isSafeInteger(value.sequence) || value.sequence < 1
      || typeof value.state_sha256 !== 'string' || value.state_sha256.length !== 64 || !HASH.test(value.state_sha256)
      || typeof value.profile_fingerprint !== 'string' || value.profile_fingerprint.length !== 64 || !HASH.test(value.profile_fingerprint)) throw error();
  return {schema_version: 1, sequence: value.sequence, state_sha256: value.state_sha256,
    profile_fingerprint: value.profile_fingerprint};
}
function canonical(value) {
  const checked = checkpoint(value);
  return JSON.stringify(Object.fromEntries(Object.keys(checked).sort().map(key => [key, checked[key]])));
}
function blob(raw) {
  return createHash('sha1').update('blob ' + raw.length + '\0').update(raw).digest('hex');
}
function decode(value) {
  if (!plain(value) || value.type !== 'file' || value.encoding !== 'base64'
      || hasLink(value) || !Number.isSafeInteger(value.size) || value.size < 1 || value.size > MAX_BYTES
      || typeof value.content !== 'string' || value.content.length > 2 * MAX_BYTES
      || typeof value.sha !== 'string' || value.sha.length !== 40 || !GIT_HASH.test(value.sha)) throw error();
  const encoded = value.content.replace(/[\t\r\n ]/g, '');
  if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(encoded)) throw error();
  const raw = Buffer.from(encoded, 'base64');
  if (raw.length !== value.size || raw.length > MAX_BYTES || blob(raw) !== value.sha
      || raw.toString('base64') !== encoded) throw error();
  const text = new TextDecoder('utf-8', {fatal: true}).decode(raw);
  // Checkpoints have an exact four-field shape. Requiring canonical bytes also
  // rejects duplicate keys, whitespace, escaped-key aliases and alternate numbers.
  const parsed = checkpoint(JSON.parse(text));
  if (canonical(parsed) !== text) throw error();
  return {value: parsed, sha: value.sha};
}
function hasLink(value) {
  return Object.hasOwn(value, 'target') || Object.hasOwn(value, 'submodule_git_url')
    || value.type === 'symlink' || value.type === 'submodule';
}

export function createGitHubCheckpointStore({github, branch, campaignId}) {
  if (typeof github !== 'function' || typeof branch !== 'string' || /[\r\n]/.test(branch) || !BRANCH.test(branch)
      || typeof campaignId !== 'string' || /[\r\n]/.test(campaignId) || !/^[A-Za-z0-9_-]{1,128}$/.test(campaignId)) {
    throw new Error('paid_checkpoint_configuration_refused');
  }
  const relative = 'p07-paid-checkpoints/' + campaignId + '.json';
  const readPath = 'contents/' + relative + '?ref=' + encodeURIComponent(branch);
  async function readCurrent() {
    let result;
    try { result = await github(readPath); } catch { throw error(); }
    if (result?.status === 404) return null;
    if (result?.status !== 200) throw error();
    try { return decode(result.data); } catch { throw error(); }
  }
  return {
    async read() {
      const current = await readCurrent();
      return current === null ? null : {...current.value};
    },
    async compareAndSwap(expected, next) {
      let old, following;
      try {
        old = expected === null ? null : checkpoint(expected);
        following = checkpoint(next);
        if (old === null ? following.sequence !== 1 : (
          old.sequence >= Number.MAX_SAFE_INTEGER || following.sequence !== old.sequence + 1
          || following.profile_fingerprint !== old.profile_fingerprint)) throw error();
      } catch { throw error(); }
      const current = await readCurrent();
      if (current === null ? old !== null : old === null || canonical(current.value) !== canonical(old)) throw error();
      const raw = Buffer.from(canonical(following), 'utf8');
      if (raw.length > MAX_BYTES) throw error();
      const body = {message: '[skip ci] paid checkpoint ' + campaignId, branch,
        content: raw.toString('base64')};
      if (current !== null) body.sha = current.sha;
      let acknowledgement;
      try { acknowledgement = await github('contents/' + relative, 'PUT', body); } catch { throw error(); }
      const expectedStatus = current === null ? 201 : 200;
      const data = acknowledgement?.data;
      if (acknowledgement?.status !== expectedStatus || !plain(data) || !plain(data.content)
          || data.content.sha !== blob(raw) || !plain(data.commit)
          || typeof data.commit.sha !== 'string' || data.commit.sha.length !== 40 || !GIT_HASH.test(data.commit.sha)) throw error();
      // Return the exact canonical next checkpoint only after a matching blob and
      // full commit acknowledgement. A failed acknowledgement grants no retry.
      return {...following};
    },
  };
}
