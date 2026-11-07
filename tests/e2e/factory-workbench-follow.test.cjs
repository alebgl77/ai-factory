/* SPDX-License-Identifier: MIT */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const sandbox = { window: {}, AbortController };
vm.runInNewContext(fs.readFileSync(path.join(__dirname,
  '../../dashboard/assets/factory-workbench-follow.js'), 'utf8'), sandbox);
const { create } = sandbox.window.FactoryWorkbenchFollow;

const row = (id = 1, revision = 1, status = 'waiting') => ({
  case_id: id.toString(16).padStart(32, '0'), revision, status,
});
const snapshot = (...encounters) => ({ status: 200, body: { encounters } });

function follower(options = {}) {
  const starts = [], decisions = [], clears = [];
  const api = create({
    read: async () => snapshot(),
    onStart: event => starts.push({ ...event }),
    onDecision: event => decisions.push({ ...event }),
    onClear: reason => clears.push(reason),
    ...options,
  });
  return { api, starts, decisions, clears };
}

test('throttles polls and discards stale responses after abort and restart', async () => {
  const jobs = [];
  const { api, starts, clears } = follower({
    read: ({ signal, caseId }) => new Promise(resolve => jobs.push({ signal, caseId, resolve })),
  });
  assert.equal(api.tick(0), undefined);
  api.start();
  assert.equal(api.tick(NaN), undefined);
  const abandoned = api.tick(0);
  assert.equal(api.tick(50_000), undefined);
  assert.equal(jobs.length, 1);
  assert.equal(jobs[0].caseId, null);
  api.stop();
  assert.equal(jobs[0].signal.aborted, true);
  api.start();
  const current = api.tick(10);
  jobs[0].resolve({ status: 403 });
  await abandoned;
  assert.equal(api.enabled, true);
  assert.equal(api.tick(20_000), undefined);
  jobs[1].resolve(snapshot(row()));
  await current;
  assert.deepEqual(starts, [row()]);
  assert.deepEqual(clears, ['stopped']);
  assert.equal(api.tick(5009), undefined);
  const next = api.tick(5010);
  assert.equal(jobs[2].caseId, row().case_id);
  jobs[2].resolve(snapshot(row()));
  await next;
  assert.equal(starts.length, 1);
});

test('completing an exchange aborts a targeted poll instead of reusing it for discovery', async () => {
  const jobs = [];
  const { api, starts, decisions } = follower({
    read: ({ signal, caseId }) => new Promise(resolve => jobs.push({ signal, caseId, resolve })),
  });
  api.start();
  const discovery = api.tick(0);
  jobs[0].resolve(snapshot(row()));
  await discovery;
  const targeted = api.tick(5000);
  assert.equal(jobs[1].caseId, row().case_id);
  api.done();
  assert.equal(jobs[1].signal.aborted, true);
  jobs[1].resolve(snapshot(row(1, 2)));
  await targeted;
  assert.deepEqual(starts, [row()]);
  assert.deepEqual(decisions, []);
  const next = api.tick(10_000);
  assert.equal(jobs[2].caseId, null);
  jobs[2].resolve(snapshot(row(2)));
  await next;
  assert.deepEqual(starts, [row(), row(2)]);
});

test('delivers exact terminal decisions once and suppresses replay until a manual restart', async () => {
  for (const status of ['approved', 'rejected', 'changes_requested']) {
    let response = snapshot(row(), row());
    const { api, starts, decisions, clears } = follower({ read: async () => response });
    api.start();
    await api.tick(0);
    assert.equal(Object.isFrozen(api.active), true);
    response = snapshot(row(), row(2, 1, status));
    await api.tick(5000);
    assert.deepEqual(decisions, []);
    response = snapshot(row(1, 1, status));
    await api.tick(10_000);
    await api.tick(15_000);
    assert.deepEqual(decisions, [row(1, 1, status)]);
    api.done();
    response = snapshot(row());
    api.start();
    await api.tick(20_000);
    assert.deepEqual(starts, [row()]);
    assert.deepEqual(clears, []);
    api.stop();
    api.start();
    await api.tick(20_000);
    assert.deepEqual(starts, [row(), row()]);
  }
});

test('withdraws absent or revised pairs before considering another waiting case', async () => {
  for (const replacement of [snapshot(row(2)), snapshot(row(1, 2, 'approved'), row(2))]) {
    let response = snapshot(row());
    const { api, starts, decisions, clears } = follower({ read: async () => response });
    api.start();
    await api.tick(0);
    response = replacement;
    await api.tick(5000);
    assert.equal(api.active, null);
    assert.deepEqual(clears, ['stale']);
    assert.deepEqual(starts, [row()]);
    assert.deepEqual(decisions, []);
    await api.tick(10_000);
    assert.deepEqual(starts, [row(), row(2)]);
  }
});

test('fails closed on access, transport, malformed or over-broad responses', async () => {
  const invalid = [
    ['unauthorized', async () => ({ status: 401 })],
    ['unauthorized', async () => ({ status: 403 })],
    ['offline', async () => ({ status: 503 })],
    ['offline', async () => { throw Error('network unavailable'); }],
    ['malformed', async () => ({ status: 200, body: { encounters: [row()], extra: true } })],
    ['malformed', async () => snapshot({ ...row(), title: 'sensitive' })],
    ['malformed', async () => snapshot({ ...row(), case_id: 'A'.repeat(32) })],
    ['malformed', async () => snapshot({ ...row(), revision: Number.MAX_SAFE_INTEGER + 1 })],
    ['malformed', async () => snapshot(row(1, 0))],
    ['malformed', async () => snapshot(row(1, 1, 'unknown'))],
    ['malformed', async () => snapshot(row(), row(1, 2))],
    ['malformed', async () => snapshot(row(), row(1, 1, 'approved'))],
    ['malformed', async () => snapshot(...Array.from({ length: 101 }, (_, id) => row(id)))],
  ];
  for (const [expected, badRead] of invalid) {
    let read = async () => snapshot(row());
    const { api, starts, decisions, clears } = follower({ read: () => read() });
    api.start();
    await api.tick(0);
    read = badRead;
    await api.tick(5000);
    assert.equal(api.enabled, false);
    assert.equal(api.active, null);
    assert.deepEqual(clears, [expected]);
    assert.deepEqual(decisions, []);
    assert.deepEqual(starts, [row()]);
  }
});

test('skips an unreachable pair once and bounds replay protection without eviction', async () => {
  let id = 1;
  const starts = [];
  const { api, clears } = follower({
    read: async () => snapshot(row(id)), intervalMs: 0,
    onStart: event => { starts.push(event); return false; },
  });
  api.start();
  await api.tick(0);
  await api.tick(1000);
  assert.equal(starts.length, 1);
  assert.equal(clears[0], 'unreachable');
  for (id = 2; id <= 513; id++) await api.tick(id * 1000);
  assert.equal(starts.length, 512);
  assert.equal(clears.at(-1), 'limit');
  assert.equal(api.enabled, false);
  assert.equal(api.active, null);
});
