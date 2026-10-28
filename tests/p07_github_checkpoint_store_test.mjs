import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createGitHubCheckpointStore} from '../tools/p07_github_checkpoint_store.mjs';

const BRANCH = 'codex/p07-model-queue-paid-20261028-01';
const CAMPAIGN = 'p07_paid_main';
const COMMIT = 'a'.repeat(40);
const sorted = value => JSON.stringify(Object.fromEntries(Object.keys(value).sort().map(key => [key, value[key]])));
const blob = raw => createHash('sha1').update('blob ' + raw.length + '\0').update(raw).digest('hex');
const checkpoint = (sequence = 1, changes = {}) => ({schema_version: 1, sequence,
  state_sha256: 'b'.repeat(64), profile_fingerprint: 'c'.repeat(64), ...changes});
function envelope(raw) {
  const bytes = Buffer.isBuffer(raw) ? raw : Buffer.from(raw, 'utf8');
  return {type: 'file', encoding: 'base64', size: bytes.length, sha: blob(bytes), content: bytes.toString('base64')};
}
function fixture(initial = null) {
  const h = {saved: initial === null ? null : envelope(sorted(initial)), calls: [], writes: 0};
  h.github = async (relative, method, body) => {
    h.calls.push({relative, method, body: body ? structuredClone(body) : body});
    if (!method) return h.saved === null ? {status: 404, data: {message: 'not found'}}
      : {status: 200, data: structuredClone(h.saved)};
    assert.equal(method, 'PUT'); h.writes++;
    if (h.saved === null ? Object.hasOwn(body, 'sha') : body.sha !== h.saved.sha) return {status: 409, data: {}};
    const status = h.saved === null ? 201 : 200;
    h.saved = envelope(Buffer.from(body.content, 'base64'));
    return {status, data: {content: {sha: h.saved.sha}, commit: {sha: COMMIT}}};
  };
  h.store = createGitHubCheckpointStore({github: (...args) => h.github(...args), branch: BRANCH, campaignId: CAMPAIGN});
  return h;
}
const sanitized = error => error.message === 'paid_checkpoint_operation_unresolved';

test('missing checkpoint is null and a new checkpoint receives exact create acknowledgement', async () => {
  const h = fixture(), next = checkpoint();
  assert.equal(await h.store.read(), null);
  assert.deepEqual(await h.store.compareAndSwap(null, next), next);
  assert.deepEqual(await h.store.read(), next);
  assert.equal(h.writes, 1);
  assert.equal(h.calls[0].relative, 'contents/p07-paid-checkpoints/' + CAMPAIGN + '.json?ref=' + encodeURIComponent(BRANCH));
  const write = h.calls.find(call => call.method === 'PUT');
  assert.equal(write.relative, 'contents/p07-paid-checkpoints/' + CAMPAIGN + '.json');
  assert.equal(write.body.branch, BRANCH);
  assert.equal(write.body.message, '[skip ci] paid checkpoint ' + CAMPAIGN);
  assert.equal(Object.hasOwn(write.body, 'sha'), false);
  assert.equal(Buffer.from(write.body.content, 'base64').toString('utf8'), sorted(next));
});

test('updates use the current content SHA and preserve the exact profile sequence', async () => {
  const first = checkpoint(), h = fixture(first), originalSha = h.saved.sha;
  const next = checkpoint(2, {state_sha256: 'd'.repeat(64)});
  assert.deepEqual(await h.store.compareAndSwap(first, next), next);
  assert.equal(h.calls.find(call => call.method === 'PUT').body.sha, originalSha);
  assert.deepEqual(await h.store.read(), next);
});

test('different expected checkpoint and missing prior state fail before PUT', async () => {
  for (const [initial, expected] of [
    [checkpoint(), null], [null, checkpoint()], [checkpoint(), checkpoint(1, {state_sha256: 'd'.repeat(64)})],
  ]) {
    const h = fixture(initial);
    await assert.rejects(h.store.compareAndSwap(expected, checkpoint(expected ? 2 : 1)), sanitized);
    assert.equal(h.writes, 0);
  }
});

test('sequence/profile/schema failures refuse without I/O', async () => {
  const h = fixture(checkpoint());
  for (const next of [checkpoint(1), checkpoint(3), checkpoint(2, {profile_fingerprint: 'd'.repeat(64)}),
    checkpoint(2, {schema_version: true}), checkpoint(2, {state_sha256: 'B'.repeat(64)}),
    {...checkpoint(2), extra: 'unreviewed'}, checkpoint(2.5), checkpoint(Number.MAX_SAFE_INTEGER + 1)]) {
    await assert.rejects(h.store.compareAndSwap(checkpoint(), next), sanitized);
  }
  await assert.rejects(h.store.compareAndSwap(null, checkpoint(2)), sanitized);
  assert.equal(h.calls.length, 0);
});

test('concurrent creates cannot both accept one fresh checkpoint', async () => {
  const h = fixture();
  const results = await Promise.allSettled([h.store.compareAndSwap(null, checkpoint()),
    h.store.compareAndSwap(null, checkpoint(1, {state_sha256: 'd'.repeat(64)}))]);
  assert.equal(results.filter(result => result.status === 'fulfilled').length, 1);
  assert.equal(results.filter(result => result.status === 'rejected').length, 1);
  assert.equal(h.writes, 2);
});

