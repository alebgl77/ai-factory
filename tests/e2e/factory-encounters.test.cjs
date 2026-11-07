/* SPDX-License-Identifier: MIT */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const sandbox = { window: {} };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../dashboard/assets/factory-encounters.js'), 'utf8'), sandbox);
const { planRoute, createController } = sandbox.window.FactoryEncounters;

const bounds = [-3, 3, -3, 3];
const from = [-2.03, -1.47], to = [2.07, -1.43];
const door = (x, z) => Math.abs(x) < .32 && (z < 1 || z > 1.6);
const options = { mode: 'bu-human', from, to, bounds, blocked: door, question: '  Est-ce prêt ?  ' };
const plain = value => JSON.parse(JSON.stringify(value));
const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-7, `${a} ≠ ${b}`);

function advance(controller, stage, dt = .05) {
  for (let i = 0; i < 1600 && controller.state.stage !== stage; i++) controller.update(dt);
  assert.equal(controller.state.stage, stage);
  return controller.state;
}

test('the planner uses a doorway, preserves exact endpoints, and validates every segment', () => {
  const route = plain(planRoute(from, to, { blocked: door, bounds }));
  assert.deepEqual(route[0], from);
  assert.deepEqual(route.at(-1), to);
  assert.ok(route.length > 2);
  assert.deepEqual(route, plain(planRoute(from, to, { blocked: door, bounds })));
  for (let i = 1; i < route.length; i++) {
    const [ax, az] = route[i - 1], [bx, bz] = route[i];
    const steps = Math.ceil(Math.hypot(bx - ax, bz - az) / .08);
    for (let n = 0; n <= steps; n++) {
      assert.equal(door(ax + (bx - ax) * n / steps, az + (bz - az) * n / steps), false);
    }
  }
});

test('closed walls, unsafe endpoints and oversized or malformed inputs fail closed', () => {
  assert.equal(planRoute(from, to, { blocked: x => Math.abs(x) < .32, bounds }), null);
  assert.equal(planRoute(from, [0, 0], { blocked: door, bounds }), null);
  assert.equal(planRoute(from, [4, 0], { blocked: door, bounds }), null);
  assert.equal(planRoute(from, to, { blocked: door, bounds: [-1000, 1000, -1000, 1000] }), null);
  assert.equal(planRoute(Array(2), to, { blocked: door, bounds }), null);
  assert.equal(planRoute(from, to, null), null);
  const controller = createController();
  assert.equal(controller.start(null), false);
  assert.equal(controller.start({ ...options, blocked() { throw new Error('scene unavailable'); } }), false);
  assert.equal(controller.state, null);
});

test('both human destinations wait for an explicit valid reply and return along the route', () => {
  for (const mode of ['bu-human', 'agent-human']) {
    const controller = createController();
    assert.equal(controller.start({ ...options, mode, simulatedReply: 'must be ignored' }), true);
    assert.equal(controller.state.question, 'Est-ce prêt ?');
    assert.equal(controller.state.actor.pose, 'walk');
    assert.equal(controller.respond('trop tôt'), false);
    assert.equal(controller.start(options), false);
    const request = advance(controller, 'requesting');
    assert.equal(request.actor.pose, 'request');
    assert.equal(request.target.pose, 'listen');
    const dx = request.target.x - request.actor.x, dz = request.target.z - request.actor.z;
    close(Math.hypot(dx, dz), .8);
    close(-Math.sin(request.actor.yaw), dx / .8);
    close(-Math.cos(request.actor.yaw), dz / .8);
    close(-Math.sin(request.target.yaw), -dx / .8);
    const waiting = advance(controller, 'waiting');
    for (let i = 0; i < 30; i++) assert.equal(controller.update(.25), waiting);
    assert.equal(controller.respond('   '), false);
    assert.equal(controller.respond('x'.repeat(281)), false);
    assert.equal(controller.respond('  Oui, vérifié.  '), true);
    assert.equal(controller.state.stage, 'replying');
    assert.equal(controller.state.target.pose, 'reply');
    assert.equal(controller.state.reply, 'Oui, vérifié.');
    assert.equal(advance(controller, 'returning').actor.pose, 'walk');
    const done = advance(controller, 'complete');
    close(done.actor.x, from[0]);
    close(done.actor.z, from[1]);
    assert.equal(done.reply, 'Oui, vérifié.');
    assert.equal(controller.respond('réponse tardive'), false);
  }
});

test('agents reply from the supplied demo text and state is deterministic', () => {
  const a = createController(), b = createController();
  const config = { ...options, mode: 'agent-agent', simulatedReply: '  Réponse simulée. ' };
  assert.equal(a.start({ ...config, simulatedReply: '' }), false);
  assert.equal(a.start(config), true);
  assert.equal(b.start(config), true);
  const seen = new Set([a.state.stage]);
  while (a.state.stage !== 'complete') {
    const state = a.update(.17);
    assert.deepEqual(state, b.update(.17));
    if (state.stage === 'walking' || state.stage === 'returning') assert.equal(door(state.actor.x, state.actor.z), false);
    seen.add(state.stage);
  }
  assert.deepEqual([...seen], ['walking', 'requesting', 'replying', 'returning', 'complete']);
  assert.equal(a.state.reply, 'Réponse simulée.');
});

test('cancellation clears motion and reduced motion uses neutral poses at discrete steps', () => {
  const controller = createController();
  assert.equal(controller.start(options), true);
  controller.update(.1);
  assert.equal(controller.cancel(), true);
  const canceled = controller.state;
  assert.equal(canceled.stage, 'canceled');
  close(canceled.actor.x, from[0]);
  assert.equal(controller.update(100), canceled);
  assert.equal(controller.cancel(), false);
  assert.equal(controller.start({ ...options, reducedMotion: true }), true);
  assert.equal(controller.state.actor.pose, 'stand');
  const request = controller.update(.016);
  assert.equal(request.stage, 'requesting');
  close(Math.hypot(request.actor.x - to[0], request.actor.z - to[1]), .8);
  assert.equal(request.actor.pose, 'stand');
  assert.equal(request.target.pose, 'stand');
  advance(controller, 'waiting');
  assert.equal(controller.respond('OK'), true);
  assert.equal(controller.state.target.pose, 'stand');
  advance(controller, 'returning');
  const complete = controller.update(.016);
  assert.equal(complete.stage, 'complete');
  close(complete.actor.x, from[0]);
  close(complete.actor.z, from[1]);
});
