/* SPDX-License-Identifier: MIT */
(() => {
  'use strict';

  const DAY = 86400000, HOUR = 3600000;
  const PERIODS = [7, 30, 90];
  const ACTORS = ['agent', 'user'];
  const STATUS = ['completed', 'failed', 'waiting'];
  const SUPPORTS = ['file', 'support', 'work'];
  const SOURCES = ['reported', 'workbench'];
  const startOfDay = time => Math.floor(time / DAY) * DAY;
  const integer = value => Number.isSafeInteger(value) && value >= 0;
  const text = (value, limit) => typeof value === 'string' && value.length > 0 && Array.from(value).length <= limit;
  const stamp = value => typeof value === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z$/.test(value) ? Date.parse(value) : NaN;

  function eventOf(row) {
    if (!row || !/^[0-9a-f]{32}$/.test(row.id || '') || !ACTORS.includes(row.actor_type) || !text(row.user_id, 256) ||
        (row.actor_type === 'user' ? row.actor_id !== row.user_id : !/^[a-z0-9][a-z0-9._-]{0,63}$/.test(row.actor_id || '')) ||
        (row.unit !== null && !text(row.unit, 128)) ||
        !text(row.action, 100) || !STATUS.includes(row.status) || !SOURCES.includes(row.source) ||
        !(row.duration_ms === null || integer(row.duration_ms)) || !Array.isArray(row.supports) || row.supports.length > 8) return null;
    const finished = stamp(row.finished_at), started = row.started_at === null ? null : stamp(row.started_at);
    if (!Number.isFinite(finished) || (started !== null && (!Number.isFinite(started) || started > finished))) return null;
    const tokens = row.tokens;
    if (tokens !== null && (!tokens || !['input', 'output', 'total'].every(key => tokens[key] === null || integer(tokens[key])))) return null;
    if (row.supports.some(item => !item || !SUPPORTS.includes(item.kind) || !text(item.label, 120) ||
        (item.ref !== null && (typeof item.ref !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(item.ref) || item.ref.includes('..'))))) return null;
    return { id: row.id, type: row.actor_type, actor: row.actor_id, user: row.user_id, unit: row.unit,
      action: row.action, started, finished, duration: row.duration_ms, status: row.status, tokens,
      supports: row.supports.map(item => ({ kind: item.kind, label: item.label, ref: item.ref ?? null })), source: row.source };
  }

  function parse(payload) {
    if (!payload || !Array.isArray(payload.events) || payload.events.length > 300 || typeof payload.truncated !== 'boolean' ||
        typeof payload.can_view_all !== 'boolean' || !/^[0-9a-f]{64}$/.test(payload.view || '')) return null;
    const since = stamp(payload.since), until = stamp(payload.until);
    if (!Number.isFinite(since) || !Number.isFinite(until) || since > until || until - since > 90 * DAY) return null;
    const events = payload.events.map(eventOf);
    if (events.some(row => !row || row.finished < since || row.finished > until) ||
        new Set(events.map(row => JSON.stringify([row.user, row.unit, row.id]))).size !== events.length) return null;
    return { events, since, until, truncated: payload.truncated, viewer: payload.view, canViewAll: payload.can_view_all };
  }

  function demo(days, now, language) {
    const anchor = startOfDay(now), since = anchor - (days - 1) * DAY;
    const fr = language === 'fr', events = [];
    const cast = [
      { type: 'agent', actor: fr ? 'Agent Qualité' : 'Quality Agent', user: 'Camille Laurent', unit: 'Delivery', action: fr ? 'Évaluer les preuves RAG' : 'Evaluate RAG evidence', label: 'rag-eval.csv' },
      { type: 'user', actor: 'Camille Laurent', user: 'Camille Laurent', unit: 'Delivery', action: fr ? 'Relire le dossier de décision' : 'Review the decision brief', label: fr ? 'Dossier de décision' : 'Decision brief' },
      { type: 'agent', actor: 'Agent Ops', user: 'Lucas Perrin', unit: 'R&D', action: fr ? 'Analyser la dérive du modèle' : 'Analyse model drift', label: 'model-drift.json' },
      { type: 'user', actor: 'Lucas Perrin', user: 'Lucas Perrin', unit: 'R&D', action: fr ? 'Consigner la revue de sécurité' : 'Record the security review', label: fr ? 'Note de revue' : 'Review note' },
    ];
    for (const [i, age] of [1, 2, 3, 4, 5, 6, 8, 11, 15, 18, 23, 27, 32, 39, 47, 55, 68, 81].entries()) {
      const day = anchor - age * DAY;
      if (day < since) continue;
      const subjects = i % 3 === 1 ? [2, 3] : [0, 1, ...(i % 2 === 0 ? [2] : [])];
      for (const n of subjects) {
        const actor = cast[n], finished = day + (10 + (i * 3 + n) % 8) * HOUR + n * 900000;
        const decision = n === 1 && i % 3 === 0;
        const duration = decision ? null : (24 + (i * 13 + n * 17) % 95) * 60000;
        const input = actor.type === 'agent' ? 1700 + (i * 2173 + n * 683) % 14200 : 0;
        const output = actor.type === 'agent' ? 410 + (i * 463 + n * 239) % 3920 : 0;
        events.push({ ...actor, id: `demo-${i}-${n}`, started: duration === null ? null : finished - duration,
          action: decision ? (fr ? 'Consigner la décision Workbench' : 'Record the Workbench decision') : actor.action,
          finished, duration, status: i % 7 === 4 && n === 2 ? 'failed' : i % 6 === 3 && n === 1 ? 'waiting' : 'completed',
          tokens: decision || (i % 5 === 2 && n === 0) ? null : { input, output, total: input + output }, source: decision ? 'workbench' : 'reported',
          supports: [{ kind: actor.type === 'agent' ? 'file' : 'work', label: actor.label, ref: `DEMO-${i + 11}` },
            ...(n === 0 ? [{ kind: 'support', label: fr ? 'Référentiel qualité' : 'Quality reference', ref: null }] : [])] });
      }
    }
    return { events, since, until: now, truncated: false, canViewAll: false };
  }

  const sum = values => values.reduce((total, value) => value === null || value === undefined ? total : (total ?? 0) + value, null);
  const subjectOf = (event, kind) => kind === 'agent' ? (event.type === 'agent' ? event.actor : null) : event.user;
  function covered(events, kind, from, to) {
    const groups = new Map();
    for (const row of events) {
      if (row.duration === null || row.started === null) continue;
      const start = Math.max(from, row.started), end = Math.min(to, row.finished);
      if (start >= end && !(row.started === row.finished && row.started >= from && row.started < to)) continue;
      const key = subjectOf(row, kind);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push([start, end]);
    }
    if (!groups.size) return null;
    let total = 0;
    for (const intervals of groups.values()) {
      intervals.sort((a, b) => a[0] - b[0]);
      let [start, end] = intervals[0];
      for (const [next, finish] of intervals.slice(1)) {
        if (next > end) { total += end - start; start = next; end = finish; }
        else end = Math.max(end, finish);
      }
      total += end - start;
    }
    return total;
  }

  function summary(payload, kind, subject) {
    const candidates = payload.events.filter(event => subjectOf(event, kind) !== null);
    const subjects = [...new Set(candidates.map(event => subjectOf(event, kind)))].sort((a, b) => a.localeCompare(b));
    const exact = subjects.includes(subject) ? subject : '';
    const events = candidates.filter(event => !exact || subjectOf(event, kind) === exact)
      .sort((a, b) => b.finished - a.finished || a.id.localeCompare(b.id));
    const days = [];
    for (let day = startOfDay(payload.since); day <= startOfDay(payload.until); day += DAY) {
      const from = Math.max(day, payload.since), to = Math.min(day + DAY, payload.until + 1);
      const ended = events.filter(row => row.finished >= from && row.finished < to);
      days.push({ day, duration: covered(events, kind, from, to), tokens: sum(ended.map(row => row.tokens?.total)),
        unknownTime: ended.some(row => row.duration === null || row.started === null), unknownTokens: ended.some(row => row.tokens?.total == null) });
    }
    return { events, subjects, exact, days, duration: sum(days.map(day => day.duration)), tokens: sum(days.map(day => day.tokens)),
      input: sum(events.map(row => row.tokens?.input)), output: sum(events.map(row => row.tokens?.output)),
      missingDuration: events.filter(row => row.duration === null || row.started === null).length,
      missingTokens: events.filter(row => row.tokens?.total == null).length };
  }

  function render(view, language, esc) {
    const fr = language === 'fr', tr = (en, french) => fr ? french : en, locale = fr ? 'fr-FR' : 'en-GB';
    const number = new Intl.NumberFormat(locale), date = new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'short', timeZone: 'UTC' });
    const time = new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' });
    const missing = tr('Unknown', 'Non renseigné');
    const amount = value => value === null || value === undefined ? missing : number.format(value);
    const elapsed = value => {
      if (value === null || value === undefined) return missing;
      if (value === 0) return '0 min';
      if (value < 1000) return tr('Under 1 s', 'Moins de 1 s');
      if (value < 60000) return `${Math.floor(value / 1000)} s`;
      const minutes = Math.round(value / 60000), h = Math.floor(minutes / 60), m = minutes % 60;
      return h ? `${h} h${m ? ` ${m} min` : ''}` : `${m} min`;
    };
    const options = (entries, current) => entries.map(([key, label]) => `<option value="${esc(key)}"${String(key) === String(current) ? ' selected' : ''}>${esc(label)}</option>`).join('');
    const select = (key, label, entries, current) => `<label><span>${esc(label)}</span><select data-activity-filter="${key}">${options(entries, current)}</select></label>`;
    const data = view.data ? summary(view.data, view.kind, view.subject) : null;
    const filters = `<div class="activity-filters">
      ${select('kind', tr('Group by', 'Regrouper par'), [['user', tr('User', 'Utilisateur')], ['agent', tr('Agent', 'Agent')]], view.kind)}
      ${select('subject', tr('Filter', 'Filtrer'), [['', view.kind === 'agent' ? tr('All agents', 'Tous les agents') : tr('All users', 'Tous les utilisateurs')], ...(data?.subjects || []).map(id => [id, id])], data?.exact || '')}
      ${select('days', tr('Period', 'Période'), PERIODS.map(days => [days, tr(`${days} days`, `${days} jours`)]), view.days)}
      ${view.source === 'api' && view.canViewAll ? select('scope', tr('Access', 'Périmètre'), [['own', tr('My activity', 'Mon activité')], ['all', tr('All authorized users', 'Tous les utilisateurs autorisés')]], view.scope) : ''}
    </div>`;
    const header = `<header class="activity-head"><div><p>${tr('EXECUTION LEDGER', 'JOURNAL D’EXÉCUTION')}</p><h3>${tr('Work history', 'Historique du travail')}</h3></div>
      <span class="activity-source">${view.source === 'demo' ? tr('Fictional demo', 'Démo fictive') : view.source === 'api' ? tr('Protected workspace', 'Espace protégé') : tr('Unavailable', 'Indisponible')}</span></header>`;
    const truncated = view.data?.truncated ? `<p class="activity-note activity-partial" role="status">${tr('Partial history. Totals cover the records returned for this period.', 'Historique partiel. Les totaux portent sur les activités renvoyées pour cette période.')}</p>` : '';
    const top = `<section class="activity" data-activity-state="${view.status}"${view.status === 'loading' ? ' aria-busy="true"' : ''}>${header}${filters}${truncated}`;
    if (!data) {
      const message = view.status === 'loading' ? tr('Loading the authorized history…', 'Chargement de l’historique autorisé…')
        : view.status === 'blocked' ? tr('Access to this history is unavailable.', 'L’accès à cet historique est indisponible.')
        : view.status === 'error' ? tr('The history could not be loaded.', 'L’historique n’a pas pu être chargé.')
        : tr('A protected workspace is required.', 'Un espace protégé est requis.');
      return `${top}<p class="activity-empty" role="status">${message}${view.source === 'api' && view.status !== 'loading' ? ` <button class="mini" type="button" data-activity-retry>${tr('Retry', 'Réessayer')}</button>` : ''}</p></section>`;
    }
    if (!data.events.length) return `${top}<p class="activity-empty" role="status">${tr('No recorded activity in this period.', 'Aucune activité enregistrée sur cette période.')}</p></section>`;
    const durationTitle = tr('Recorded time', 'Durée enregistrée');
    const tokensTitle = tr('Recorded tokens', 'Tokens renseignés');
    const durationMissing = tr(`${data.missingDuration} missing ${data.missingDuration === 1 ? 'duration' : 'durations'}`,
      `${data.missingDuration} ${data.missingDuration === 1 ? 'durée manquante' : 'durées manquantes'}`);
    const tokenMissing = tr(`${data.missingTokens} missing ${data.missingTokens === 1 ? 'total' : 'totals'}`,
      `${data.missingTokens} ${data.missingTokens === 1 ? 'total manquant' : 'totaux manquants'}`);
    const tile = (key, title, value, note) => `<div class="activity-kpi"><span>${title}</span><strong data-activity-total="${key}">${value}</strong><small>${note}</small></div>`;
    const cards = `<div class="activity-kpis">
      ${tile('duration', durationTitle, elapsed(data.duration), durationMissing)}
      ${tile('tokens', tokensTitle, amount(data.tokens), `${tr('In', 'Entrée')} ${amount(data.input)} · ${tr('Out', 'Sortie')} ${amount(data.output)}<br>${tokenMissing}`)}
      ${tile('events', tr('Actions', 'Actions'), number.format(data.events.length), tr('On the selected period', 'Sur la période sélectionnée'))}
    </div>`;
    const rows = data.days;
    const max = key => Math.max(0, ...rows.map(row => row[key] || 0));
    const track = (key, label, missingKey, format) => {
      const limit = max(key);
      return `<div class="activity-track-title"><b>${label}</b><span>${format(rows.some(row => row[key] !== null) ? limit : null)}</span></div>
        <div class="activity-track" data-track="${key}">${rows.map(row => {
          const value = row[key], size = value === null ? 0 : limit > 0 ? 100 * value / limit : 0;
          const style = value === null ? (row[missingKey] ? 'unknown' : 'none') : value === 0 ? 'zero' : 'bar';
          return `<span class="activity-tick ${style}" style="--bar:${size.toFixed(2)}%" title="${esc(date.format(row.day) + ' · ' + format(value))}"></span>`;
        }).join('')}</div>`;
    };
    const chart = `<figure class="activity-figure" aria-labelledby="activityAxisTitle">
      <figcaption><b id="activityAxisTitle">${tr('Time and tokens by day', 'Temps et tokens par jour')}</b><span>${tr('Shared UTC axis', 'Axe commun UTC')}</span></figcaption>
      <div class="activity-chart" style="--days:${rows.length}" aria-hidden="true">
      ${track('duration', durationTitle, 'unknownTime', elapsed)}${track('tokens', tokensTitle, 'unknownTokens', amount)}
      <div class="activity-dates">${[0, Math.round((rows.length - 1) / 2), rows.length - 1].map(i => `<span>${esc(date.format(rows[i].day))}</span>`).join('')}</div></div>
      <details class="activity-daily"><summary>${tr('Daily values', 'Valeurs par jour')}</summary><table>
        <thead><tr><th scope="col">${tr('Day UTC', 'Jour UTC')}</th><th scope="col">${durationTitle}</th><th scope="col">${tokensTitle}</th></tr></thead>
        <tbody>${rows.map(row => `<tr><th scope="row">${esc(date.format(row.day))}</th><td>${elapsed(row.duration)}</td><td>${amount(row.tokens)}</td></tr>`).join('')}</tbody>
      </table></details>
    </figure>`;
    const status = { completed: tr('Completed', 'Terminée'), failed: tr('Failed', 'Échec'), waiting: tr('Waiting', 'En attente') };
    const kinds = { file: tr('File', 'Fichier'), support: tr('Support', 'Support'), work: tr('Work', 'Travail') };
    const ledger = `<section class="activity-ledger" aria-label="${tr('Chronological history', 'Historique chronologique')}"><h4>${tr('Recorded actions', 'Actions enregistrées')}</h4><ol>${data.events.map(row => `<li class="activity-event">
      <time datetime="${new Date(row.finished).toISOString()}"><b>${esc(date.format(row.finished))}</b><small>${esc(time.format(row.finished))} UTC</small></time>
      <div class="activity-entry"><div class="activity-event-title"><b>${esc(row.action)}</b><span data-status="${row.status}">${status[row.status]}</span></div>
        <p>${esc(row.actor)}${row.unit ? ` · ${esc(row.unit)}` : ''}${row.type === 'agent' && row.user ? ` · ${esc(row.user)}` : ''} · ${row.source === 'workbench' ? 'Workbench' : tr('Reported', 'Déclaré')}</p>
        ${row.supports.length ? `<ul class="activity-supports" aria-label="${tr('Associated records', 'Supports associés')}">${row.supports.map(item => `<li><span>${kinds[item.kind]}</span> ${esc(item.label)}${item.ref ? ` <code>${esc(item.ref)}</code>` : ''}</li>`).join('')}</ul>` : ''}
      </div><div class="activity-values"><b>${elapsed(row.duration)}</b><span>${amount(row.tokens?.total)} ${row.tokens?.total == null ? '' : 'tokens'}</span><small>${tr('In', 'Entrée')} ${amount(row.tokens?.input)} · ${tr('Out', 'Sortie')} ${amount(row.tokens?.output)}</small></div>
    </li>`).join('')}</ol></section>`;
    const note = tr('Time covers recorded executions, with overlaps counted once per subject. Tokens are assigned to the end date. External activities are reported by their integrations.', 'Les durées couvrent les exécutions enregistrées, avec un seul comptage des chevauchements par sujet. Les tokens sont rattachés à la date de fin. Les activités externes sont déclarées par leurs intégrations.');
    return `${top}${cards}${chart}<p class="activity-note">${note}</p>${ledger}</section>`;
  }

  window.FactoryActivity = Object.freeze({ parse, demo, summary, render });
})();
