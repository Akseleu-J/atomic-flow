import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {runCheckpointSmoke, CAMPAIGN_ID} from '../tools/p07_checkpoint_store_smoke.mjs';

const BRANCH = 'codex/p07-model-queue-paid-20261028-01';
const SHA = 'a'.repeat(40);
const NOW = 1700000000000;
const blob = raw => createHash('sha1').update('blob ' + raw.length + '\0').update(raw).digest('hex');
function envelope(raw) {
  return {type: 'file', encoding: 'base64', size: raw.length,
    content: raw.toString('base64'), sha: blob(raw)};
}
function githubFixture() {
  const h = {saved: null, writes: 0, reads: 0, messages: [], commits: []};
  h.github = async (relative, method, body) => {
    const file = 'contents/p07-paid-checkpoints/' + CAMPAIGN_ID + '.json';
    if (!method) {
      assert.equal(relative, file + '?ref=' + encodeURIComponent(BRANCH));
      h.reads++;
      return h.saved ? {status: 200, data: structuredClone(h.saved)} : {status: 404, data: {}};
    }
    assert.equal(relative, file);
    assert.equal(method, 'PUT');
    assert.equal(body.branch, BRANCH);
    assert.equal(body.message, '[skip ci] paid checkpoint ' + CAMPAIGN_ID);
    if (h.saved === null ? Object.hasOwn(body, 'sha') : body.sha !== h.saved.sha) return {status: 409, data: {}};
    const status = h.saved ? 200 : 201;
    h.saved = envelope(Buffer.from(body.content, 'base64'));
    h.writes++;
    h.messages.push(body.message);
    const value = JSON.parse(Buffer.from(h.saved.content, 'base64'));
    h.commits.push(value);
    return {status, data: {content: {sha: h.saved.sha}, commit: {sha: 'b'.repeat(40)}}};
  };
  return h;
}
async function directory(t) {
  const parent = await fs.mkdtemp(path.join(os.tmpdir(), 'p07-checkpoint-smoke-test-'));
  t.after(() => fs.rm(parent, {recursive: true, force: true}));
  return path.join(parent, 'fresh-smoke');
}

test('real store and ledger integration preserves synthetic unknown hold across owners and reopen', async t => {
  const h = githubFixture(), root = await directory(t);
  const report = await runCheckpointSmoke({github: h.github, branch: BRANCH, directory: root,
    frameworkSha: SHA, now: () => NOW});
  assert.equal(report.status, 'synthetic_real_github_checkpoint_ledger_hold_qualified');
  assert.equal(report.provider_calls, 0);
  assert.equal(report.scripted_callback_entries, 1);
  assert.equal(report.model_generation, false);
  assert.equal(report.model_response_generated, false);
  assert.equal(report.route_qualification, 'not_run');
  assert.equal(report.zero_charged, true);
  assert.equal(report.actual_provider_charge_usd, '0');
  assert.deepEqual(report.framework_accounting, {known_actual_usd: '0', known_total_including_prior_usd: '0',
    unresolved_reserved_usd: '0.01', reserved_call_count: 1});
  assert.equal(report.admission_hold_active, true);
  assert.equal(report.unresolved_call_ids.length, 2);
  assert.equal(report.reopen_matches, true);
  assert.equal(report.checkpoint.state_sha256, report.local_state_sha256);
  assert.deepEqual(report.checkpoint, report.reopened_checkpoint);
  assert.equal(report.first_call_identity.owner, 'akseley');
  assert.equal(report.second_call_identity.owner, 'umirbayulgaisha');
  assert.equal(report.first_call_identity.framework_sha, SHA);
  assert.equal(report.second_call_identity.workflow_sha, SHA);
  assert.equal(report.first_attempt.state, 'unknown');
  assert.equal(report.first_attempt.sent, true);
  assert.equal(report.second_attempt.state, 'unknown');
  assert.equal(report.second_attempt.sent, false);
  assert.ok(h.writes >= 5);
  assert.deepEqual(h.commits.map(value => value.sequence), h.commits.map((value, i) => i + 1));
  assert.equal(Object.keys(report.response_sha256).length, 2);
  assert.match(report.scope, /synthetic/);
  assert.match(report.reservation_scope, /no provider was contacted/);
  const ledger = JSON.parse(await fs.readFile(path.join(root, 'campaign.json'), 'utf8'));
  assert.equal(ledger.profile.total_budget_usd, '1');
  assert.deepEqual(ledger.prior_spend_usd, {total: '0', owners: {akseley: '0', umirbayulgaisha: '0'}});
  for (const call of Object.values(ledger.calls)) {
    assert.equal(call.settlement, null);
    assert.equal(Object.hasOwn(call.broker, 'result'), false);
    assert.equal(call.broker.response.result, null);
  }
});

test('fresh local directory and fresh remote campaign are mandatory; no automatic resume', async t => {
  const h = githubFixture(), root = await directory(t);
  await runCheckpointSmoke({github: h.github, branch: BRANCH, directory: root, frameworkSha: SHA, now: () => NOW});
  const writes = h.writes;
  await assert.rejects(runCheckpointSmoke({github: h.github, branch: BRANCH, directory: root,
    frameworkSha: SHA, now: () => NOW}), /fresh_directory_required/);
  const another = path.join(path.dirname(root), 'different-local-directory');
  await assert.rejects(runCheckpointSmoke({github: h.github, branch: BRANCH, directory: another,
    frameworkSha: SHA, now: () => NOW}), /fresh_remote_campaign_required/);
  assert.equal(h.writes, writes);
  await assert.rejects(fs.stat(another), {code: 'ENOENT'});
});

test('uncertain checkpoint acknowledgement stops before callback and never retries write', async t => {
  const h = githubFixture(), root = await directory(t), normal = h.github;
  const github = async (...args) => {
    const result = await normal(...args);
    if (args[1] === 'PUT') throw Error('synthetic lost GitHub acknowledgement');
    return result;
  };
  const report = await runCheckpointSmoke({github, branch: BRANCH, directory: root, frameworkSha: SHA, now: () => NOW});
  assert.equal(report.status, 'not_qualified');
  assert.equal(report.error_code, 'checkpoint_smoke_integration_unresolved_no_retry');
  assert.equal(report.scripted_callback_entries, 0);
  assert.equal(report.provider_calls, 0);
  assert.equal(report.zero_charged, true);
  assert.equal(h.writes, 1);
});

test('invalid branch, framework and clock are refused without writes', async t => {
  const h = githubFixture(), root = await directory(t);
  await assert.rejects(runCheckpointSmoke({github: h.github, branch: 'main', directory: root,
    frameworkSha: SHA, now: () => NOW}));
  await assert.rejects(runCheckpointSmoke({github: h.github, branch: BRANCH, directory: root,
    frameworkSha: 'main', now: () => NOW}));
  await assert.rejects(runCheckpointSmoke({github: h.github, branch: BRANCH, directory: root,
    frameworkSha: SHA, now: () => NaN}));
  assert.equal(h.writes, 0);
  assert.equal(h.reads, 0);
});

test('remote checkpoint tampering fails rather than starting a new synthetic run', async t => {
  const h = githubFixture(), root = await directory(t);
  h.saved = envelope(Buffer.from('{"schema_version":1,"sequence":1,"state_sha256":"' + 'c'.repeat(64)
    + '","profile_fingerprint":"' + 'd'.repeat(64) + '"}', 'utf8'));
  await assert.rejects(runCheckpointSmoke({github: h.github, branch: BRANCH, directory: root,
    frameworkSha: SHA, now: () => NOW}));
  assert.equal(h.writes, 0);
});