test('concurrent updates use stale blob CAS and only one can acknowledge', async () => {
  const first = checkpoint(), h = fixture(first);
  const results = await Promise.allSettled([h.store.compareAndSwap(first, checkpoint(2)),
    h.store.compareAndSwap(first, checkpoint(2, {state_sha256: 'd'.repeat(64)}))]);
  assert.equal(results.filter(result => result.status === 'fulfilled').length, 1);
  assert.equal(results.filter(result => result.status === 'rejected').length, 1);
  assert.equal(h.writes, 2);
});

test('PUT conflicts and unacknowledged writes have no automatic retry', async () => {
  for (const mode of ['409', 'throw-after-write']) {
    const h = fixture(), normal = h.github;
    h.github = async (...args) => {
      if (args[1] === 'PUT') {
        if (mode === '409') { h.writes++; return {status: 409, data: {message: 'private response'}}; }
        await normal(...args); throw Error('private credential/path response text');
      }
      return normal(...args);
    };
    await assert.rejects(h.store.compareAndSwap(null, checkpoint()), sanitized);
    assert.equal(h.writes, 1);
    if (mode === 'throw-after-write') assert.deepEqual(await h.store.read(), checkpoint());
  }
});

test('acknowledgement must match expected HTTP status, canonical blob and full commit SHA', async () => {
  for (const edit of [
    ack => ack.status = 200,
    ack => ack.data.content.sha = 'e'.repeat(40),
    ack => delete ack.data.content,
    ack => ack.data.commit.sha = 'abc123',
    ack => ack.data.commit.sha = 'A'.repeat(40),
    ack => delete ack.data.commit,
  ]) {
    const h = fixture(), normal = h.github;
    h.github = async (...args) => {
      const ack = await normal(...args);
      if (args[1] === 'PUT') edit(ack);
      return ack;
    };
    await assert.rejects(h.store.compareAndSwap(null, checkpoint()), sanitized);
    assert.equal(h.writes, 1);
  }
});

test('wrong file types, links, size, encoding, blob SHA and base64 are rejected', async () => {
  const good = envelope(sorted(checkpoint()));
  for (const edit of [
    value => value.type = 'dir', value => value.type = 'symlink',
    value => value.target = 'outside', value => value.submodule_git_url = 'private',
    value => value.encoding = 'utf-8', value => value.size = 16385,
    value => value.size--, value => value.sha = 'f'.repeat(40),
    value => value.sha = 'A'.repeat(40), value => value.content += '!',
    value => value.content = 'AB==', value => value.size = true,
  ]) {
    const h = fixture(), body = structuredClone(good); edit(body);
    h.github = async () => ({status: 200, data: body});
    await assert.rejects(h.store.read(), sanitized);
  }
});

test('invalid JSON, duplicate keys, wrong schema and noncanonical bytes are rejected', async () => {
  for (const raw of [
    '{broken', '[]', 'null', sorted({...checkpoint(), extra: 1}),
    sorted(checkpoint()).replace('"sequence":1', '"sequence":1,"sequence":1'),
    JSON.stringify(checkpoint(), null, 2),
    sorted(checkpoint()).replace('"sequence":1', '"sequence":1.0'),
    sorted(checkpoint()).replace('"schema_version":1', '"schema_version":true'),
  ]) {
    const h = fixture(); h.github = async () => ({status: 200, data: envelope(raw)});
    await assert.rejects(h.store.read(), sanitized);
  }
  const h = fixture(); h.github = async () => ({status: 200, data: envelope(Buffer.from([0xff]))});
  await assert.rejects(h.store.read(), sanitized);
});

test('base64 whitespace formatting is accepted while decoded bytes remain exact', async () => {
  const h = fixture(checkpoint());
  h.saved.content = h.saved.content.match(/.{1,60}/g).join('\n') + '\n';
  assert.deepEqual(await h.store.read(), checkpoint());
});

test('read failures are sanitized and never retried', async () => {
  for (const response of [null, {status: 500, data: {message: 'sensitive body'}}, {status: 302, data: {}}]) {
    let calls = 0;
    const store = createGitHubCheckpointStore({github: async () => { calls++; return response; }, branch: BRANCH, campaignId: CAMPAIGN});
    await assert.rejects(store.read(), sanitized);
    assert.equal(calls, 1);
  }
  const store = createGitHubCheckpointStore({github: async () => { throw Error('sensitive token and URL'); },
    branch: BRANCH, campaignId: CAMPAIGN});
  await assert.rejects(store.read(), sanitized);
});

test('strict branch and campaign validation reject ref/path injection before any I/O', () => {
  for (const branch of ['main', 'codex/p07-model-queue-paid-20261028-01?ref=main',
    'codex/p07-model-queue-paid-20261028-01/../main', 'codex/p07-model-queue-task-20261028-01',
    'codex/p07-model-queue-paid-20261028-1', 'codex/p07-model-queue-paid-20261028-01\n']) {
    assert.throws(() => createGitHubCheckpointStore({github: async () => assert.fail('I/O'), branch, campaignId: CAMPAIGN}),
      error => error.message === 'paid_checkpoint_configuration_refused');
  }
  for (const campaignId of ['', '../other', 'p07?ref=main', 'p07/main', 'p07\n', 'x'.repeat(129)]) {
    assert.throws(() => createGitHubCheckpointStore({github: async () => assert.fail('I/O'), branch: BRANCH, campaignId}),
      error => error.message === 'paid_checkpoint_configuration_refused');
  }
});
