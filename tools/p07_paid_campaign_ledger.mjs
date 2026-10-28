/** Durable fixed-profile host accounting for processOne. No provider/network/env/credential I/O.
 *
 * createPaidCampaignLedger({directory, profile, priorSpendUsd, checkpointStore, now?}) returns
 * {journal, admit, settle, snapshot}. Pass its journal/admit/settle to processOne.
 * journal.withLock owns one exclusive campaign fs lock through the whole broker
 * callback, including physical send. Locks are never stolen or recovered by PID.
 *
 * Profile fields:
 * campaign_id, model, framework_sha, workflow_sha, profile_sha256, wire_config,
 * total_budget_usd, owner_caps, arm_budget_usd, task_budget_usd,
 * reserve_per_call_usd, max_physical_calls, deadline_unix_ms, allowed_arms, task_owners.
 * Decimal USD strings have <=9 fractional digits; total ceiling is at most $20.
 * owner_caps maps only explicitly authorized fixed owners to their inclusive cap.
 * Per-arm cap groups owner/task/arm; per-task cap groups owner/task across arms.
 * allowed_arms is the fixed arm-name list; task_owners maps every admitted task
 * to its one fixed authorized owner. Unlisted task/arm pairs are refused.
 * priorSpendUsd={total:decimal, owners:{owner:decimal}} binds historic spend;
 * every authorized owner needs an explicit prior entry, including zero.
 * profile_sha256 is the host-approved experiment identity; profile_fingerprint
 * additionally binds every adapter configuration byte against later reopening.
 *
 * An unresolved call, expired deadline, cost over reservation, or budget overrun
 * blocks all new paid admissions, across both named owners. A completed packet
 * may still be republished by processOne without a second inference.
 * checkpointStore is a trusted durable replica of hashes only. Its read() must
 * return the current checkpoint or null; compareAndSwap(expected,next) must
 * atomically preserve next durably and acknowledge exactly next. Every local
 * transition requires that acknowledgement before physical execution proceeds.
 * This is consistency with a trusted replica, not remote attestation.
 */
import * as fs from 'node:fs/promises';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {AsyncLocalStorage} from 'node:async_hooks';
import {MODEL, canonical, digest, validateRequest} from './p07_model_broker.mjs';

const SCALE = 1000000000n;
const OWNER = new Set(['akseley', 'umirbayulgaisha']);
const CALL = /^mq_[a-f0-9]{32}$/;
const SHA = /^[a-f0-9]{64}$/;
const COMMIT = /^[a-f0-9]{40}$/;
const MAX_BYTES = 32 * 1024 * 1024;
const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value)
  && Object.getPrototypeOf(value) === Object.prototype;
const exact = (value, keys) => plain(value) && canonical(Object.keys(value).sort()) === canonical([...keys].sort());
const clone = value => JSON.parse(canonical(value));

