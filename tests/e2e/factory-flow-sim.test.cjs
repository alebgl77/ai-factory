'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const sim = require('../../dashboard/docs/flow-sim.js');

const scenario = overrides => ({ ...sim.DEFAULT_SCENARIO, ...overrides });
const code = expected => error => error instanceof sim.FlowError && error.code === expected;

test('nominal batch is deterministic and accounting distinguishes delivery, attempts and cost', () => {
  const graph = sim.template();
  const result = sim.run(graph, scenario({}));
  assert.deepEqual(sim.run(graph, scenario({})), result);
  assert.deepEqual([result.delivered, result.blocked, result.failed], [100, 0, 0]);
  assert.deepEqual([result.attempts, result.remote, result.local], [100, 100, 0]);
  assert.deepEqual([result.input, result.output, result.tokens], [60000, 22000, 82000]);
  assert.equal(result.eur, 0.1038);
  assert.equal(result.latencyMs, 680);
});

test('C3 and C4 prefer the restricted branch, with no distant model consumption', () => {
  for (const classification of [3, 4]) {
    const result = sim.run(sim.template(), scenario({ classification }));
    assert.deepEqual([result.delivered, result.remote, result.local], [100, 0, 100]);
    assert.ok(result.followed.some(edge => edge.route === 'restricted'));
    assert.ok(!result.steps.some(step => step.type === 'remote'));
  }
});

test('C3/C4 blocks a distant model even in an edited topology without gateway', () => {
  const graph = {
    nodes: [sim.makeNode('source', 1, 0, 0), sim.makeNode('remote', 2, 1, 0), sim.makeNode('sink', 3, 2, 0)],
    edges: [{ from: 1, to: 2, route: 'default' }, { from: 2, to: 3, route: 'default' }],
  };
  const result = sim.run(graph, scenario({ classification: 4, fault: 'remote' }));
  assert.deepEqual([result.delivered, result.blocked, result.attempts, result.tokens], [0, 100, 0, 0]);
  assert.deepEqual(result.warnings, ['restricted_remote']);
});

test('unavailable model incurs delay and attempt while repli consumes only its own tokens', () => {
  const result = sim.run(sim.template(), scenario({ fault: 'remote' }));
  assert.deepEqual([result.delivered, result.attempts, result.remote, result.local], [100, 200, 0, 100]);
  assert.equal(result.tokens, 82000);
  assert.equal(result.eur, 0.03384);
  assert.deepEqual(result.warnings, ['unavailable', 'fallback_used']);
  assert.ok(result.followed.some(edge => edge.route === 'fallback'));
});

test('repli cannot bypass remote classification and does not trigger a second repli', () => {
  const graph = sim.template();
  graph.edges.find(edge => edge.route === 'fallback').to = 4;
  const blocked = sim.run(graph, scenario({ classification: 4, fault: 'gpu' }));
  assert.deepEqual([blocked.blocked, blocked.attempts, blocked.tokens, blocked.remote], [100, 100, 0, 0]);
  assert.deepEqual(blocked.warnings, ['unavailable', 'fallback_used', 'restricted_remote']);
  const down = sim.template();
  down.edges.find(edge => edge.route === 'fallback').to = 4;
  const failed = sim.run(down, scenario({ fault: 'remote' }));
  assert.deepEqual([failed.failed, failed.attempts, failed.tokens], [100, 200, 0]);
  assert.equal(failed.followed.filter(edge => edge.route === 'fallback').length, 1);
});

test('gateway rejects classification and token caps without a model attempt', () => {
  const graph = sim.template();
  const policy = graph.nodes.find(node => node.type === 'policy');
  policy.classMax = 2;
  policy.promptMax = 400;
  const tooSensitive = sim.run(graph, scenario({ classification: 3 }));
  const tooLong = sim.run(graph, scenario({ input: 401 }));
  assert.deepEqual([tooSensitive.blocked, tooSensitive.attempts, tooLong.blocked, tooLong.attempts], [100, 0, 100, 0]);
  assert.deepEqual(tooSensitive.warnings, ['policy_class']);
  assert.deepEqual(tooLong.warnings, ['policy_tokens']);
});

test('synthetic retrieval adds input tokens; consumed model tokens survive incomplete delivery', () => {
  const graph = sim.template();
  graph.nodes.push(sim.makeNode('retrieval', 7, 2, 0));
  graph.edges.find(edge => edge.from === 3 && edge.route === 'default').to = 7;
  graph.edges.push({ from: 7, to: 4, route: 'default' });
  graph.edges = graph.edges.filter(edge => edge.from !== 4);
  const result = sim.run(graph, scenario({ requests: 3, input: 100, output: 20 }));
  assert.deepEqual([result.delivered, result.failed, result.remote, result.input, result.output], [0, 3, 3, 1452, 60]);
  assert.deepEqual(result.warnings, ['unrouted']);
});

test('graph rejects loops, duplicate routes, invalid port direction and forbidden metadata', () => {
  const cycle = sim.template();
  cycle.edges.find(edge => edge.from === 5).to = 2;
  assert.throws(() => sim.validateGraph(cycle), code('cycle'));
  const duplicate = sim.template();
  duplicate.edges.push({ from: 3, to: 4, route: 'default' });
  assert.throws(() => sim.validateGraph(duplicate), code('duplicate_route'));
  const wrong = sim.template();
  wrong.edges.push({ from: 6, to: 1, route: 'default' });
  assert.throws(() => sim.validateGraph(wrong), code('invalid_route'));
  const freeText = sim.template();
  freeText.nodes[0].payload = 'private';
  assert.throws(() => sim.validateGraph(freeText), code('invalid_node'));
});

test('scenario bounds and required source/destination fail clearly', () => {
  assert.throws(() => sim.run(sim.template(), scenario({ requests: 5001 })), code('invalid_scenario'));
  assert.throws(() => sim.run(sim.template(), scenario({ secret: 'x' })), code('invalid_scenario'));
  assert.throws(() => sim.run({ nodes: [sim.makeNode('sink', 1, 0, 0)], edges: [] }, scenario({})), code('missing_source'));
  assert.throws(() => sim.run({ nodes: [sim.makeNode('source', 1, 0, 0)], edges: [] }, scenario({})), code('missing_sink'));
});

test('history keeps only valid versioned bounded synthetic records and snapshots are independent', () => {
  const graph = sim.template();
  const original = sim.snapshot(graph, scenario({}), 42);
  graph.nodes[0].x = 4;
  assert.equal(original.graph.nodes[0].x, 0);
  assert.deepEqual(sim.readHistory(JSON.stringify({
    version: sim.VERSION, entries: [original, { ...original, version: 999 }, { ...original, title: 'private' }],
  })), [original]);
  assert.deepEqual(sim.readHistory(JSON.stringify({ version: sim.VERSION, entries: Array(13).fill(original) })), []);
  assert.deepEqual(sim.readHistory(JSON.stringify({ version: 999, entries: [original] })), []);
  assert.deepEqual(sim.readHistory('x'.repeat(150001)), []);
});
