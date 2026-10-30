import test from 'node:test';
import assert from 'node:assert/strict';
import {MODEL, digest} from '../tools/p07_model_broker.mjs';
import {CAMPAIGN_ID, TASK_ID, WIRE_CONFIG, PROFILE, PROFILE_SHA256, ORIGINALS, QUERY,
  createScriptedTaskProxy} from '../tools/p07_scripted_task_proxy.mjs';

const SHA = 'a'.repeat(40);
let count = 0;
function request(role, content) {
  const req = {schema_version: 1, kind: 'p07_model_request', call_id: 'mq_' + (++count).toString(16).padStart(32, '0'),
    identity: {campaign_id: CAMPAIGN_ID, task_id: TASK_ID, owner: 'akseley', arm: 'scripted',
      framework_sha: SHA, workflow_sha: SHA, profile_sha256: PROFILE_SHA256}, config: {...WIRE_CONFIG},
    messages: [{role: 'system', content: 'Reply with ONE JSON object. No prose.'},
      {role: 'user', content: content ?? '<role>' + role + '</role> public scripted request'}]};
  return rehash(req);
}
function rehash(req) {
  req.messages_sha256 = digest(req.messages);
  delete req.request_sha256;
  req.request_sha256 = digest(req);
  return req;
}
const planner = () => request(null, 'You are the PLANNER. One step of an ongoing task failed. Replan the remaining work.\n\nORIGINAL TASK\n'
  + QUERY + '\nFAILED STEP\nfailed ordinary requirement\nHOST PLANNING OBSERVATIONS (no permissions or action replay)\ncomplete current check');
const proxy = () => createScriptedTaskProxy({frameworkSha: SHA});
const answer = async p => JSON.parse((await p.complete(request('answerer'))).content);

test('profile is deterministic and frozen, with explicit zero-provider scope', () => {
  assert.equal(PROFILE_SHA256, '5f4bb8503d5045838ee1e4528879bae0187c4392e66f9e8931ac833aac147e89');
  assert.equal(digest(PROFILE), PROFILE_SHA256);
  assert.equal(PROFILE.physical_provider_calls, 0);
  assert.equal(PROFILE.model_generation, false);
  assert.throws(() => WIRE_CONFIG.max_retries = 1);
  assert.throws(() => createScriptedTaskProxy({frameworkSha: 'main'}));
});

test('public failure/replan/two-file repair script retains exact roles and zero usage', async () => {
  const p = proxy();
  assert.equal(JSON.parse((await p.complete(request('parser'))).content).task_type, 'modify');
  assert.equal((await answer(p)).args.command, 'check');
  assert.match((await answer(p)).text, /failed/);
  await p.complete(request('critic'));
  const plan = JSON.parse((await p.complete(planner())).content);
  assert.deepEqual(plan.steps[0].scope_paths, ['a.py', 'b.py']);
  assert.equal(plan.steps[0].verify_with, 'pytest');
  await p.complete(request('parser'));
  assert.equal((await answer(p)).args.path, 'a.py');
  assert.equal((await answer(p)).args.path, 'b.py');
  const patch = await answer(p);
  assert.equal(patch.tool, 'patch_files');
  assert.deepEqual(patch.args.patches.map(v => v.path), ['a.py', 'b.py']);
  for (const part of patch.args.patches) {
    assert.equal(part.old_text, ORIGINALS[part.path]);
    assert.equal(part.new_text, ORIGINALS[part.path].replaceAll('original', 'renamed'));
    assert.match(part.expected_hash, /^[a-f0-9]{40}$/);
  }
  assert.equal((await answer(p)).args.command, 'check');
  const req = request('answerer'), result = await p.complete(req);
  assert.match(JSON.parse(result.content).text, /completed/);
  assert.equal(result.attempt_id, req.call_id);
  assert.equal(result.resolved_model, MODEL);
  assert.equal(result.cost_usd, '0');
  assert.equal(result.cost_source, 'scripted_zero_provider_calls');
  assert.equal(result.tokens_in, 0); assert.equal(result.tokens_out, 0);
  await p.complete(request('critic'));
  assert.deepEqual(p.snapshot(), {roles: ['parser', 'answerer', 'answerer', 'critic', 'host_planner', 'parser',
    'answerer', 'answerer', 'answerer', 'answerer', 'answerer', 'critic'], answerer_steps: 7,
    host_replans: 1, provider_calls: 0, model_generation: false, profile_sha256: PROFILE_SHA256, script_consumed: true});
  await assert.rejects(p.complete(request('answerer')), /sequence exhausted/);
  await assert.rejects(p.complete(planner()), /extra or early/);
  await assert.rejects(p.complete(request('critic')), /critic phase/);
});

test('unknown roles, arbitrary untagged requests and duplicate tags are refused before consumption', async () => {
  const p = proxy();
  for (const req of [request('tools'), request(null, 'arbitrary untagged request'),
    request(null, '<role>parser</role><role>answerer</role>')]) {
    await assert.rejects(p.complete(req));
  }
  assert.equal(p.snapshot().roles.length, 0);
});

test('role phases and original physical call cannot replay', async () => {
  const p = proxy(), req = request('parser');
  await p.complete(req);
  await assert.rejects(p.complete(req), /replay/);
  await assert.rejects(p.complete(request('parser')), /parser phase/);
  await assert.rejects(p.complete(request('critic')), /critic phase/);
  await assert.rejects(p.complete(planner()), /early/);
  await answer(p); await answer(p);
  await assert.rejects(p.complete(request('answerer')), /missing replan/);
  assert.equal(p.snapshot().answerer_steps, 2);
});

test('profile, owner, runtime, workflow, arm, task and config substitution fail closed', async () => {
  for (const edit of [r => r.identity.owner = 'umirbayulgaisha', r => r.identity.arm = 'atomic',
    r => r.identity.task_id = 'other', r => r.identity.campaign_id = 'other',
    r => r.identity.framework_sha = 'b'.repeat(40), r => r.identity.workflow_sha = 'b'.repeat(40),
    r => r.identity.profile_sha256 = 'b'.repeat(64), r => r.config.temperature = '0.1',
    r => r.config.max_input_tokens = 16000, r => r.config.max_output_tokens = 1024]) {
    const p = proxy(), req = request('parser');
    edit(req); rehash(req);
    await assert.rejects(p.complete(req), /binding mismatch/);
    assert.equal(p.snapshot().roles.length, 0);
  }
});
