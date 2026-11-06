/* SPDX-License-Identifier: MIT */
(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const copy = {
    en: {
      eyebrow: 'HUMAN OVERSIGHT', title: 'Decision desk',
      intro: 'Review agent proposals, ask questions and record your decisions.',
      queue: 'Decision inbox', chat: 'Agent conversation', cases: 'Cases', refresh: 'Refresh',
      back: 'Back to cases', workspace: 'Workspace',
      proposal: 'Proposed action', evidence: 'Evidence', receipt: 'Execution receipt',
      decision: 'Human decision', rationale: 'Reason for the decision', approve: 'Approve',
      reject: 'Reject', request_changes: 'Request changes', feedback: 'Give feedback', comment: 'Comment (optional)',
      helpful: 'Helpful', needs_work: 'Needs work', advice: 'ADVISORY CHANNEL', talk: 'Talk to an agent',
      agent: 'Agent', operations: 'Operations', finance: 'Finance', governance: 'Governance',
      chatHelp: 'Ask for evidence or request a human review for a strategic or undocumented choice.',
      chatEmpty: 'Start with a question about the proposal.',
      openReview: 'Open the human review', message: 'Your message', send: 'Send', factory: 'Factory',
      language: 'Language', static: 'Local simulation · fictitious cases · this tab only',
      demo: 'Connected simulation · shared cases · simulated ERP',
      protected_demo: 'Authenticated workspace · simulated ERP',
      loading: 'Connecting to the workspace…', unavailable: 'Workspace unavailable',
      noCases: 'No cases available.', noCase: 'No linked case', linkedCase: 'Linked case',
      action: 'Business action', strategic: 'Strategic choice', undocumented: 'Undocumented choice',
      revision: 'Revision', priority: 'Priority', waiting_review: 'Review required', applying: 'Applying',
      changes_requested: 'Changes requested', rejected: 'Rejected', executed: 'Executed', decided: 'Decided',
      readOnly: 'This decision is read-only for your current role or case state.',
      refreshRequired: 'The proposal changed. Refresh the case and review the new revision.',
      proposalUpdated: 'The proposal changed. Review the new revision before deciding.',
      blocked: 'Sign in with an authorized identity and reopen this workspace.',
      requestError: 'The workspace could not complete this request. Try again.',
      reasonRequired: 'Enter a reason for the decision.', messageRequired: 'Enter a message.',
      recorded: 'Decision recorded.', feedbackRecorded: 'Feedback recorded.',
      ownMessage: 'You', local: 'Local model', simulation: 'Simulation', retry: 'Check or retry execution',
      expired: 'This proposal has expired. Request changes for a new review.',
      expires: 'Approval expires', decidedAt: 'Decision recorded',
      history: 'Decision history', openHistory: 'Show history', closeHistory: 'Hide history',
      noDecisions: 'No decisions have been recorded.', myFeedback: 'Your feedback', historyLoading: 'Loading history…',
      privacy: 'Questions sent for human review are shared with workspace reviewers. Use only information appropriate for this workspace.'
    },
    fr: {
      eyebrow: 'SUPERVISION HUMAINE', title: 'Bureau de décision',
      intro: 'Examinez les propositions des agents, posez vos questions et consignez vos décisions.',
      queue: 'Dossiers à examiner', chat: 'Conversation avec un agent', cases: 'Dossiers', refresh: 'Actualiser',
      back: 'Retour aux dossiers', workspace: 'Espace de travail',
      proposal: 'Action proposée', evidence: 'Éléments de preuve', receipt: 'Reçu d’exécution',
      decision: 'Décision humaine', rationale: 'Motif de la décision', approve: 'Approuver',
      reject: 'Refuser', request_changes: 'Demander une modification', feedback: 'Donner un avis', comment: 'Commentaire (facultatif)',
      helpful: 'Utile', needs_work: 'À améliorer', advice: 'CANAL DE CONSEIL', talk: 'Parler à un agent',
      agent: 'Agent', operations: 'Opérations', finance: 'Finance', governance: 'Gouvernance',
      chatHelp: 'Demandez des preuves ou une revue humaine pour un choix stratégique ou non documenté.',
      chatEmpty: 'Commencez par une question sur la proposition.',
      openReview: 'Ouvrir la revue humaine', message: 'Votre message', send: 'Envoyer', factory: 'Usine',
      language: 'Langue', static: 'Simulation locale · dossiers fictifs · cet onglet uniquement',
      demo: 'Simulation connectée · dossiers partagés · ERP simulé',
      protected_demo: 'Espace authentifié · ERP simulé',
      loading: 'Connexion à l’espace de travail…', unavailable: 'Espace de travail indisponible',
      noCases: 'Aucun dossier disponible.', noCase: 'Aucun dossier lié', linkedCase: 'Dossier lié',
      action: 'Action métier', strategic: 'Choix stratégique', undocumented: 'Choix non documenté',
      revision: 'Révision', priority: 'Priorité', waiting_review: 'Revue requise', applying: 'Exécution en cours',
      changes_requested: 'Modification demandée', rejected: 'Refusé', executed: 'Exécuté', decided: 'Décidé',
      readOnly: 'Ce dossier est en lecture seule pour votre rôle ou son état actuel.',
      refreshRequired: 'La proposition a changé. Actualisez le dossier et examinez la nouvelle révision.',
      proposalUpdated: 'La proposition a changé. Examinez la nouvelle révision avant de décider.',
      blocked: 'Connectez-vous avec une identité autorisée et ouvrez de nouveau cet espace.',
      requestError: 'La demande n’a pas abouti. Réessayez.',
      reasonRequired: 'Saisissez le motif de votre décision.', messageRequired: 'Saisissez un message.',
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
  const narrow = matchMedia('(max-width: 43.999rem)');
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

  function displayNotice(id, key = '', error = false, caseId = '') {
    const notice = byId(id);
    notice.dataset.key = key;
    notice.dataset.error = String(error);
    notice.dataset.caseId = caseId;
    renderNotice(notice);
  }

  function renderNotice(notice) {
    const { key, caseId } = notice.dataset;
    notice.textContent = key && caseId ? `${caseId} · ${t(key)}` : t(key);
  }

  function setRefreshing(refreshing) {
    for (const id of ['refresh', 'refresh-case']) byId(id).setAttribute('aria-disabled', String(refreshing));
  }

  function focusSelectedCase() {
    const selected = byId('cases').querySelector('[aria-current="true"]');
    (selected || byId('refresh')).focus();
  }

  function showInbox(moveFocus = false) {
    byId('queue-view').dataset.screen = 'inbox';
    if (moveFocus) focusSelectedCase();
  }

  function openCase(id, fromChat = false) {
    selectCase(id);
    byId('queue-view').dataset.screen = 'detail';
    if (fromChat) tab(false);
    if (narrow.matches || fromChat) {
      const title = byId('case-title');
      title.focus({ preventScroll: true });
      const anchor = narrow.matches ? byId('back-to-cases') : title;
      anchor.scrollIntoView({ block: 'nearest' });
    } else focusSelectedCase();
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
      const heading = document.createElement('span');
      const meta = document.createElement('small');
      const status = document.createElement('span');
      button.type = 'button';
      button.dataset.caseId = item.id;
      if (item.id === selectedId) button.setAttribute('aria-current', 'true');
      heading.className = 'case-list-title';
      heading.textContent = item.title;
      meta.textContent = `${item.id} · ${t(item.kind)}`;
      status.className = 'case-list-status';
      status.dataset.status = item.status;
      status.textContent = t(item.status);
      button.append(meta, heading, status);
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
    if (!item) showInbox();
    setText('chat-context', item ? `${t('linkedCase')} · ${item.id}` : t('noCase'));
    if (!item) return;
    setText('case-label', `${item.id} · ${t(item.kind)}`);
    setText('case-title', item.title);
    setText('case-status', t(item.status));
    byId('case-status').dataset.status = item.status;
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
    const allowed = capabilities.can_decide === true && item.can_decide !== false &&
      ['waiting_review', 'applying'].includes(item.status);
    show('decision-form', allowed);
    show('decision-readonly', !allowed);
    setText('decision-readonly', t('readOnly'));
    const applying = item.status === 'applying';
    const approve = byId('decision-form').querySelector('[value="approve"]');
    approve.textContent = t(applying ? 'retry' : 'approve');
    approve.disabled = !applying && item.approval_expired === true;
    byId('decision-form').querySelector('[value="reject"]').disabled = applying;
    byId('decision-form').querySelector('[value="request_changes"]').disabled = applying;
    const expires = formatTime(item.expires_at);
    show('approval-expiry', !applying && Boolean(expires || item.approval_expired));
    byId('approval-expiry').dataset.expired = String(item.approval_expired === true);
    setText('approval-expiry', item.approval_expired ? t('expired') : `${t('expires')} · ${expires}`);
    const rationale = byId('rationale');
    rationale.readOnly = applying;
    if (applying) rationale.value = item.decision_rationale ?? '';
    if (applying && !rationale.value) approve.disabled = true;
  }

  function selectCase(id) {
    if (selectedId !== id) {
      selectedId = id;
      for (const notice of ['case-notice', 'decision-notice', 'feedback-notice']) displayNotice(notice);
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

  function reportError(error, noticeId, caseId = '') {
    displayNotice(noticeId, error.status === 409 ? 'refreshRequired' :
      error.status === 401 || error.status === 403 ? 'blocked' : 'requestError', true, caseId);
  }

  async function loadCases() {
    const version = ++listRevision;
    setRefreshing(true);
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
        for (const id of ['decision-notice', 'feedback-notice']) {
          if (byId(id).dataset.key === 'refreshRequired') displayNotice(id);
        }
        const workspaceNotice = byId('workspace-notice');
        if (workspaceNotice.dataset.key === 'refreshRequired' && workspaceNotice.dataset.caseId === next.id) {
          displayNotice('workspace-notice');
        }
        displayNotice('case-notice', 'proposalUpdated', true);
        if (!byId('case-detail').getClientRects().length) {
          displayNotice('workspace-notice', 'proposalUpdated', true, next.id);
        }
      }
      const lostDetail = previous && !next && byId('case-detail').contains(document.activeElement);
      selectCase(current()?.id ?? cases[0]?.id ?? null);
      if (previous && !next) showInbox(lostDetail);
    } catch (error) {
      if (version !== listRevision) return;
      mode = 'unavailable';
      cases = [];
      capabilities = {};
      byId('messages').replaceChildren();
      show('chat-empty', true);
      byId('message').value = '';
      escalationId = null;
      show('escalation', false);
      const lostFocus = ['chat-view', 'case-detail'].some(id => byId(id).contains(document.activeElement));
      selectCase(null);
      if (!byId('chat-view').hidden) tab(false);
      if (lostFocus) focusSelectedCase();
      reportError(error, 'inbox-notice');
    } finally {
      if (version === listRevision) {
        setRefreshing(false);
        displayConnection();
      }
    }
  }

  function translate() {
    document.documentElement.lang = lang;
    byId('language').value = lang;
    for (const item of document.querySelectorAll('[data-i18n]')) item.textContent = t(item.dataset.i18n);
    for (const notice of document.querySelectorAll('.notice[data-key]')) renderNotice(notice);
    if (!byId('history-panel').hidden) void loadHistory();
    byId('workspace-nav').setAttribute('aria-label', t('workspace'));
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
    show('chat-empty', false);
    byId('messages').append(row);
    if (byId('messages').children.length > 60) byId('messages').firstElementChild.remove();
  }

  async function submit(event, noticeId, perform) {
    event.preventDefault();
    if (busy) return;
    busy = true;
    event.submitter?.setAttribute('aria-busy', 'true');
    const caseId = selectedId;
    displayNotice(noticeId);
    displayNotice('workspace-notice');
    try {
      await perform(event.submitter?.value);
    } catch (error) {
      const originVisible = (noticeId === 'chat-notice' || selectedId === caseId) && byId(noticeId).getClientRects().length;
      reportError(error, originVisible ? noticeId : 'workspace-notice', originVisible ? '' : caseId);
      if ([401, 403, 409].includes(error.status)) await loadCases();
    } finally {
      busy = false;
      event.submitter?.removeAttribute('aria-busy');
    }
  }

  byId('cases').addEventListener('click', event => {
    const button = event.target.closest('button[data-case-id]');
    if (button) openCase(button.dataset.caseId);
  });
  byId('back-to-cases').addEventListener('click', () => showInbox(true));
  for (const [button, notice] of [['refresh', 'inbox-notice'], ['refresh-case', 'case-notice']]) {
    byId(button).addEventListener('click', () => {
      if (byId(button).getAttribute('aria-disabled') === 'true') return;
      displayNotice(notice);
      displayNotice('workspace-notice');
      void loadCases();
    });
  }
  byId('queue-view').addEventListener('focusin', event => {
    if (!narrow.matches) {
      byId('queue-view').dataset.screen = byId('case-detail').contains(event.target) ? 'detail' : 'inbox';
    }
  });
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
  byId('decision-form').addEventListener('submit', event => submit(event, 'decision-notice', async decision => {
    const item = current();
    const rationale = (item?.status === 'applying' ? item.decision_rationale ?? '' : byId('rationale').value).trim();
    if (!item || !['approve', 'request_changes', 'reject'].includes(decision)) return;
    if (!rationale) return displayNotice('decision-notice', 'reasonRequired', true);
    const input = { decision, revision: item.revision, proposal_hash: item.proposal_hash, rationale };
    const updated = demo ? demo.decide(item.id, input, lang) :
      await request(`/cases/${encodeURIComponent(item.id)}/decisions`, input);
    ++listRevision;
    setRefreshing(false);
    const sameCase = current()?.id === item.id;
    const focusedForm = sameCase && byId('decision-form').contains(document.activeElement);
    if (sameCase) byId('rationale').value = '';
    cases = cases.map(previous => previous.id === item.id ? updated : previous);
    renderList();
    displayCase();
    if (sameCase) {
      displayNotice('decision-notice', 'recorded');
      if (focusedForm && byId('decision-form').hidden) byId('decision-notice').focus();
      if (!byId('history-panel').hidden) void loadHistory();
    }
  }));
  byId('feedback-form').addEventListener('submit', event => submit(event, 'feedback-notice', async rating => {
    const item = current();
    if (!item || !['helpful', 'needs_work'].includes(rating)) return;
    const input = { rating, comment: byId('feedback-comment').value.trim() };
    if (demo) demo.feedback(item.id, input);
    else await request(`/cases/${encodeURIComponent(item.id)}/feedback`, input);
    if (current()?.id === item.id) {
      byId('feedback-comment').value = '';
      displayNotice('feedback-notice', 'feedbackRecorded');
      if (!byId('history-panel').hidden) void loadHistory();
    }
  }));
  byId('chat-form').addEventListener('submit', event => submit(event, 'chat-notice', async () => {
    const field = byId('message');
    const message = field.value.trim();
    if (!message) return displayNotice('chat-notice', 'messageRequired', true);
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
    openCase(escalationId, true);
  });

  translate();
  void loadCases();
})();
