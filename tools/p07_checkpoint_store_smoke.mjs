/** Scoped real GitHub checkpoint/ledger integration smoke; never calls a model.
 * GitHub is an explicitly injected repo-bound client. Provider clients and
 * credential discovery are deliberately absent. The sole callback throws before
 * inference, so its synthetic unknown reservation is evidence of a hold, not a
 * measured provider charge or paid-route qualification.
 */
import * as fs from 'node:fs/promises';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {MODEL, canonical, digest, processOne} from './p07_model_broker.mjs';
import {createPaidCampaignLedger} from './p07_paid_campaign_ledger.mjs';
import {createGitHubCheckpointStore} from './p07_github_checkpoint_store.mjs';

export const CAMPAIGN_ID = 'p07_checkpoint_store_synthetic_smoke';
export const WIRE_CONFIG = Object.freeze({model: MODEL, max_input_tokens: 1000,
  max_output_tokens: 128, temperature: null, max_retries: 0});
const PRIOR = Object.freeze({total: '0', owners: Object.freeze({akseley: '0', umirbayulgaisha: '0'})});

async function freshDirectory(directory) {
  if (typeof directory !== 'string' || !directory) throw Error('checkpoint_smoke_configuration_refused');
  const absolute = path.resolve(directory);
  try { await fs.lstat(absolute); throw Error('checkpoint_smoke_fresh_directory_required'); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  let current = path.dirname(absolute);
  while (true) {
    const stat = await fs.lstat(current);
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw Error('checkpoint_smoke_directory_refused');
    const parent = path.dirname(current); if (parent === current) break; current = parent;
  }
  return absolute;
}
function request(profile, owner, taskId, arm) {
  const req = {schema_version: 1, kind: 'p07_model_request', call_id: 'mq_' + randomUUID().replaceAll('-', ''),
    identity: {campaign_id: CAMPAIGN_ID, owner, arm, task_id: taskId,
      framework_sha: profile.framework_sha, workflow_sha: profile.workflow_sha, profile_sha256: profile.profile_sha256},
    config: {...WIRE_CONFIG}, messages: [{role: 'user', content: 'Synthetic checkpoint hold test; no inference permitted.'}]};
  req.messages_sha256 = digest(req.messages); req.request_sha256 = digest(req);
  return req;
}

export async function runCheckpointSmoke({github, branch, directory, frameworkSha, now = () => Date.now()}) {
  if (typeof frameworkSha !== 'string' || frameworkSha.length !== 40 || !/^[a-f0-9]{40}$/.test(frameworkSha)
      || typeof now !== 'function') throw Error('checkpoint_smoke_configuration_refused');
  const current = now();
  if (!Number.isSafeInteger(current) || current <= 0 || current > Number.MAX_SAFE_INTEGER - 600000) {
    throw Error('checkpoint_smoke_configuration_refused');
  }
  const store = createGitHubCheckpointStore({github, branch, campaignId: CAMPAIGN_ID});
  const absolute = await freshDirectory(directory);
  if (await store.read() !== null) throw Error('checkpoint_smoke_fresh_remote_campaign_required');
  await fs.mkdir(absolute, {recursive: false, mode: 0o700});
  const profileBody = {campaign_id: CAMPAIGN_ID, model: MODEL, framework_sha: frameworkSha,
    workflow_sha: frameworkSha, wire_config: {...WIRE_CONFIG}, total_budget_usd: '1',
    owner_caps: {akseley: '0.5', umirbayulgaisha: '0.5'}, arm_budget_usd: '0.1',
    task_budget_usd: '0.2', reserve_per_call_usd: '0.01', max_physical_calls: 2,
    deadline_unix_ms: current + 600000, allowed_arms: ['synthetic_atomic', 'synthetic_baseline'],
    task_owners: {synthetic_owner2: 'akseley', synthetic_participant3: 'umirbayulgaisha'}};
  const profile = {...profileBody, profile_sha256: digest(profileBody)};
  const report = {schema_version: 1, status: 'not_qualified', campaign_id: CAMPAIGN_ID,
    tested_framework_sha: frameworkSha, tested_workflow_sha: frameworkSha, branch,
    profile_sha256: profile.profile_sha256, provider_calls: 0, model_generation: false,
    scripted_callback_entries: 0, zero_charged: true, actual_provider_charge_usd: '0',
    route_qualification: 'not_run', model_response_generated: false,
    scope: 'Real GitHub checkpoint CAS and durable ledger hold integration with a synthetic pre-inference callback failure',
    reservation_scope: 'Synthetic framework reservation only; no provider was contacted and no charge was incurred'};
  try {
    const adapter = await createPaidCampaignLedger({directory: absolute, profile, priorSpendUsd: PRIOR, checkpointStore: store, now});
    const first = request(profile, 'akseley', 'synthetic_owner2', 'synthetic_atomic');
    const second = request(profile, 'umirbayulgaisha', 'synthetic_participant3', 'synthetic_baseline');
    const packets = new Map();
    const queue = {async put_response(id, packet) {
      if (packets.has(id) && canonical(packets.get(id)) !== canonical(packet)) throw Error('checkpoint_smoke_packet_conflict');
      packets.set(id, structuredClone(packet));
    }};
    const proxy = {async complete() {
      report.scripted_callback_entries++;
      throw Error('checkpoint_smoke_synthetic_before_inference');
    }};
    const firstResult = await processOne({request: first, queue, proxy, ...adapter});
    const secondResult = await processOne({request: second, queue, proxy, ...adapter});
    const observed = await adapter.snapshot();
    const after = await store.read();
    const reopenedStore = createGitHubCheckpointStore({github, branch, campaignId: CAMPAIGN_ID});
    const reopened = await createPaidCampaignLedger({directory: absolute, profile, priorSpendUsd: PRIOR,
      checkpointStore: reopenedStore, now});
    const recovered = await reopened.snapshot();
    const recoveredCheckpoint = await reopenedStore.read();
    const local = JSON.parse(await fs.readFile(path.join(absolute, 'campaign.json'), 'utf8'));
    const unresolved = Object.values(recovered.calls).filter(call => !['completed', 'refused'].includes(call.broker.state))
      .map(call => call.call_id).sort();
    report.first_attempt = firstResult;
    report.second_attempt = secondResult;
    report.first_call_identity = first.identity;
    report.second_call_identity = second.identity;
    report.framework_accounting = {known_actual_usd: recovered.known_actual_usd,
      known_total_including_prior_usd: recovered.known_total_including_prior_usd,
      unresolved_reserved_usd: recovered.unresolved_reserved_usd,
      reserved_call_count: recovered.reserved_call_count};
    report.ledger_holds = recovered.holds;
    report.unresolved_call_ids = unresolved;
    report.admission_hold_active = unresolved.length > 0 || recovered.holds.length > 0;
    report.profile_fingerprint = recovered.profile_fingerprint;
    report.checkpoint = after;
    report.reopened_checkpoint = recoveredCheckpoint;
    report.local_state_sha256 = digest(local);
    report.response_sha256 = Object.fromEntries([...packets].map(([id, packet]) => [id, packet.response_sha256]));
    report.reopen_matches = canonical(observed) === canonical(recovered)
      && canonical(after) === canonical(recoveredCheckpoint);
    const qualified = firstResult.state === 'unknown' && firstResult.sent === true
      && secondResult.state === 'unknown' && secondResult.sent === false
      && report.scripted_callback_entries === 1 && recovered.known_actual_usd === '0'
      && recovered.known_total_including_prior_usd === '0' && recovered.unresolved_reserved_usd === '0.01'
      && recovered.reserved_call_count === 1 && recovered.calls[first.call_id].reservation !== null
      && recovered.calls[first.call_id].settlement === null && recovered.calls[second.call_id].reservation === null
      && unresolved.length === 2 && report.reopen_matches && after !== null
      && after.profile_fingerprint === recovered.profile_fingerprint
      && after.state_sha256 === report.local_state_sha256
      && after.sequence === local.checkpoint_sequence;
    if (qualified) report.status = 'synthetic_real_github_checkpoint_ledger_hold_qualified';
  } catch {
    report.error_code = 'checkpoint_smoke_integration_unresolved_no_retry';
  }
  return report;
}
