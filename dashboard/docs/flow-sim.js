(function (root, build) {
  const engine = build();
  if (typeof module === 'object' && module.exports) module.exports = engine;
  else root.FactoryFlowSim = engine;
})(globalThis, function () {
  'use strict';

  const VERSION = 1;
  const MAX_NODES = 20;
  const MAX_EDGES = 36;
  const MAX_HISTORY = 12;
  const TYPES = Object.freeze(['source', 'policy', 'router', 'retrieval', 'cpu', 'gpu', 'remote', 'sink']);
  const ROUTES = Object.freeze(['default', 'restricted', 'fallback']);
  const FAULTS = Object.freeze(['none', 'cpu', 'gpu', 'remote']);
  // EUR per million input/output tokens; synthetic catalogue, not provider pricing.
  const PROFILES = Object.freeze({
    cpu: Object.freeze({ input: 0.16, output: 0.40, fixedMs: 85, tokensPerSecond: 420 }),
    gpu: Object.freeze({ input: 0.30, output: 0.72, fixedMs: 55, tokensPerSecond: 1900 }),
    remote: Object.freeze({ input: 0.85, output: 2.40, fixedMs: 140, tokensPerSecond: 1550 }),
  });

  const DEFAULT_SCENARIO = Object.freeze({ classification: 2, requests: 100, input: 600, output: 220, fault: 'none' });

  class FlowError extends Error {
    constructor(code) {
      super(code);
      this.name = 'FlowError';
      this.code = code;
    }
  }

  function fail(code) { throw new FlowError(code); }
  function integer(value, min, max) { return Number.isInteger(value) && value >= min && value <= max; }
  function shape(value, fields) {
    return value !== null && typeof value === 'object' && !Array.isArray(value) &&
      Object.keys(value).length === fields.length && fields.every(field => Object.hasOwn(value, field));
  }

  function makeNode(type, id, x, y) {
    if (!TYPES.includes(type)) fail('invalid_node');
    const node = { id, type, x, y };
    if (type === 'policy') Object.assign(node, { classMax: 4, promptMax: 8192 });
    if (type === 'retrieval') node.contextTokens = 384;
    return node;
  }

  function template() {
    return {
      nodes: [
        makeNode('source', 1, 0, 1), makeNode('policy', 2, 1, 1),
        makeNode('router', 3, 2, 1), makeNode('remote', 4, 3, 0),
        makeNode('gpu', 5, 3, 2), makeNode('sink', 6, 4, 1),
      ],
      edges: [
        { from: 1, to: 2, route: 'default' }, { from: 2, to: 3, route: 'default' },
        { from: 3, to: 4, route: 'default' }, { from: 3, to: 5, route: 'restricted' },
        { from: 3, to: 5, route: 'fallback' }, { from: 4, to: 6, route: 'default' },
        { from: 5, to: 6, route: 'default' },
      ],
    };
  }

  function validateGraph(graph) {
    if (!shape(graph, ['nodes', 'edges']) || !Array.isArray(graph.nodes) ||
        !Array.isArray(graph.edges) || graph.nodes.length > MAX_NODES || graph.edges.length > MAX_EDGES) fail('invalid_graph');
    const nodes = new Map();
    const positions = new Set();
    let source = null;
    for (const node of graph.nodes) {
      const keys = ['id', 'type', 'x', 'y'];
      if (node?.type === 'policy') keys.push('classMax', 'promptMax');
      if (node?.type === 'retrieval') keys.push('contextTokens');
      if (!shape(node, keys) || !TYPES.includes(node.type) || !integer(node.id, 1, 99) ||
          !integer(node.x, 0, 4) || !integer(node.y, 0, 3) || nodes.has(node.id)) fail('invalid_node');
      if (node.type === 'policy' && (!integer(node.classMax, 1, 4) || !integer(node.promptMax, 1, 16384))) fail('invalid_policy');
      if (node.type === 'retrieval' && !integer(node.contextTokens, 0, 4096)) fail('invalid_retrieval');
      const position = node.x + ':' + node.y;
      if (positions.has(position)) fail('occupied_position');
      positions.add(position);
      if (node.type === 'source') {
        if (source) fail('single_source');
        source = node;
      }
      nodes.set(node.id, node);
    }

    const edges = new Map();
    for (const id of nodes.keys()) edges.set(id, new Map());
    for (const edge of graph.edges) {
      if (!shape(edge, ['from', 'to', 'route']) || !nodes.has(edge.from) || !nodes.has(edge.to) ||
          edge.from === edge.to || !ROUTES.includes(edge.route)) fail('invalid_edge');
      const from = nodes.get(edge.from);
      const to = nodes.get(edge.to);
      if (from.type === 'sink' || to.type === 'source' ||
          (from.type !== 'router' && edge.route !== 'default') ||
          (edge.route === 'fallback' && !Object.hasOwn(PROFILES, to.type))) fail('invalid_route');
      if (edges.get(from.id).has(edge.route)) fail('duplicate_route');
      edges.get(from.id).set(edge.route, edge);
    }

    const visiting = new Set();
    const complete = new Set();
    function visit(id) {
      if (visiting.has(id)) fail('cycle');
      if (complete.has(id)) return;
      visiting.add(id);
      for (const edge of edges.get(id).values()) visit(edge.to);
      visiting.delete(id);
      complete.add(id);
    }
    for (const id of nodes.keys()) visit(id);
    return { nodes, edges, source };
  }

  function validateScenario(scenario) {
    if (!shape(scenario, ['classification', 'requests', 'input', 'output', 'fault']) ||
        !integer(scenario.classification, 1, 4) || !integer(scenario.requests, 1, 5000) ||
        !integer(scenario.input, 1, 8192) || !integer(scenario.output, 1, 4096) ||
        !FAULTS.includes(scenario.fault)) fail('invalid_scenario');
  }

  function run(graph, scenario) {
    const { nodes, edges, source } = validateGraph(graph);
    validateScenario(scenario);
    if (!source) fail('missing_source');
    if (![...nodes.values()].some(node => node.type === 'sink')) fail('missing_sink');

    let node = source;
    let prompt = scenario.input;
    let backup = null;
    let attempts = 0;
    let local = 0;
    let remote = 0;
    let input = 0;
    let output = 0;
    let eur = 0;
    let latencyMs = 0;
    let outcome = 'unrouted';
    const steps = [];
    const followed = [];
    const warnings = new Set();

    function jump(edge) {
      if (edge) followed.push(edge);
      node = edge ? nodes.get(edge.to) : null;
    }

    // A fallback can revisit a model already traversed via a different branch.
    // Each taken fallback consumes a prior router checkpoint. A validated DAG
    // and the execution ceiling bound even adversarial, edited topologies.
    const usedBackups = new Set();
    for (let hops = 0; node && hops < MAX_NODES * 2; hops++) {
      const current = node;
      const outgoing = edges.get(current.id);
      const step = { id: current.id, type: current.type, event: 'forward' };
      steps.push(step);
      if (current.type === 'sink') {
        outcome = 'delivered';
        step.event = outcome;
        node = null;
      } else if (current.type === 'policy') {
        latencyMs += 6;
        if (scenario.classification > current.classMax || prompt > current.promptMax) {
          outcome = 'blocked';
          step.event = scenario.classification > current.classMax ? 'policy_class' : 'policy_tokens';
          warnings.add(step.event);
          node = null;
        } else {
          step.event = 'policy_pass';
          jump(outgoing.get('default'));
        }
      } else if (current.type === 'router') {
        latencyMs += 4;
        const restricted = scenario.classification >= 3 ? outgoing.get('restricted') : null;
        const fallback = outgoing.get('fallback');
        backup = fallback && !usedBackups.has(current.id) ? { edge: fallback, prompt } : null;
        step.event = restricted ? 'restricted_route' : 'default_route';
        jump(restricted ?? outgoing.get('default'));
      } else if (current.type === 'retrieval') {
        latencyMs += 38;
        prompt += current.contextTokens;
        step.event = 'retrieved';
        step.tokens = current.contextTokens;
        jump(outgoing.get('default'));
      } else if (Object.hasOwn(PROFILES, current.type)) {
        if (current.type === 'remote' && scenario.classification >= 3) {
          outcome = 'blocked';
          step.event = 'restricted_remote';
          warnings.add(step.event);
          node = null;
          continue;
        }
        attempts++;
        if (scenario.fault === current.type) {
          latencyMs += 75;
          step.event = 'unavailable';
          warnings.add(step.event);
          if (backup) {
            const pending = backup;
            backup = null;
            usedBackups.add(pending.edge.from);
            prompt = pending.prompt;
            warnings.add('fallback_used');
            jump(pending.edge);
          } else {
            outcome = 'failed';
            node = null;
          }
          continue;
        }

        backup = null;
        const profile = PROFILES[current.type];
        if (current.type === 'remote') remote++;
        else local++;
        input += prompt;
        output += scenario.output;
        eur += (prompt * profile.input + scenario.output * profile.output) / 1000000;
        latencyMs += profile.fixedMs + Math.ceil((prompt + scenario.output) * 1000 / profile.tokensPerSecond);
        step.event = current.type === 'remote' ? 'model_remote' : 'model_local';
        step.tokens = prompt + scenario.output;
        jump(outgoing.get('default'));
      } else {
        jump(outgoing.get('default'));
      }
    }
    if (node) {
      outcome = 'failed';
      warnings.add('execution_limit');
    } else if (outcome === 'unrouted') warnings.add('unrouted');

    const scale = scenario.requests;
    return {
      version: VERSION, outcome,
      requests: scale, attempts: attempts * scale,
      delivered: outcome === 'delivered' ? scale : 0,
      blocked: outcome === 'blocked' ? scale : 0,
      failed: outcome === 'failed' || outcome === 'unrouted' ? scale : 0,
      local: local * scale, remote: remote * scale,
      input: input * scale, output: output * scale, tokens: (input + output) * scale,
      latencyMs, eur: Number((eur * scale).toFixed(6)),
      warnings: [...warnings], steps, followed,
    };
  }

  function snapshot(graph, scenario, timestamp) {
    validateGraph(graph);
    validateScenario(scenario);
    if (!integer(timestamp, 0, 8640000000000000)) fail('invalid_history');
    return { version: VERSION, timestamp, graph: JSON.parse(JSON.stringify(graph)), scenario: { ...scenario } };
  }

  function readHistory(raw) {
    if (typeof raw !== 'string' || raw.length > 150000) return [];
    let payload;
    try { payload = JSON.parse(raw); } catch { return []; }
    if (!shape(payload, ['version', 'entries']) || payload.version !== VERSION ||
        !Array.isArray(payload.entries) || payload.entries.length > MAX_HISTORY) return [];
    return payload.entries.flatMap(entry => {
      if (!shape(entry, ['version', 'timestamp', 'graph', 'scenario']) || entry.version !== VERSION) return [];
      try {
        const saved = snapshot(entry.graph, entry.scenario, entry.timestamp);
        run(saved.graph, saved.scenario);
        return [saved];
      } catch { return []; }
    });
  }

  return Object.freeze({ VERSION, MAX_NODES, MAX_EDGES, MAX_HISTORY, TYPES, ROUTES, FAULTS, PROFILES, DEFAULT_SCENARIO, FlowError, makeNode, template, validateGraph, validateScenario, run, snapshot, readHistory });
});
