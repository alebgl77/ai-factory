/* SPDX-License-Identifier: MIT */
(() => {
  'use strict';

  const MAX_CELLS = 32000, SAMPLE_STEP = .08, PERSONAL_SPACE = .8;
  const WALK_SPEED = 2.1, REQUEST_TIME = 1.2, REPLY_TIME = 1.4, TEXT_LIMIT = 280;
  const TAU = 2 * Math.PI;
  const MODES = new Set(['bu-human', 'agent-agent', 'agent-human']);
  const NEIGHBORS = [[1, 0], [0, 1], [-1, 0], [0, -1]];

  const point = value => Array.isArray(value) && value.length === 2 &&
    Number.isFinite(value[0]) && Number.isFinite(value[1]);
  const distance = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1]);
  const yawTo = (a, b) => Math.atan2(a[0] - b[0], a[1] - b[1]);
  const lerp = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];

  function clearSegment(a, b, blocked) {
    const samples = Math.max(1, Math.ceil(distance(a, b) / SAMPLE_STEP));
    for (let i = 0; i <= samples; i++) {
      if (blocked(...lerp(a, b, i / samples))) return false;
    }
    return true;
  }

  function push(heap, item) {
    let at = heap.length;
    heap.push(item);
    while (at > 0) {
      const parent = (at - 1) >> 1;
      if (heap[parent].score <= item.score) break;
      heap[at] = heap[parent];
      at = parent;
    }
    heap[at] = item;
  }

  function pop(heap) {
    const first = heap[0], last = heap.pop();
    if (!heap.length) return first;
    let at = 0;
    while (at * 2 + 1 < heap.length) {
      let child = at * 2 + 1;
      if (child + 1 < heap.length && heap[child + 1].score < heap[child].score) child++;
      if (last.score <= heap[child].score) break;
      heap[at] = heap[child];
      at = child;
    }
    heap[at] = last;
    return first;
  }

  function smooth(path, blocked) {
    const result = [path[0]];
    let i = 0;
    while (i < path.length - 1) {
      let next = Math.min(i + 24, path.length - 1);
      while (next > i + 1 && !clearSegment(path[i], path[next], blocked)) next--;
      result.push(path[next]);
      i = next;
    }
    return result;
  }

  function planRoute(start, end, options) {
    const { blocked, bounds, step = .2 } = options || {};
    if (!point(start) || !point(end) || typeof blocked !== 'function' ||
        !Array.isArray(bounds) || bounds.length !== 4 || !bounds.every(Number.isFinite) ||
        !Number.isFinite(step) || step < .1 || step > .5) return null;
    const [minX, maxX, minZ, maxZ] = bounds;
    const width = Math.floor((maxX - minX) / step) + 1;
    const height = Math.floor((maxZ - minZ) / step) + 1;
    if (width < 1 || height < 1 || width * height > MAX_CELLS) return null;
    const inside = ([x, z]) => x >= minX && x <= maxX && z >= minZ && z <= maxZ;
    if (!inside(start) || !inside(end) || blocked(...start) || blocked(...end)) return null;
    if (clearSegment(start, end, blocked)) return distance(start, end) ? [start.slice(), end.slice()] : [start.slice()];

    const count = width * height;
    const best = new Float64Array(count).fill(Infinity), previous = new Int32Array(count).fill(-1);
    const cells = new Uint8Array(count), heap = [];
    const coords = id => [minX + (id % width) * step, minZ + Math.floor(id / width) * step];
    function isFree(id) {
      if (!cells[id]) cells[id] = blocked(...coords(id)) ? 2 : 1;
      return cells[id] === 1;
    }
    function connectors(p) {
      const x = Math.round((p[0] - minX) / step), z = Math.round((p[1] - minZ) / step);
      const result = [];
      for (let row = Math.max(0, z - 2); row <= Math.min(height - 1, z + 2); row++) {
        for (let col = Math.max(0, x - 2); col <= Math.min(width - 1, x + 2); col++) {
          const id = row * width + col;
          if (isFree(id) && clearSegment(p, coords(id), blocked)) result.push(id);
        }
      }
      return result;
    }
    const goals = new Set(connectors(end));
    if (!goals.size) return null;
    for (const id of connectors(start)) {
      const p = coords(id), cost = distance(start, p);
      best[id] = cost;
      push(heap, { id, cost, score: cost + distance(p, end) });
    }
    while (heap.length) {
      const { id, cost } = pop(heap);
      if (cost > best[id]) continue;
      if (goals.has(id)) {
        const route = [end.slice()];
        for (let current = id; current >= 0; current = previous[current]) {
          const p = coords(current);
          if (distance(p, route.at(-1)) > 1e-8) route.push(p);
        }
        if (distance(start, route.at(-1)) > 1e-8) route.push(start.slice());
        return smooth(route.reverse(), blocked);
      }
      const p = coords(id), x = id % width, z = Math.floor(id / width);
      for (const [dx, dz] of NEIGHBORS) {
        const nx = x + dx, nz = z + dz;
        if (nx < 0 || nx >= width || nz < 0 || nz >= height) continue;
        const next = nz * width + nx, nextCost = cost + step;
        if (nextCost + 1e-8 >= best[next] || !isFree(next)) continue;
        const q = coords(next);
        if (!clearSegment(p, q, blocked)) continue;
        previous[next] = id;
        best[next] = nextCost;
        push(heap, { id: next, cost: nextCost, score: nextCost + distance(q, end) });
      }
    }
    return null;
  }

  // Keep the actor outside the receiver's personal space, even when the route curves near arrival.
  function stopBefore(route, receiver) {
    for (let i = route.length - 2; i >= 0; i--) {
      const a = route[i], b = route[i + 1];
      if (distance(a, receiver) < PERSONAL_SPACE) continue;
      const dx = b[0] - a[0], dz = b[1] - a[1];
      const x = a[0] - receiver[0], z = a[1] - receiver[1];
      const square = dx * dx + dz * dz, dot = x * dx + z * dz;
      const t = Math.max(0, Math.min(1, (-dot - Math.sqrt(Math.max(0,
        dot * dot - square * (x * x + z * z - PERSONAL_SPACE * PERSONAL_SPACE)))) / square));
      const stop = lerp(a, b, t), trimmed = route.slice(0, i + 1);
      if (distance(a, stop) > 1e-8) trimmed.push(stop);
      return trimmed.length > 1 ? trimmed : null;
    }
    return null;
  }

  function cleanText(value) {
    const text = typeof value === 'string' ? value.trim() : '';
    return text.length <= TEXT_LIMIT ? text : '';
  }
  const actor = (position, yaw, pose, phase = 0) => Object.freeze({ x: position[0], z: position[1], yaw, pose, phase });

  function createController() {
    let state = null, route = null, segment = 0, covered = 0, phase = 0, elapsed = 0;
    let reduced = false, receiver, replyForAgent, origin;
    const active = () => !!route;
    function set(next) { state = Object.freeze({ ...state, ...next }); }

    function start(options) {
      const { mode, from, to, question, simulatedReply, blocked, bounds, reducedMotion = false } = options || {};
      const message = cleanText(question), answer = cleanText(simulatedReply);
      if (active() || !MODES.has(mode) || !message || (mode === 'agent-agent' && !answer) ||
          !point(from) || !point(to) || distance(from, to) <= PERSONAL_SPACE) return false;
      let planned;
      try { planned = planRoute(from, to, { blocked, bounds }); }
      catch { return false; } // A failed scene collision query must not start a partially valid encounter.
      const safe = planned && stopBefore(planned, to);
      if (!safe) return false;
      route = safe;
      origin = from.slice(); receiver = to.slice(); replyForAgent = mode === 'agent-agent' ? answer : '';
      reduced = reducedMotion === true;
      segment = 0; covered = 0; phase = 0; elapsed = 0;
      state = Object.freeze({ mode, stage: 'walking', question: message, reply: '',
        actor: actor(origin, yawTo(route[0], route[1]), reduced ? 'stand' : 'walk'),
        target: actor(receiver, yawTo(receiver, route.at(-1)), 'stand') });
      return true;
    }

    function conversation(stage, reply = state.reply) {
      const end = route.at(-1), answering = stage === 'replying', asking = stage === 'requesting';
      set({ stage, reply,
        actor: actor(end, yawTo(end, receiver), reduced ? 'stand' : asking ? 'request' : 'listen'),
        target: actor(receiver, yawTo(receiver, end), reduced ? 'stand' : answering ? 'reply' : 'listen') });
      elapsed = 0;
    }

    function respond(text) {
      const reply = cleanText(text);
      if (state?.stage !== 'waiting' || !reply) return false;
      conversation('replying', reply);
      return true;
    }

    function cancel() {
      if (!active()) return false;
      set({ stage: 'canceled', actor: actor(origin, state.actor.yaw, 'stand'),
        target: actor(receiver, state.target.yaw, 'stand') });
      route = null;
      return true;
    }

    function move(dt) {
      let remaining = reduced ? Infinity : WALK_SPEED * dt;
      while (segment < route.length - 1) {
        const length = distance(route[segment], route[segment + 1]);
        const delta = Math.min(length - covered, remaining);
        phase = (phase + delta * 4.8) % TAU;
        remaining -= delta;
        covered += delta;
        if (length - covered > 1e-8) break;
        segment++;
        covered = 0;
      }
      if (segment === route.length - 1) {
        if (state.stage === 'walking') conversation('requesting');
        else {
          set({ stage: 'complete', actor: actor(origin, state.actor.yaw, 'stand') });
          route = null;
        }
      } else {
        const a = route[segment], b = route[segment + 1];
        set({ actor: actor(lerp(a, b, covered / distance(a, b)), yawTo(a, b), 'walk', phase) });
      }
    }

    function update(dtSeconds) {
      if (!active() || !Number.isFinite(dtSeconds) || dtSeconds <= 0) return state;
      const dt = Math.min(dtSeconds, .25);
      if (state.stage === 'walking' || state.stage === 'returning') move(dt);
      else if (state.stage === 'requesting' || state.stage === 'replying') {
        elapsed += dt;
        if (state.stage === 'requesting' && elapsed >= REQUEST_TIME) {
          if (state.mode === 'agent-agent') conversation('replying', replyForAgent);
          else conversation('waiting');
        } else if (state.stage === 'replying' && elapsed >= REPLY_TIME) {
          route.reverse(); segment = 0; covered = 0; phase = 0;
          set({ stage: 'returning', actor: actor(route[0], yawTo(route[0], route[1]), reduced ? 'stand' : 'walk'),
            target: actor(receiver, state.target.yaw, 'stand') });
        } else if (!reduced) {
          const gesturing = state.stage === 'requesting' ? 'actor' : 'target';
          const person = state[gesturing];
          set({ [gesturing]: actor([person.x, person.z], person.yaw, person.pose, elapsed * 5) });
        }
      }
      return state;
    }

    return { start, update, respond, cancel, get state() { return state; } };
  }

  window.FactoryEncounters = Object.freeze({ planRoute, createController });
})();