export function nanoUsd(value) {
  if (typeof value !== 'string' || value.length > 64 || !/^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,9})?$/.test(value)) {
    throw Error('USD requires a nonnegative exact decimal string with at most nine fractional digits');
  }
  const [whole, fraction = ''] = value.split('.');
  return BigInt(whole) * SCALE + BigInt(fraction.padEnd(9, '0'));
}
export function decimalUsd(value) {
  if (typeof value !== 'bigint' || value < 0n) throw Error('nonnegative integer nanoUSD required');
  const whole = value / SCALE, fraction = (value % SCALE).toString().padStart(9, '0').replace(/0+$/, '');
  return whole.toString() + (fraction ? '.' + fraction : '');
}
function checkProfile(profile, prior) {
  const keys = ['campaign_id', 'model', 'framework_sha', 'workflow_sha', 'profile_sha256', 'wire_config',
    'total_budget_usd', 'owner_caps', 'arm_budget_usd', 'task_budget_usd', 'reserve_per_call_usd',
    'max_physical_calls', 'deadline_unix_ms', 'allowed_arms', 'task_owners'];
  if (!exact(profile, keys) || typeof profile.campaign_id !== 'string'
      || !/^[A-Za-z0-9_-]{1,128}$/.test(profile.campaign_id) || profile.model !== MODEL
      || !COMMIT.test(profile.framework_sha || '') || !COMMIT.test(profile.workflow_sha || '')
      || !SHA.test(profile.profile_sha256 || '') || !plain(profile.owner_caps)
      || !Object.keys(profile.owner_caps).length || Object.keys(profile.owner_caps).some(owner => !OWNER.has(owner))
      || !Array.isArray(profile.allowed_arms) || !profile.allowed_arms.length || profile.allowed_arms.length > 8
      || new Set(profile.allowed_arms).size !== profile.allowed_arms.length
      || profile.allowed_arms.some(arm => typeof arm !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(arm))
      || !plain(profile.task_owners) || !Object.keys(profile.task_owners).length || Object.keys(profile.task_owners).length > 256
      || Object.entries(profile.task_owners).some(([task, owner]) => !/^[A-Za-z0-9_-]{1,128}$/.test(task)
        || typeof owner !== 'string' || !Object.hasOwn(profile.owner_caps, owner))
      || !Number.isSafeInteger(profile.max_physical_calls) || profile.max_physical_calls < 1 || profile.max_physical_calls > 10000
      || !Number.isSafeInteger(profile.deadline_unix_ms) || profile.deadline_unix_ms <= 0) {
    throw Error('exact fixed paid campaign profile required');
  }
  const total = nanoUsd(profile.total_budget_usd);
  const arm = nanoUsd(profile.arm_budget_usd), task = nanoUsd(profile.task_budget_usd);
  const reserve = nanoUsd(profile.reserve_per_call_usd);
  if (total <= 0n || total > 20n * SCALE || reserve <= 0n || arm < reserve || task < arm || total < task
      || Object.values(profile.owner_caps).some(cap => nanoUsd(cap) < task || nanoUsd(cap) > total)) {
    throw Error('paid budget ceilings or conservative fixed reservation invalid');
  }
  if (Object.values(profile.owner_caps).reduce((sum, cap) => sum + nanoUsd(cap), 0n) > total) {
    throw Error('owner allocations exceed combined fixed ceiling');
  }
  if (!exact(prior, ['total', 'owners']) || !plain(prior.owners)
      || canonical(Object.keys(prior.owners).sort()) !== canonical(Object.keys(profile.owner_caps).sort())) {
    throw Error('exact historic aggregate and owner spend binding required');
  }
  const historic = nanoUsd(prior.total);
  if (historic > total || Object.values(prior.owners).reduce((sum, amount) => sum + nanoUsd(amount), 0n) > historic
      || Object.entries(prior.owners).some(([owner, amount]) => nanoUsd(amount) > nanoUsd(profile.owner_caps[owner]))) {
    throw Error('historic spend exceeds declared authority');
  }
  // Reuse the exact wire schema validator without creating any request or call.
  const probe = {schema_version: 1, kind: 'p07_model_request', call_id: 'mq_' + '0'.repeat(32),
    identity: {campaign_id: profile.campaign_id, owner: Object.keys(profile.owner_caps)[0], arm: 'profile_validation',
      task_id: 'profile_validation', framework_sha: profile.framework_sha, workflow_sha: profile.workflow_sha,
      profile_sha256: profile.profile_sha256}, config: profile.wire_config,
    messages: [{role: 'user', content: 'host profile validation'}]};
  probe.messages_sha256 = digest(probe.messages); probe.request_sha256 = digest(probe);
  validateRequest(probe);
}

