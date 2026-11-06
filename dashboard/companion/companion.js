/* SPDX-License-Identifier: MIT */
(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const copy = {
    en: {
      eyebrow: 'HUMAN DECISION DESK', title: 'Take a decision.',
      intro: 'Inspect what an agent proposes, ask a question and leave a reasoned decision.',
      queue: 'Decision inbox', chat: 'Agent conversation', cases: 'Cases', refresh: 'Refresh',
      proposal: 'Proposed action', evidence: 'Evidence', receipt: 'Execution receipt',
      decision: 'Human decision', rationale: 'Reason for the decision', approve: 'Approve',
      reject: 'Reject', request_changes: 'Request changes', feedback: 'Give feedback', comment: 'Comment (optional)',
      helpful: 'Helpful', needs_work: 'Needs work', advice: 'ADVISORY CHANNEL', talk: 'Talk to an agent',
      agent: 'Agent', operations: 'Operations', finance: 'Finance', governance: 'Governance',
      chatHelp: 'Ask for evidence or request a human review for a strategic or undocumented choice.',
      openReview: 'Open the human review', message: 'Your message', send: 'Send', factory: 'Factory',
      language: 'Language', static: 'Local simulation · fictitious cases · this tab only',
      demo: 'Connected simulation · shared cases · simulated ERP',
      protected_demo: 'Authenticated workspace · simulated ERP',
      loading: 'Connecting to the workspace…', unavailable: 'Workspace unavailable',
      noCases: 'No cases available.', noCase: 'No linked case', linkedCase: 'Linked case',
      action: 'Business action', strategic: 'Strategic choice', undocumented: 'Undocumented choice',
      revision: 'Revision', priority: 'Priority', waiting_review: 'Review required', applying: 'Applying',
      changes_requested: 'Changes requested', rejected: 'Rejected', executed: 'Executed', decided: 'Decided',
      withdrawn: 'Withdrawn by CRM', reconciliationRequired: 'ERP reconciliation required',
      reconciliationReadOnly: 'Check the existing ERP operation before taking another action.',
      readOnly: 'This decision is read-only for your current role or case state.',
      refreshRequired: 'The proposal changed. Refresh the case and review the new revision.',
      blocked: 'Sign in with an authorized identity and reopen this workspace.',
      requestError: 'The workspace could not complete this request. Try again.',
      recorded: 'Decision recorded.', feedbackRecorded: 'Feedback recorded.',
      ownMessage: 'You', local: 'Local model', simulation: 'Simulation', retry: 'Check or retry execution',
      expired: 'This proposal has expired. Request changes for a new review.',
      expires: 'Approval expires', decidedAt: 'Decision recorded',
      history: 'Decision history', openHistory: 'Show history', closeHistory: 'Hide history',
      noDecisions: 'No decisions have been recorded.', myFeedback: 'Your feedback', historyLoading: 'Loading history…',
      privacy: 'Questions sent for human review are shared with workspace reviewers. Use only information appropriate for this workspace.'
    },
    fr: {
      eyebrow: 'POSTE DE DÉCISION HUMAINE', title: 'Prenez la décision.',
      intro: 'Examinez la proposition de l’agent, posez vos questions et consignez une décision motivée.',
      queue: 'Boîte de décision', chat: 'Conversation avec un agent', cases: 'Dossiers', refresh: 'Actualiser',
      proposal: 'Action proposée', evidence: 'Éléments de preuve', receipt: 'Reçu d’exécution',
      decision: 'Décision humaine', rationale: 'Motif de la décision', approve: 'Approuver',
      reject: 'Refuser', request_changes: 'Demander une modification', feedback: 'Donner un avis', comment: 'Commentaire (facultatif)',
      helpful: 'Utile', needs_work: 'À améliorer', advice: 'CANAL DE CONSEIL', talk: 'Parler à un agent',
      agent: 'Agent', operations: 'Opérations', finance: 'Finance', governance: 'Gouvernance',
      chatHelp: 'Demandez des preuves ou une revue humaine pour un choix stratégique ou non documenté.',
      openReview: 'Ouvrir la revue humaine', message: 'Votre message', send: 'Envoyer', factory: 'Usine',
      language: 'Langue', static: 'Simulation locale · dossiers fictifs · cet onglet uniquement',
      demo: 'Simulation connectée · dossiers partagés · ERP simulé',
      protected_demo: 'Espace authentifié · ERP simulé',
      loading: 'Connexion à l’espace de travail…', unavailable: 'Espace de travail indisponible',
      noCases: 'Aucun dossier disponible.', noCase: 'Aucun dossier lié', linkedCase: 'Dossier lié',
      action: 'Action métier', strategic: 'Choix stratégique', undocumented: 'Choix non documenté',
      revision: 'Révision', priority: 'Priorité', waiting_review: 'Revue requise', applying: 'Exécution en cours',
      changes_requested: 'Modification demandée', rejected: 'Refusé', executed: 'Exécuté', decided: 'Décidé',
      withdrawn: 'Retiré par le CRM', reconciliationRequired: 'Rapprochement ERP requis',
      reconciliationReadOnly: 'Vérifiez l’opération existante dans l’ERP avant toute nouvelle action.',
      readOnly: 'Ce dossier est en lecture seule pour votre rôle ou son état actuel.',
      refreshRequired: 'La proposition a changé. Actualisez le dossier et examinez la nouvelle révision.',
      blocked: 'Connectez-vous avec une identité autorisée et ouvrez de nouveau cet espace.',
      requestError: 'La demande n’a pas abouti. Réessayez.',
      recorded: 'Décision enregistrée.', feedbackRecorded: 'Avis enregistré.',
      ownMessage: 'Vous', local: 'Modèle local', simulation: 'Simulation', retry: 'Vérifier ou relancer l’exécution',
      expired: 'Cette proposition a expiré. Demandez une modification pour ouvrir une nouvelle revue.',
      expires: 'Expiration de l’approbation', decidedAt: 'Décision consignée',
      history: 'Historique des décisions', openHistory: 'Afficher l’historique', closeHistory: 'Masquer l’historique',
      noDecisions: 'Aucune décision n’a été consignée.', myFeedback: 'Votre avis', historyLoading: 'Chargement de l’historique…',
      privacy: 'Les questions transmises en revue humaine sont partagées avec les personnes chargées des décisions. Saisissez uniquement des informations adaptées à cet espace.'
    }
  };
  const query = new URLSearchParams(location.search);
  const staticMode = location.protocol === 'file:' || document.documentElement.dataset.staticDemo === '1';
  const demo = staticMode ? window.createCompanionDemo() : null;
  let lang = query.get('lang') === 'fr' || (!query.has('lang') && navigator.language.startsWith('fr')) ? 'fr' : 'en';
  let mode = staticMode ? 'static' : 'loading';
  let cases = [];
  let selectedId = null;
  let capabilities = {};
  let escalationId = null;
  let listRevision = 0;
  let historyRevision = 0;
  let busy = false;
  const t = key => copy[lang][key] || key;
  const show = (id, visible) => { byId(id).hidden = !visible; };
  const setText = (id, value) => { byId(id).textContent = String(value ?? ''); };
  const current = () => cases.find(item => item.id === selectedId);
  const caseStatus = item => t(item.reconciliation_required === true ? 'reconciliationRequired' : item.status);

  function displayNotice(key, error = false) {
    byId('notice').dataset.error = String(error);
    setText('notice', t(key));
    show('notice', Boolean(key));
  }

  function displayConnection() {
    byId('connection').dataset.mode = mode;
    setText('connection', t(mode));
    const online = ['static', 'demo', 'protected_demo'].includes(mode);
    byId('chat-tab').disabled = !online;
    if (!online && !byId('chat-view').hidden) tab(false);
  }

  function renderList() {
    const list = byId('cases');
    list.replaceChildren();
    for (const item of cases) {
      const entry = document.createElement('li');
      const button = document.createElement('button');
      const meta = document.createElement('small');
      button.type = 'button';
      button.dataset.caseId = item.id;
      if (item.id === selectedId) button.setAttribute('aria-current', 'true');
      button.textContent = item.title;
      meta.textContent = `${t(item.kind)} · ${caseStatus(item)}`;
      button.append(meta);
      entry.append(button);
      list.append(entry);
    }
    setText('queue-count', cases.filter(item => ['waiting_review', 'applying'].includes(item.status)).length);
    show('empty', cases.length === 0);
    setText('empty', t(mode === 'loading' ? 'loading' : mode === 'unavailable' ? 'unavailable' : 'noCases'));
  }

  function formatted(value) {
    return typeof value === 'object' && value !== null ? JSON.stringify(value) : String(value ?? '—');
  }

  function formatTime(value) {
    const timestamp = value && Date.parse(value);
    return Number.isFinite(timestamp)
      ? new Intl.DateTimeFormat(lang, { dateStyle: 'medium', timeStyle: 'short' }).format(timestamp) : '';
  }

  function displayCase() {
    const item = current();
    show('case-detail', Boolean(item));
    setText('chat-context', item ? `${t('linkedCase')} · ${item.id}` : t('noCase'));
    if (!item) return;
    setText('case-label', `${item.id} · ${t(item.kind)}`);
    setText('case-title', item.title);
    setText('case-status', caseStatus(item));
    setText('case-revision', `${t('revision')} ${item.revision} · ${t('priority')} ${item.priority ?? '—'}`);
    setText('case-reason', item.reason);
    show('action', Boolean(item.action));
    setText('action-type', item.action?.type);
    const fields = byId('action-fields');
    fields.replaceChildren();
    for (const [key, value] of Object.entries(item.action?.fields ?? {})) {
      const label = document.createElement('dt');
      const field = document.createElement('dd');
      label.textContent = key;
      field.textContent = formatted(value);
      fields.append(label, field);
    }
    show('evidence', Boolean(item.evidence?.length));
    byId('evidence-items').replaceChildren(...(item.evidence ?? []).map(value => {
      const entry = document.createElement('li');
      entry.textContent = formatted(value);
      return entry;
    }));
    show('receipt', Boolean(item.receipt));
    setText('receipt-id', item.receipt?.id);
    setText('receipt-time', formatTime(item.receipt?.executed_at || item.receipt?.created_at));
    const decisionTime = formatTime(item.decided_at || item.decision_at);
    show('case-decision-time', Boolean(decisionTime));
    setText('case-decision-time', `${t('decidedAt')} · ${decisionTime}`);
    const allowed = capabilities.can_decide === true && item.can_decide !== false && item.reconciliation_required !== true &&
      ['waiting_review', 'applying'].includes(item.status);
    show('decision-form', allowed);
    show('decision-readonly', !allowed);
    setText('decision-readonly', t(item.reconciliation_required === true ? 'reconciliationReadOnly' : 'readOnly'));
    const applying = item.status === 'applying';
    const approve = byId('decision-form').querySelector('[value="approve"]');
    approve.textContent = t(applying ? 'retry' : 'approve');
    approve.disabled = !applying && item.approval_expired === true;
    byId('decision-form').querySelector('[value="reject"]').disabled = applying;
    byId('decision-form').querySelector('[value="request_changes"]').disabled = applying;
    const expires = formatTime(item.expires_at);
    show('approval-expiry', !applying && Boolean(expires || item.approval_expired));
    setText('approval-expiry', item.approval_expired ? t('expired') : `${t('expires')} · ${expires}`);
    const rationale = byId('rationale');
    rationale.readOnly = applying;
    if (applying) rationale.value = item.decision_rationale ?? '';
    if (applying && !rationale.value) approve.disabled = true;
  }

  function selectCase(id) {
    if (selectedId !== id) {
      selectedId = id;
      byId('rationale').value = '';
      byId('feedback-comment').value = '';
      closeHistory();
    }
    renderList();
    displayCase();
  }

  function closeHistory() {
    ++historyRevision;
    show('history-panel', false);
    byId('history-toggle').setAttribute('aria-expanded', 'false');
    setText('history-toggle', t('openHistory'));
    byId('history-items').replaceChildren();
    setText('history-feedback', '');
  }

  async function loadHistory() {
    const id = selectedId;
    if (!id) return;
    const version = ++historyRevision;
    show('history-panel', true);
    byId('history-toggle').setAttribute('aria-expanded', 'true');
    setText('history-toggle', t('closeHistory'));
    setText('history-status', t('historyLoading'));
    byId('history-items').replaceChildren();
    setText('history-feedback', '');
    try {
      const data = demo ? demo.history(id) : await request(`/cases/${encodeURIComponent(id)}/history`);
      if (id !== selectedId || version !== historyRevision) return;
      const decisions = data.decisions ?? [];
      setText('history-status', decisions.length ? '' : t('noDecisions'));
      for (const entry of decisions) {
        const item = document.createElement('li');
        const when = formatTime(entry.created_at);
        const title = document.createElement('strong');
        title.textContent = [t(entry.decision), `${t('revision')} ${entry.revision}`, when].filter(Boolean).join(' · ');
        item.append(title);
        if (entry.rationale) {
          const reason = document.createElement('p');
          reason.textContent = entry.rationale;
          item.append(reason);
        }
        byId('history-items').append(item);
      }
      const counts = data.feedback_counts ?? {};
      const own = data.my_feedback;
      const totals = `${t('helpful')} ${counts.helpful ?? 0} · ${t('needs_work')} ${counts.needs_work ?? 0}`;
      setText('history-feedback', own
        ? `${totals} · ${t('myFeedback')} ${t(own.rating)}${own.comment ? ` · ${own.comment}` : ''}`
        : totals);
    } catch {
      if (id === selectedId && version === historyRevision) setText('history-status', t('requestError'));
    }
  }

  function tab(chat) {
    byId('queue-tab').setAttribute('aria-pressed', String(!chat));
    byId('chat-tab').setAttribute('aria-pressed', String(chat));
    show('queue-view', !chat);
    show('chat-view', chat);
  }

  async function request(path, payload) {
    const response = await fetch(`/api/v1/workbench${path}`, {
      method: payload === undefined ? 'GET' : 'POST',
      credentials: 'same-origin', redirect: 'error', cache: 'no-store',
      headers: payload === undefined ? { Accept: 'application/json' } : {
        Accept: 'application/json', 'Content-Type': 'application/json'
      },
      body: payload === undefined ? undefined : JSON.stringify(payload),
      signal: AbortSignal.timeout(15000)
    });
    if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) {
      throw Object.assign(new Error('request failed'), { status: response.status });
    }
    return response.json();
  }

  function reportError(error) {
    displayNotice(error.status === 409 ? 'refreshRequired' :
      error.status === 401 || error.status === 403 ? 'blocked' : 'requestError', true);
  }

  async function loadCases() {
    const version = ++listRevision;
    byId('refresh').disabled = true;
    try {
      const result = demo ? demo.cases(lang) : await request(`/cases?lang=${lang}`);
      if (version !== listRevision) return;
      if (!Array.isArray(result.cases) || !['demo', 'protected_demo'].includes(result.mode)) throw new Error('bad response');
      mode = staticMode ? 'static' : result.mode;
      const previous = current();
      cases = [...result.cases].sort((a, b) => (b.priority ?? 0) - (a.priority ?? 0));
      capabilities = result.capabilities ?? {};
      const next = current();
      if (previous && next && (previous.revision !== next.revision || previous.proposal_hash !== next.proposal_hash)) {
        byId('rationale').value = '';
        displayNotice('refreshRequired', true);
      }
      selectCase(current()?.id ?? cases[0]?.id ?? null);
    } catch (error) {
      if (version !== listRevision) return;
      mode = 'unavailable';
      cases = [];
      capabilities = {};
      byId('messages').replaceChildren();
      byId('message').value = '';
      escalationId = null;
      show('escalation', false);
      selectCase(null);
      reportError(error);
    } finally {
      if (version === listRevision) {
        byId('refresh').disabled = false;
        displayConnection();
      }
    }
  }

  function translate() {
    document.documentElement.lang = lang;
    byId('language').value = lang;
    document.querySelector('label[for="language"]').textContent = t('language');
    for (const item of document.querySelectorAll('[data-i18n]')) item.textContent = t(item.dataset.i18n);
    if (!byId('history-panel').hidden) void loadHistory();
    setText('return-factory', t('factory'));
    byId('return-factory').href = `../index.html?lang=${lang}`;
    displayConnection();
    renderList();
    displayCase();
  }

  function appendMessage(author, text, source) {
    const row = document.createElement('li');
    const label = document.createElement('strong');
    const body = document.createElement('p');
    if (author === 'user') row.dataset.author = 'user';
    label.textContent = author === 'user' ? t('ownMessage') : `${t(author)} · ${t(source || 'simulation')}`;
    body.textContent = text;
    row.append(label, body);
    byId('messages').append(row);
    if (byId('messages').children.length > 60) byId('messages').firstElementChild.remove();
  }

  async function submit(event, perform) {
    event.preventDefault();
    if (busy) return;
    busy = true;
    event.submitter?.setAttribute('aria-busy', 'true');
    displayNotice('');
    try {
      await perform(event.submitter?.value);
    } catch (error) {
      reportError(error);
      if ([401, 403, 409].includes(error.status)) await loadCases();
    } finally {
      busy = false;
      event.submitter?.removeAttribute('aria-busy');
    }
  }

  byId('cases').addEventListener('click', event => {
    const button = event.target.closest('button[data-case-id]');
    if (!button) return;
    selectCase(button.dataset.caseId);
    const replacement = [...byId('cases').querySelectorAll('button')].find(node => node.dataset.caseId === selectedId);
    replacement?.focus();
  });
  byId('refresh').addEventListener('click', () => { displayNotice(''); void loadCases(); });
  byId('history-toggle').addEventListener('click', () => {
    if (byId('history-panel').hidden) void loadHistory();
    else closeHistory();
  });
  byId('queue-tab').addEventListener('click', () => tab(false));
  byId('chat-tab').addEventListener('click', () => tab(true));
  byId('language').addEventListener('change', event => {
    lang = event.target.value === 'fr' ? 'fr' : 'en';
    translate();
    void loadCases();
  });
  byId('decision-form').addEventListener('submit', event => submit(event, async decision => {
    const item = current();
    const rationale = (item?.status === 'applying' ? item.decision_rationale ?? '' : byId('rationale').value).trim();
    if (!item || !rationale || !['approve', 'request_changes', 'reject'].includes(decision)) return;
    const input = { decision, revision: item.revision, proposal_hash: item.proposal_hash, rationale };
    const updated = demo ? demo.decide(item.id, input, lang) :
      await request(`/cases/${encodeURIComponent(item.id)}/decisions`, input);
    ++listRevision;
    byId('refresh').disabled = false;
    if (current()?.id === item.id) byId('rationale').value = '';
    cases = cases.map(previous => previous.id === item.id ? updated : previous);
    renderList();
    displayCase();
    displayNotice('recorded');
    if (current()?.id === item.id && !byId('history-panel').hidden) void loadHistory();
  }));
  byId('feedback-form').addEventListener('submit', event => submit(event, async rating => {
    const item = current();
    if (!item || !['helpful', 'needs_work'].includes(rating)) return;
    const input = { rating, comment: byId('feedback-comment').value.trim() };
    if (demo) demo.feedback(item.id, input);
    else await request(`/cases/${encodeURIComponent(item.id)}/feedback`, input);
    if (current()?.id === item.id) byId('feedback-comment').value = '';
    displayNotice('feedbackRecorded');
    if (current()?.id === item.id && !byId('history-panel').hidden) void loadHistory();
  }));
  byId('chat-form').addEventListener('submit', event => submit(event, async () => {
    const field = byId('message');
    const message = field.value.trim();
    if (!message) return;
    const agent = byId('agent').value;
    const input = { agent, message };
    if (current()) input.case_id = current().id;
    const response = demo ? demo.chat(input, lang) : await request(`/chat?lang=${lang}`, input);
    if (field.value.trim() === message) field.value = '';
    appendMessage('user', message);
    appendMessage(agent, response.answer, response.source);
    escalationId = response.escalated && response.case_id ? response.case_id : null;
    show('escalation', Boolean(escalationId));
    if (escalationId) await loadCases();
  }));
  byId('escalation').addEventListener('click', () => {
    if (!cases.some(item => item.id === escalationId)) return;
    selectCase(escalationId);
    tab(false);
    byId('case-title').setAttribute('tabindex', '-1');
    byId('case-title').focus();
  });

  translate();
  void loadCases();
})();
