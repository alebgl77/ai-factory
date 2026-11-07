/* SPDX-License-Identifier: MIT */
(() => {
  'use strict';

  const statuses = new Set(['waiting', 'approved', 'rejected', 'changes_requested']);
  const maxEncounters = 100;
  const maxSeen = 512;
  const pair = event => `${event.case_id}:${event.revision}`;
  const hasKeys = (value, keys) => value !== null && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));

  function parse(body) {
    if (!hasKeys(body, ['encounters']) || !Array.isArray(body.encounters)
        || body.encounters.length > maxEncounters) return null;
    const encounters = new Map();
    for (const row of body.encounters) {
      if (!hasKeys(row, ['case_id', 'revision', 'status'])
          || typeof row.case_id !== 'string' || !/^[0-9a-f]{32}$/.test(row.case_id)
          || !Number.isSafeInteger(row.revision) || row.revision < 1 || !statuses.has(row.status)) return null;
      const previous = encounters.get(row.case_id);
      if (previous && (previous.revision !== row.revision || previous.status !== row.status)) return null;
      if (!previous) encounters.set(row.case_id, Object.freeze({
        case_id: row.case_id, revision: row.revision, status: row.status,
      }));
    }
    return encounters;
  }

  function create({ read, onStart, onDecision, onClear, intervalMs = 5000 }) {
    const interval = Number.isFinite(intervalMs) ? Math.min(60000, Math.max(1000, intervalMs)) : 5000;
    const seen = new Set();
    let enabled = false;
    let active = null;
    let generation = 0;
    let pending = null;
    let lastPoll = null;

    function start() {
      if (enabled) return;
      generation++;
      enabled = true;
      lastPoll = null;
      seen.clear();
    }

    function stop(reason = 'stopped') {
      if (!enabled) return;
      enabled = false;
      generation++;
      active = null;
      const current = pending;
      pending = null;
      current?.controller.abort();
      onClear(reason);
    }

    function update(encounters) {
      if (active) {
        const current = encounters.get(active.case_id);
        if (!current || current.revision !== active.revision
            || (active.status !== 'waiting' && current.status !== active.status)) {
          active = null;
          onClear('stale');
        } else if (active.status === 'waiting' && current.status !== 'waiting') {
          active = current;
          onDecision(current);
        }
        return;
      }
      const next = [...encounters.values()].find(row => row.status === 'waiting' && !seen.has(pair(row)));
      if (!next) return;
      // Do not evict a replay guard while following is enabled. A new session is explicit.
      if (seen.size >= maxSeen) { stop('limit'); return; }
      seen.add(pair(next));
      active = next;
      if (onStart(next) === false && active === next) {
        active = null;
        onClear('unreachable');
      }
    }

    function isCurrent(job) {
      return enabled && pending === job && job.generation === generation;
    }

    async function poll(job) {
      try {
        const response = await read({ signal: job.controller.signal, caseId: job.caseId });
        if (!isCurrent(job)) return;
        if (response?.status !== 200) {
          stop(response?.status === 401 || response?.status === 403 ? 'unauthorized' : 'offline');
          return;
        }
        const encounters = parse(response.body);
        if (!encounters) { stop('malformed'); return; }
        update(encounters);
      } catch {
        if (isCurrent(job)) stop('offline');
      } finally {
        if (pending === job) pending = null;
      }
    }

    function tick(nowMs) {
      if (!enabled || pending || !Number.isFinite(nowMs)
          || (lastPoll !== null && nowMs - lastPoll < interval)) return;
      lastPoll = nowMs;
      pending = { generation, controller: new AbortController(), caseId: active?.case_id || null };
      return poll(pending);
    }

    function done() {
      active = null;
      if (pending?.caseId) {
        const current = pending;
        pending = null;
        current.controller.abort();
      }
    }
    return { start, tick, stop, done,
      get enabled() { return enabled; }, get active() { return active; } };
  }

  window.FactoryWorkbenchFollow = Object.freeze({ create });
})();