export async function createPaidCampaignLedger({directory, profile, priorSpendUsd, checkpointStore, now = () => Date.now()}) {
  checkProfile(profile, priorSpendUsd);
  if (typeof directory !== 'string' || !directory || typeof now !== 'function') throw Error('private ledger directory and clock required');
  if (!checkpointStore || typeof checkpointStore.read !== 'function' || typeof checkpointStore.compareAndSwap !== 'function') {
    throw Error('trusted durable checkpoint read/CAS store required');
  }
  const frozen = clone(profile), historic = clone(priorSpendUsd);
  const fingerprint = digest({profile: frozen, prior_spend_usd: historic});
  const root = path.resolve(directory), target = path.join(root, 'campaign.json'), lock = path.join(root, 'campaign.lock');
  const context = new AsyncLocalStorage();
  const token = randomUUID();
  let state;
  let checkpoint = null, checkpointUncertain = false;
  function checkCheckpoint(value) {
    if (value !== null && (!exact(value, ['schema_version', 'sequence', 'state_sha256', 'profile_fingerprint'])
        || value.schema_version !== 1 || !Number.isSafeInteger(value.sequence) || value.sequence < 1
        || typeof value.state_sha256 !== 'string' || !SHA.test(value.state_sha256)
        || value.profile_fingerprint !== fingerprint)) throw Error('invalid authoritative paid checkpoint');
    return value === null ? null : clone(value);
  }
  async function safeRoot() {
    let current = root;
    while (true) {
      try {
        const stat = await fs.lstat(current);
        if (!stat.isDirectory() || stat.isSymbolicLink()) throw Error('paid ledger root contains a link or non-directory');
      } catch (e) { if (e.code !== 'ENOENT') throw e; }
      const parent = path.dirname(current); if (parent === current) break; current = parent;
    }
  }
  function ownLock() {
    if (context.getStore() !== token) throw Error('campaign exclusive lock required for accounting');
    if (checkpointUncertain) throw Error('paid checkpoint outcome unknown; explicit reconciliation required');
  }
  function clock() {
    const value = now();
    if (!Number.isSafeInteger(value) || value <= 0) throw Error('invalid host deadline clock');
    return value;
  }
  async function read() {
    try {
      const stat = await fs.lstat(target);
      if (!stat.isFile() || stat.isSymbolicLink() || stat.size > MAX_BYTES) throw Error('unsafe paid ledger file');
      const raw = await fs.readFile(target);
      if (raw.length > MAX_BYTES) throw Error('paid ledger exceeded bound');
      const saved = JSON.parse(raw.toString('utf8'));
      if (!plain(saved) || saved.schema_version !== 1 || saved.profile_fingerprint !== fingerprint
          || !Number.isSafeInteger(saved.checkpoint_sequence) || saved.checkpoint_sequence < 1
          || canonical(saved.profile) !== canonical(frozen) || canonical(saved.prior_spend_usd) !== canonical(historic)
          || !plain(saved.calls) || Object.keys(saved.calls).length > frozen.max_physical_calls + 100
          || !Array.isArray(saved.holds) || saved.holds.some(hold => typeof hold !== 'string')) {
        throw Error('paid ledger profile or durable record identity changed');
      }
      for (const [id, record] of Object.entries(saved.calls)) {
        if (!CALL.test(id) || !plain(record) || record.call_id !== id || !SHA.test(record.request_sha256 || '')
            || !plain(record.identity) || !plain(record.broker)) throw Error('invalid original call ledger identity');
        if (record.reservation) {
          if (!exact(record.reservation, ['reservation_id', 'reserved_usd', 'created_at_ms', 'owner', 'arm', 'task_id'])
              || record.reservation.reservation_id !== 'paid_' + id
              || record.reservation.owner !== record.identity.owner || record.reservation.arm !== record.identity.arm
              || record.reservation.task_id !== record.identity.task_id
              || nanoUsd(record.reservation.reserved_usd) !== nanoUsd(frozen.reserve_per_call_usd)) {
            throw Error('original reservation binding invalid');
          }
        }
        if (['claimed', 'received', 'completed'].includes(record.broker.state) && !record.reservation) {
          throw Error('physical broker state has no durable reservation');
        }
        if (record.broker.state === 'completed' && !record.settlement) throw Error('completed call has no physical settlement');
        if (record.settlement) {
          if (!record.reservation || !plain(record.settlement)) throw Error('settlement has no original reservation');
          nanoUsd(record.settlement.cost_usd);
        }
      }
      return saved;
    } catch (e) { if (e.code === 'ENOENT') return null; throw e; }
  }
  async function persist() {
    ownLock(); await safeRoot();
    const sequence = checkpoint === null ? 1 : checkpoint.sequence + 1;
    if (!Number.isSafeInteger(sequence)) throw Error('paid checkpoint sequence exhausted');
    state.checkpoint_sequence = sequence;
    const raw = canonical(state);
    if (Buffer.byteLength(raw) > MAX_BYTES) throw Error('paid ledger capacity exceeded');
    const temporary = target + '.' + randomUUID() + '.tmp';
    const handle = await fs.open(temporary, 'wx', 0o600);
    try { await handle.writeFile(raw, 'utf8'); await handle.sync(); }
    finally { await handle.close(); }
    await fs.rename(temporary, target);
    const next = {schema_version: 1, sequence, state_sha256: digest(state), profile_fingerprint: fingerprint};
    try {
      const acknowledged = checkCheckpoint(await checkpointStore.compareAndSwap(
        checkpoint === null ? null : clone(checkpoint), clone(next)));
      if (acknowledged === null || canonical(acknowledged) !== canonical(next)) throw Error('paid checkpoint acknowledgement differs');
      checkpoint = clone(next);
    } catch {
      checkpointUncertain = true;
      throw Error('paid checkpoint outcome unknown; no automatic retry');
    }
  }
  async function withLock(callback) {
    if (checkpointUncertain) throw Error('paid checkpoint outcome unknown; explicit reconciliation required');
    if (context.getStore() === token) throw Error('nested paid campaign lock is not permitted');
    await safeRoot();
    let handle;
    try { handle = await fs.open(lock, 'wx', 0o600); }
    catch { throw Error('campaign lock held or stale; explicit reconciliation required'); }
    try {
      await handle.writeFile(token + '\n'); await handle.sync();
      return await context.run(token, async () => {
        try { checkpoint = checkCheckpoint(await checkpointStore.read()); }
        catch {
          checkpointUncertain = true;
          throw Error('paid checkpoint read unknown; no automatic retry');
        }
        state = await read();
        if (state === null) {
          if (checkpoint !== null) throw Error('local paid ledger missing despite authoritative checkpoint');
          state = {schema_version: 1, profile_fingerprint: fingerprint, profile: frozen,
            prior_spend_usd: historic, calls: {}, holds: []};
          await persist();
        } else if (checkpoint === null || state.checkpoint_sequence !== checkpoint.sequence
            || digest(state) !== checkpoint.state_sha256) {
          throw Error('local paid ledger and authoritative checkpoint differ; reconciliation required');
        }
        return callback();
      });
    } finally { await handle.close(); await fs.unlink(lock); }
  }
  await safeRoot(); await fs.mkdir(root, {recursive: true}); await safeRoot();
  await withLock(async () => {});
  const journal = {
    withLock,
    async get(id) {
      ownLock(); if (!CALL.test(id)) throw Error('invalid original call ID');
      return state.calls[id] ? clone(state.calls[id].broker) : null;
    },
    async put(id, broker) {
      ownLock();
      if (typeof id !== 'string' || !CALL.test(id) || !plain(broker) || !SHA.test(broker.request_sha256 || '')) {
        throw Error('invalid bounded broker journal record');
      }
      const old = state.calls[id];
      if (old) {
        if (old.request_sha256 !== broker.request_sha256
            || (broker.identity && canonical(old.identity) !== canonical(broker.identity))) throw Error('original paid request conflict');
        if (['completed', 'refused'].includes(old.broker.state) && canonical(old.broker) !== canonical(broker)) {
          throw Error('terminal original broker record is immutable');
        }
        old.broker = clone(broker);
      } else {
        if (!plain(broker.identity) || Object.keys(state.calls).length >= frozen.max_physical_calls + 100) {
          throw Error('original identity or ledger capacity unavailable');
        }
        state.calls[id] = {call_id: id, request_sha256: broker.request_sha256, identity: clone(broker.identity),
          broker: clone(broker), reservation: null, settlement: null};
      }
      await persist();
    },
  };
  function bound(req) {
    validateRequest(req);
    const id = req.identity;
    return id.campaign_id === frozen.campaign_id && id.framework_sha === frozen.framework_sha
      && id.workflow_sha === frozen.workflow_sha && id.profile_sha256 === frozen.profile_sha256
      && Object.hasOwn(frozen.owner_caps, id.owner) && frozen.task_owners[id.task_id] === id.owner
      && frozen.allowed_arms.includes(id.arm) && canonical(req.config) === canonical(frozen.wire_config);
  }
  function accounting(predicate = () => true) {
    let actual = 0n, reserved = 0n, count = 0;
    for (const record of Object.values(state.calls)) {
      if (!record.reservation || !predicate(record)) continue;
      count++;
      if (record.settlement) actual += nanoUsd(record.settlement.cost_usd);
      else reserved += nanoUsd(record.reservation.reserved_usd);
    }
    return {actual, reserved, count};
  }
  async function admit(req) {
    ownLock();
    if (!bound(req)) return {status: 'refused'};
    const original = state.calls[req.call_id];
    if (!original || original.request_sha256 !== req.request_sha256 || canonical(original.identity) !== canonical(req.identity)
        || original.broker.state !== 'admitting' || original.reservation) throw Error('original admission intent missing or already reserved');
    if (state.holds.length || Object.values(state.calls).some(record => record.call_id !== req.call_id
        && !['completed', 'refused'].includes(record.broker.state))) throw Error('campaign has unresolved original outcome');
    const all = accounting(), owner = accounting(record => record.identity.owner === req.identity.owner);
    const task = accounting(record => record.identity.owner === req.identity.owner && record.identity.task_id === req.identity.task_id);
    const arm = accounting(record => record.identity.owner === req.identity.owner && record.identity.task_id === req.identity.task_id
      && record.identity.arm === req.identity.arm);
    const reserve = nanoUsd(frozen.reserve_per_call_usd);
    if (clock() >= frozen.deadline_unix_ms || all.count >= frozen.max_physical_calls
        || nanoUsd(historic.total) + all.actual + all.reserved + reserve > nanoUsd(frozen.total_budget_usd)
        || nanoUsd(historic.owners[req.identity.owner]) + owner.actual + owner.reserved + reserve > nanoUsd(frozen.owner_caps[req.identity.owner])
        || task.actual + task.reserved + reserve > nanoUsd(frozen.task_budget_usd)
        || arm.actual + arm.reserved + reserve > nanoUsd(frozen.arm_budget_usd)) return {status: 'refused'};
    original.reservation = {reservation_id: 'paid_' + req.call_id, reserved_usd: frozen.reserve_per_call_usd,
      created_at_ms: clock(), owner: req.identity.owner, arm: req.identity.arm, task_id: req.identity.task_id};
    await persist();
    return {status: 'admitted', reservation_id: original.reservation.reservation_id};
  }
  async function settle(reservation, result, req) {
    ownLock();
    if (!bound(req)) throw Error('settlement identity differs from admitted profile');
    const record = state.calls[req.call_id];
    if (!record?.reservation || reservation?.reservation_id !== record.reservation.reservation_id
        || record.request_sha256 !== req.request_sha256 || canonical(record.identity) !== canonical(req.identity)
        || !['received', 'completed', 'unknown'].includes(record.broker.state)
        || !plain(record.broker.result) || canonical(record.broker.result) !== canonical(result)
        || !plain(result) || result.attempt_id !== req.call_id || result.resolved_model !== MODEL
        || result.resolved_backend !== 'kaggle_model_proxy' || result.cost_source !== 'provider'
        || !Number.isSafeInteger(result.tokens_in) || result.tokens_in < 0 || result.tokens_in > frozen.wire_config.max_input_tokens
        || !Number.isSafeInteger(result.tokens_out) || result.tokens_out < 0 || result.tokens_out > frozen.wire_config.max_output_tokens) throw Error('physical settlement provenance invalid');
    const cost = nanoUsd(result.cost_usd);
    const settlement = {cost_usd: decimalUsd(cost), tokens_in: result.tokens_in, tokens_out: result.tokens_out,
      cost_source: result.cost_source, result_sha256: digest(result)};
    if (record.settlement && canonical(record.settlement) !== canonical(settlement)) throw Error('original settlement conflict');
    record.settlement = settlement;
    if (cost > nanoUsd(record.reservation.reserved_usd) && !state.holds.includes('actual_cost_exceeded_reservation')) {
      state.holds.push('actual_cost_exceeded_reservation');
    }
    const all = accounting(), owner = accounting(row => row.identity.owner === req.identity.owner);
    const task = accounting(row => row.identity.owner === req.identity.owner && row.identity.task_id === req.identity.task_id);
    const arm = accounting(row => row.identity.owner === req.identity.owner && row.identity.task_id === req.identity.task_id
      && row.identity.arm === req.identity.arm);
    if (nanoUsd(historic.total) + all.actual + all.reserved > nanoUsd(frozen.total_budget_usd)
        || nanoUsd(historic.owners[req.identity.owner] || '0') + owner.actual + owner.reserved > nanoUsd(frozen.owner_caps[req.identity.owner])
        || task.actual + task.reserved > nanoUsd(frozen.task_budget_usd)
        || arm.actual + arm.reserved > nanoUsd(frozen.arm_budget_usd)) {
      if (!state.holds.includes('actual_budget_ceiling_exceeded')) state.holds.push('actual_budget_ceiling_exceeded');
    }
    await persist();
    return {status: 'settled'};
  }
  async function snapshot() {
    return withLock(async () => {
      const all = accounting();
      return {profile_fingerprint: fingerprint, profile: clone(frozen), prior_spend_usd: clone(historic),
        known_actual_usd: decimalUsd(all.actual), unresolved_reserved_usd: decimalUsd(all.reserved),
        known_total_including_prior_usd: decimalUsd(nanoUsd(historic.total) + all.actual),
        reserved_call_count: all.count, holds: [...state.holds], calls: clone(state.calls)};
    });
  }
  return {journal, admit, settle, snapshot};
}
