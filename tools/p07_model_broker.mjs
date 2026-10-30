/** Host-only single-writer model broker. No credential loading or CLI side effects.
 * All GitHub/proxy/durable-ledger clients are explicitly injected by the operator.
 * Public benchmark JSON data must never be imported or executed as source.
 */
import {createHash} from 'node:crypto';

export const MAX_WIRE_BYTES = 1048576;
export const MODEL = 'anthropic/claude-sonnet-5@default';
const owners = new Set(['akseley', 'umirbayulgaisha']);
const callPattern = /^mq_[a-f0-9]{32}$/;
const hashPattern = /^[a-f0-9]{64}$/;
const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value)
  && [Object.prototype, null].includes(Object.getPrototypeOf(value));
const keysAre = (value, keys) => plain(value) && Object.keys(value).sort().join('|') === [...keys].sort().join('|');
const integer = (value, min, max) => Number.isSafeInteger(value) && value >= min && value <= max;
const text = (value, max) => typeof value === 'string' && Buffer.byteLength(value) <= max
  && Buffer.from(value, 'utf8').toString('utf8') === value;

export function canonical(value) {
  if (value === null || typeof value === 'boolean') return JSON.stringify(value);
  if (typeof value === 'string') {
    if (!text(value, MAX_WIRE_BYTES * 4)) throw new Error('invalid UTF-8 wire text');
    return JSON.stringify(value);
  }
  if (typeof value === 'number' && Number.isSafeInteger(value)) return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (plain(value)) {
    if (Object.keys(value).some(k => !/^[\x00-\x7f]*$/.test(k))) throw new Error('ASCII wire keys required');
    return '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + canonical(value[k])).join(',') + '}';
  }
  throw new Error('wire data requires valid UTF-8 and safe integers');
}

export const digest = value => createHash('sha256').update(canonical(value), 'utf8').digest('hex');

export function validateRequest(req) {
  if (!keysAre(req, ['schema_version', 'kind', 'call_id', 'identity', 'config', 'messages', 'messages_sha256', 'request_sha256'])
      || req.schema_version !== 1 || req.kind !== 'p07_model_request' || !callPattern.test(req.call_id)) {
    throw new Error('invalid queue request');
  }
  const id = req.identity;
  if (!keysAre(id, ['campaign_id', 'owner', 'arm', 'task_id', 'framework_sha', 'workflow_sha', 'profile_sha256'])
      || typeof id.owner !== 'string' || !owners.has(id.owner)
      || ['campaign_id', 'arm', 'task_id'].some(k => typeof id[k] !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(id[k]))
      || ['framework_sha', 'workflow_sha'].some(k => typeof id[k] !== 'string' || !/^[a-f0-9]{40}$/.test(id[k]))
      || typeof id.profile_sha256 !== 'string' || !hashPattern.test(id.profile_sha256)) throw new Error('unbound queue identity');
  const cfg = req.config;
  if (!keysAre(cfg, ['model', 'max_input_tokens', 'max_output_tokens', 'temperature', 'max_retries'])
      || cfg.model !== MODEL || cfg.max_retries !== 0 || !integer(cfg.max_input_tokens, 1, 10000000)
      || !integer(cfg.max_output_tokens, 16, 200000)
      || !(cfg.temperature === null || (typeof cfg.temperature === 'string'
           && /^(?:(?:0|1)(?:\.[0-9]+)?|2(?:\.0+)?)$/.test(cfg.temperature)))) throw new Error('unreviewed queue config');
  if (!Array.isArray(req.messages) || req.messages.length < 1 || req.messages.length > 256
      || req.messages.some(m => !keysAre(m, ['role', 'content']) || !['system', 'user', 'assistant'].includes(m.role)
          || !text(m.content, MAX_WIRE_BYTES))) throw new Error('bounded text-json messages required');
  if (Buffer.byteLength(canonical(req)) > MAX_WIRE_BYTES || req.messages_sha256 !== digest(req.messages)) throw new Error('queue bounds/hash mismatch');
  const {request_sha256, ...body} = req;
  if (request_sha256 !== digest(body)) throw new Error('queue request digest mismatch');
  return req;
}

export function responsePacket(req, state, result = null, error_code = 'none') {
  const response = {schema_version: 1, kind: 'p07_model_response', call_id: req.call_id,
    request_sha256: req.request_sha256, identity_sha256: digest(req.identity), state, result, error_code};
  return {...response, response_sha256: digest(response)};
}

function costText(value) {
  let s = typeof value === 'number' && Number.isFinite(value) && value >= 0 ? String(value) : value;
  if (typeof s !== 'string' || s.length > 64) throw new Error('reported cost unknown');
  const exp = /^([0-9]+)(?:\.([0-9]+))?e([+-]?[0-9]+)$/i.exec(s);
  if (exp) {
    if (Math.abs(Number(exp[3])) > 64) throw new Error('reported cost exponent exceeds bound');
    const digits = exp[1] + (exp[2] || '');
    const point = exp[1].length + Number(exp[3]);
    s = point <= 0 ? '0.' + '0'.repeat(-point) + digits
      : point >= digits.length ? digits + '0'.repeat(point - digits.length)
        : digits.slice(0, point) + '.' + digits.slice(point);
  }
  if (!/^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/.test(s) || s.length > 64 || !Number.isFinite(Number(s))) throw new Error('reported cost invalid');
  return s;
}

