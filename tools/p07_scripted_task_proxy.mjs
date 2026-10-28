/** Zero-provider scripted public TaskRunner proxy. No credential loading or network clients. */
import {createHash} from 'node:crypto';
import {MODEL, canonical, digest, validateRequest} from './p07_model_broker.mjs';

export const CAMPAIGN_ID = 'p07_public_task_queue_smoke';
export const TASK_ID = 'public_coupled_queue_smoke';
export const WIRE_CONFIG = Object.freeze({model: MODEL, max_input_tokens: 16384, max_output_tokens: 4096,
  temperature: null, max_retries: 0});
export const PROFILE = Object.freeze({schema_version: 1, kind: 'scripted_public_queued_task_journey',
  campaign_id: CAMPAIGN_ID, task_id: TASK_ID, owner: 'akseley', arm: 'scripted',
  physical_provider_calls: 0, model_generation: false, wire_config: WIRE_CONFIG,
  price_in_per_mtok: '0', price_out_per_mtok: '0', check_command: 'check', max_task_calls: 40, answerer_steps: 7});
export const PROFILE_SHA256 = digest(PROFILE);
export const ORIGINALS = Object.freeze({
  'a.py': 'from b import original\n\ndef call():\n    return original()\n',
  'b.py': 'def original():\n    return 42\n',
});
export const QUERY = 'Rename original to renamed consistently across a.py and b.py and pass the existing ordinary test';
const sha1 = value => createHash('sha1').update(value, 'utf8').digest('hex');
const answers = [
  {tool: 'run_command', args: {command: 'check'}},
  {text: 'The source check failed; repair is required.', confidence: 'low'},
  {tool: 'read_file', args: {path: 'a.py'}},
  {tool: 'read_file', args: {path: 'b.py'}},
  {tool: 'patch_files', args: {patches: Object.entries(ORIGINALS).map(([path, source]) => ({
    path, expected_hash: sha1(source), old_text: source, new_text: source.replaceAll('original', 'renamed'),
  }))}},
  {tool: 'run_command', args: {command: 'check'}},
  {text: 'Coupled rename completed and ordinary checks passed.', confidence: 'low'},
];
const repair = {steps: [{description: 'Perform coupled original-to-renamed source edit and verify ordinary requirement',
  tools_needed: ['read_file', 'patch_files', 'run_command'], verify_with: 'pytest', scope_paths: ['a.py', 'b.py']}],
  rationale: 'Repair failed ordinary requirement under original scope'};

function requestRole(req) {
  if (req.messages.length < 2 || req.messages[1].role !== 'user') throw new Error('bound user prompt required');
  const user = req.messages[1].content;
  const matches = [...user.matchAll(/<role>(\w+)<\/role>/g)];
  if (matches.length === 1) {
    const role = matches[0][1];
    if (!['parser', 'answerer', 'critic'].includes(role)) throw new Error('unknown scripted role');
    return role;
  }
  if (matches.length || user.includes('<role>') || !user.startsWith('You are the PLANNER. One step of an ongoing task failed. Replan the remaining work.')
      || !user.includes(QUERY) || !user.includes('\nFAILED STEP\n')
      || !user.includes('\nHOST PLANNING OBSERVATIONS (no permissions or action replay)\n')) {
    throw new Error('unknown host planner request');
  }
  return 'host_planner';
}

/** One fresh proxy per explicitly reviewed scripted campaign run; no restart/resume.
 * Call through the durable processOne broker, whose original-call journal prevents duplicate sends.
 * The operator still supplies strict admission, real exclusive lock and immutable GitHub response writes.
 */
export function createScriptedTaskProxy({frameworkSha, workflowSha = frameworkSha} = {}) {
  if (!/^[a-f0-9]{40}$/.test(frameworkSha || '') || !/^[a-f0-9]{40}$/.test(workflowSha || '')) {
    throw new Error('exact framework/workflow SHAs required');
  }
  const seen = new Set(), roles = [], parserPhases = new Set(), criticPhases = new Set();
  let index = 0, planned = false;
  return {
    async complete(req) {
      validateRequest(req);
      const id = req.identity;
      if (id.campaign_id !== CAMPAIGN_ID || id.task_id !== TASK_ID || id.owner !== 'akseley' || id.arm !== 'scripted'
          || id.framework_sha !== frameworkSha || id.workflow_sha !== workflowSha || id.profile_sha256 !== PROFILE_SHA256
          || canonical(req.config) !== canonical(WIRE_CONFIG)) throw new Error('scripted profile binding mismatch');
      if (seen.has(req.call_id)) throw new Error('scripted physical call cannot replay');
      if (roles.length >= PROFILE.max_task_calls) throw new Error('scripted call cap exceeded');
      const role = requestRole(req);
      let answer;
      if (role === 'answerer') {
        if (index >= answers.length || (index >= 2 && !planned)) throw new Error('scripted answer sequence exhausted or missing replan');
        answer = answers[index];
        index++;
      } else if (role === 'host_planner') {
        if (planned || index !== 2) throw new Error('unexpected extra or early replan');
        planned = true;
        answer = repair;
      } else if (role === 'parser') {
        if (![0, 2].includes(index) || parserPhases.has(index) || (index === 2 && !planned)) throw new Error('unexpected parser phase');
        parserPhases.add(index);
        answer = {task_type: 'modify', focus_terms: ['original', 'renamed', 'call']};
      } else {
        if (![2, 7].includes(index) || criticPhases.has(index)) throw new Error('unexpected critic phase');
        criticPhases.add(index);
        answer = {verdict: 'ok', citation_coverage: 1};
      }
      seen.add(req.call_id);
      roles.push(role);
      return {content: canonical(answer), tokens_in: 0, tokens_out: 0, cost_usd: '0', latency_ms: 0,
        truncated: false, cost_source: 'scripted_zero_provider_calls', resolved_backend: 'kaggle_model_proxy',
        resolved_model: MODEL, attempt_id: req.call_id};
    },
    snapshot() {
      return {roles: [...roles], answerer_steps: index, host_replans: Number(planned),
        provider_calls: 0, model_generation: false, profile_sha256: PROFILE_SHA256,
        script_consumed: index === answers.length && planned};
    },
  };
}