export function normalizeResult(value, req) {
  if (!plain(value) || !text(value.content, MAX_WIRE_BYTES)
      || !integer(value.tokens_in, 0, req.config.max_input_tokens)
      || !integer(value.tokens_out, 0, req.config.max_output_tokens)
      || !integer(value.latency_ms, 0, 86400000) || typeof value.truncated !== 'boolean'
      || !text(value.cost_source, 100) || value.resolved_backend !== 'kaggle_model_proxy'
      || value.resolved_model !== MODEL || value.attempt_id !== req.call_id) throw new Error('invalid physical result');
  const result = {content: value.content, tokens_in: value.tokens_in, tokens_out: value.tokens_out,
    cost_usd: costText(value.cost_usd), latency_ms: value.latency_ms, truncated: value.truncated,
    cost_source: value.cost_source, resolved_backend: value.resolved_backend,
    resolved_model: value.resolved_model, attempt_id: value.attempt_id};
  if (Buffer.byteLength(canonical(result)) > MAX_WIRE_BYTES - 2048) throw new Error('response exceeds bound');
  return result;
}

/**
 * journal.withLock(fn) MUST be an exclusive durable single-writer host lock.
 * journal.get/put operate on call IDs and preserve intent before physical send.
 * admit(req) MUST durably reserve against fixed campaign/owner/arm/call caps and
 * reject any owner/model/runtime/permissions mismatch or prior unknown charge.
 * settle(reservation,result,req) MUST durably account physical usage and return
 * {status:'settled'}; absent/unknown settlement is never priced at zero.
 * proxy.complete performs ONE no-retry physical request. A received unusable
 * response may throw {usage_result:canonicalChatResult}; usage still settles.
 * queue.put_response MUST create immutably or compare an existing exact packet.
 */
export async function processOne({request: req, queue, proxy, journal, admit, settle}) {
  validateRequest(req);
  if (!queue || typeof queue.put_response !== 'function' || !proxy || typeof proxy.complete !== 'function'
      || !journal || ['withLock', 'get', 'put'].some(k => typeof journal[k] !== 'function')
      || typeof admit !== 'function' || typeof settle !== 'function') throw new Error('trusted admission/settlement/journal clients required');
  return journal.withLock(async () => {
    let old = await journal.get(req.call_id);
    if (old) {
      if (old.request_sha256 !== req.request_sha256) throw new Error('immutable broker call conflict');
      if (['completed', 'refused'].includes(old.state)) {
        await queue.put_response(req.call_id, old.response);
        return {call_id: req.call_id, state: old.state, sent: false};
      }
      const response = old.response || responsePacket(req, 'unknown', null, 'prior_outcome_unknown');
      await journal.put(req.call_id, {...old, state: 'unknown', response});
      await queue.put_response(req.call_id, response);
      return {call_id: req.call_id, state: 'unknown', sent: false};
    }
    // A crash during admission may have reserved funds. Keep that call unresolved.
    await journal.put(req.call_id, {request_sha256: req.request_sha256, identity: req.identity, state: 'admitting'});
    let reservation;
    try {
      reservation = await admit(req);
      if (reservation?.status !== 'refused' && (reservation?.status !== 'admitted'
          || !text(reservation.reservation_id, 200) || !reservation.reservation_id)) {
        throw new Error('durable reservation required before claim');
      }
    }
    catch {
      const response = responsePacket(req, 'unknown', null, 'admission_outcome_unknown');
      await journal.put(req.call_id, {request_sha256: req.request_sha256, state: 'unknown', response});
      await queue.put_response(req.call_id, response);
      return {call_id: req.call_id, state: 'unknown', sent: false};
    }
    if (reservation?.status === 'refused') {
      const response = responsePacket(req, 'refused', null, 'admission_refused');
      await journal.put(req.call_id, {request_sha256: req.request_sha256, state: 'refused', response});
      await queue.put_response(req.call_id, response);
      return {call_id: req.call_id, state: 'refused', sent: false};
    }
    const record = {request_sha256: req.request_sha256, identity: req.identity,
      state: 'claimed', reservation_id: reservation.reservation_id};
    await journal.put(req.call_id, record);
    let result, rawResult, responseError = 'none';
    try {
      try { rawResult = await proxy.complete(req, reservation); }
      catch (error) {
        if (!error?.usage_result) throw error;
        rawResult = error.usage_result;
        responseError = 'response_shape';
      }
      result = normalizeResult(rawResult, req);
      await journal.put(req.call_id, {...record, state: 'received', result});
      const settled = await settle(reservation, result, req);
      if (settled?.status !== 'settled') throw new Error('physical usage settlement unresolved');
    } catch {
      const response = responsePacket(req, 'unknown', null, 'physical_or_accounting_outcome_unknown');
      await journal.put(req.call_id, {...record, state: 'unknown', ...(result ? {result} : {}), response});
      await queue.put_response(req.call_id, response);
      return {call_id: req.call_id, state: 'unknown', sent: true};
    }
    const response = responsePacket(req, 'completed', result, responseError);
    if (Buffer.byteLength(canonical(response)) > MAX_WIRE_BYTES) throw new Error('completed response exceeds bound');
    await journal.put(req.call_id, {...record, state: 'completed', result, response});
    await queue.put_response(req.call_id, response);
    return {call_id: req.call_id, state: 'completed', sent: true};
  });
}
