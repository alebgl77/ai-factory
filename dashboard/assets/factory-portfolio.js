/* SPDX-License-Identifier: MIT */
(() => {
  'use strict';

  const STATUSES = ['planned', 'ready', 'in_progress', 'blocked', 'review', 'workbench', 'done', 'cancelled'];
  const PRIORITIES = ['low', 'normal', 'high', 'critical'];
  const EXECUTIONS = ['human', 'read_only', 'prepare', 'sensitive'];
  const AUTONOMIES = ['autonomous', 'human_review', 'blocked'];
  const STATES = ['planned', 'active', 'done', 'archived'];
  const GOAL_STATUS = { met: ['Target met', 'Cible atteinte'], unmet: ['Target unmet', 'Cible non atteinte'],
    unmeasured: ['Awaiting measurement', 'En attente de mesure'] };
  const DIRECTIONS = ['at_least', 'at_most'];
  const ROLES = ['project_lead', 'manager', 'architect'];
  const KINDS = ['employee', 'agent'];
  const RUNNER_ERROR = {
    interrupted: ['The run was interrupted. Review before assigning it again.', 'L’exécution a été interrompue. Vérifiez-la avant de réattribuer la tâche.'],
    gateway_unavailable: ['The model gateway refused or could not complete the call.', 'La passerelle a refusé l’appel ou n’a pas pu l’exécuter.'],
    invalid_response: ['The model response or token counters could not be validated.', 'La réponse du modèle ou les compteurs n’ont pas pu être validés.'],
    incomplete_output: ['The output was incomplete. Known token usage was recorded.', 'Le résultat est incomplet. Les tokens mesurés ont été enregistrés.'],
    authorization_changed: ['The agent or parent task lost its authorization during the run.', 'L’agent ou la tâche parente a perdu son autorisation pendant l’exécution.'],
  };
  const WORKBENCH_DECISION = {
    approve: ['Approved', 'Approuvée'], reject: ['Rejected', 'Rejetée'],
    request_changes: ['Changes requested', 'Modifications demandées'],
  };
  const id = value => typeof value === 'string' && /^[0-9a-f]{32}$/.test(value);
  const text = (value, max = 1200) => typeof value === 'string' && Array.from(value).length <= max;
  const ref = value => text(value, 256) && value.length > 0;
  const date = value => value === null || (typeof value === 'string' && /^\d{4}-\d\d-\d\d$/.test(value) && Number.isFinite(Date.parse(value)));
  const revision = value => Number.isSafeInteger(value) && value >= 1;
  const timestamp = value => value == null || (text(value, 32) && Number.isFinite(Date.parse(value)));
  const metric = value => Number.isFinite(value) && value >= 0;
  const amount = value => Number.isSafeInteger(value) && value >= 0 && value <= 10 ** 9;
  const optionalRef = value => value === null || value === undefined || ref(value);
  const assigned = value => value === null || (!!value && KINDS.includes(value.kind) && ref(value.id));
  const workbenchDecision = value => value == null || (typeof value === 'object'
    && Object.hasOwn(WORKBENCH_DECISION, value.outcome) && ref(value.at) && Number.isFinite(Date.parse(value.at)));
  const evidence = value => Array.isArray(value) && value.length <= 20 &&
    value.every(row => row && ref(row.kind) && text(row.label, 512) && optionalRef(row.ref));
  const history = value => Array.isArray(value) && value.length <= 20 &&
    value.every(row => row && ref(row.action) && STATUSES.includes(row.status) && optionalRef(row.actor) && revision(row.revision) && ref(row.at));
  const item = row => row && id(row.id) && text(row.title, 512) && text(row.description) && date(row.due_on) && revision(row.revision);
  const summary = (row, key, values) => row && Object.keys(row).length === 2 && text(row.title, 512) && values.includes(row[key]);
  const array = (value, test) => Array.isArray(value) && value.length <= 1000 && value.every(test);
  const distinct = rows => {
    const ids = rows.filter(row => row.id).map(row => row.id);
    return new Set(ids).size === ids.length;
  };

  function parse(data) {
    const c = data?.capabilities;
    if (!data || typeof data.truncated !== 'boolean' || typeof data.view !== 'string' || !/^[0-9a-f]{64}$/.test(data.view) ||
      (data.next_cursor != null && !/^[A-Za-z0-9_-]+\.[0-9a-f]{64}$/.test(data.next_cursor)) ||
      !array(data.goals, row => summary(row, 'status', Object.keys(GOAL_STATUS)) ||
        item(row) && text(row.metric, 256) && metric(row.target) && (row.current === null || metric(row.current)) && DIRECTIONS.includes(row.direction)) ||
      !array(data.projects, row => summary(row, 'state', STATES) ||
        item(row) && id(row.goal_id) && array(row.unit_ids, ref) && ref(row.owner_unit) && optionalRef(row.usecase_id) && STATES.includes(row.state)) ||
      !array(data.tasks, row => item(row) && id(row.project_id) && ref(row.unit) && PRIORITIES.includes(row.priority) && EXECUTIONS.includes(row.execution) &&
        typeof row.technical === 'boolean' && array(row.dependency_ids, id) && typeof row.dependencies_ready === 'boolean' &&
        assigned(row.assignee) && STATUSES.includes(row.status) && evidence(row.evidence) && history(row.history) && workbenchDecision(row.workbench_decision) &&
        typeof row.start_authorized === 'boolean' && (row.execution === 'human' ? row.autonomy === null : AUTONOMIES.includes(row.autonomy)) &&
        (row.runner_managed === undefined || typeof row.runner_managed === 'boolean') &&
        (row.runner_available === undefined || typeof row.runner_available === 'boolean') &&
        (row.runner_result == null || text(row.runner_result, 4000)) &&
        (row.runner_error == null || Object.hasOwn(RUNNER_ERROR, row.runner_error)) &&
        timestamp(row.runner_started_at) && timestamp(row.runner_finished_at) &&
        (row.parent_task_id === null ? row.delegation === null : id(row.parent_task_id) && ['manual', 'automatic'].includes(row.delegation)) &&
        (row.tokens === null || (row.tokens && amount(row.tokens.input) && amount(row.tokens.output) && row.tokens.total === row.tokens.input + row.tokens.output))) ||
      !array(data.units, row => row && ref(row.id) && text(row.label, 512)) ||
      !array(data.people, row => row && ref(row.id) && text(row.label, 512) && ref(row.unit)) ||
      !array(data.agents, row => row && ref(row.id) && text(row.label, 512) && ref(row.unit) && typeof row.ready === 'boolean') ||
      ![data.goals, data.projects, data.tasks, data.units].every(distinct) ||
      !c || typeof c.global_admin !== 'boolean' || !optionalRef(c.employee_id) || !optionalRef(c.agent_id) ||
      !c.roles_by_unit || typeof c.roles_by_unit !== 'object' || Array.isArray(c.roles_by_unit) ||
      !Object.entries(c.roles_by_unit).every(([key, values]) => ref(key) && Array.isArray(values) && values.every(role => ROLES.includes(role)))) return null;
    return data;
  }

  const uuid = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, '0')).join('');
  const localeOf = language => language === 'fr' ? 'fr-FR' : 'en-GB';
  const fr = language => language === 'fr';
  const copy = (language, en, french) => fr(language) ? french : en;
  const dateOf = (now, days = 0) => new Date(now + days * 86400000).toISOString().slice(0, 10);
  const demoId = index => index.toString(16).padStart(32, '0');
  function demo(language, now = Date.now()) {
    const t = (en, french) => copy(language, en, french);
    const base = (index, title, description, offset) => ({ id: demoId(index), title, description, due_on: dateOf(now, offset), revision: 1 });
    const units = [{ id: 'delivery', label: 'Delivery' }, { id: 'platform', label: t('Platform', 'Plateforme') }, { id: 'governance', label: t('Governance', 'Gouvernance') }];
    const people = [{ id: 'camille', label: 'Camille Laurent', unit: 'delivery' }, { id: 'lucas', label: 'Lucas Perrin', unit: 'platform' }, { id: 'sarah', label: 'Sarah Nguyen', unit: 'governance' }];
    const agents = [{ id: 'quality-agent', label: t('Quality Agent', 'Agent Qualité'), unit: 'delivery', ready: true },
      { id: 'retrieval-agent', label: t('Retrieval Agent', 'Agent Recherche'), unit: 'delivery', ready: true },
      { id: 'platform-agent', label: 'Agent Ops', unit: 'platform', ready: true }, { id: 'audit-agent', label: 'Agent Audit', unit: 'governance', ready: true }];
    const autonomy_by_unit = {
      delivery: { read_only: 'autonomous', prepare: 'autonomous', sensitive: 'human_review' },
      platform: { read_only: 'autonomous', prepare: 'human_review', sensitive: 'human_review' },
      governance: { read_only: 'human_review', prepare: 'human_review', sensitive: 'blocked' },
    };
    const goals = [
      { ...base(1, t('Resolve requests faster', 'Accélérer la résolution des demandes'), t('Reduce request turnaround while keeping audit evidence traceable.', 'Réduire le délai de traitement des demandes et conserver des preuves traçables.'), 34),
        metric: t('Median turnaround (h)', 'Délai médian (h)'), target: 24, current: 31, direction: 'at_most' },
      { ...base(2, t('Extend governance coverage', 'Étendre la couverture de gouvernance'), t('Document active AI workflows and the related decisions.', 'Documenter les workflows IA actifs et les décisions associées.'), 62),
        metric: t('Documented workflows (%)', 'Workflows documentés (%)'), target: 85, current: 61, direction: 'at_least' },
    ];
    const project = (n, goal, title, desc, days, unit_ids, usecase_id) => ({ ...base(n, title, desc, days), goal_id: demoId(goal), state: 'active', owner_unit: unit_ids[0], unit_ids, usecase_id });
    const projects = [
      project(10, 1, t('Customer triage', 'Triage des demandes client'), t('Pilot CRM case intake and the evaluation workflow.', 'Piloter l’entrée des demandes CRM et le parcours d’évaluation.'), 19, ['delivery', 'platform'], 'uc-04'),
      project(11, 2, t('AI approval register', 'Registre des validations IA'), t('Make decision evidence available for review.', 'Rassembler les preuves de décision pour la revue.'), 38, ['governance', 'platform'], 'uc-01'),
    ];
    const task = (n, projectNo, unit, title, description, days, status, opts = {}) => ({
      ...base(n, title, description, days), project_id: demoId(projectNo), unit, priority: 'normal', execution: 'human',
      technical: false, dependency_ids: [], dependencies_ready: true, assignee: null, status, evidence: [], history: [],
      autonomy: opts.execution ? autonomy_by_unit[unit][opts.execution] : null, start_authorized: false,
      parent_task_id: null, delegation: null, tokens: null, ...opts,
    });
    const tasks = [
      task(21, 10, 'delivery', t('Prepare the RAG evidence', 'Préparer les preuves RAG'), t('Produce a reusable comparison for the technical review.', 'Produire un comparatif réutilisable pour la revue technique.'), 3, 'in_progress',
        { execution: 'prepare', technical: true, priority: 'high', assignee: { kind: 'agent', id: 'quality-agent' } }),
      task(22, 10, 'delivery', t('Qualify the pilot requests', 'Qualifier les demandes du pilote'), t('Select representative cases for the acceptance test.', 'Sélectionner des cas représentatifs pour la recette.'), 2, 'ready',
        { assignee: { kind: 'employee', id: 'camille' } }),
      task(23, 10, 'delivery', t('Check the ERP handoff', 'Vérifier le passage de relais ERP'), t('Prepare the review inputs for the separately controlled action.', 'Préparer les éléments de revue pour l’action soumise à validation séparée.'), -1, 'blocked',
        { execution: 'sensitive', priority: 'critical', assignee: { kind: 'agent', id: 'quality-agent' }, dependency_ids: [demoId(21)], dependencies_ready: false }),
      task(24, 11, 'governance', t('Review the evidence plan', 'Revoir le plan de preuves'), t('Check that the evidence can be reviewed independently.', 'Vérifier que les preuves peuvent être relues indépendamment.'), 4, 'review',
        { execution: 'prepare', technical: true, assignee: { kind: 'agent', id: 'audit-agent' },
          evidence: [{ kind: 'work', label: t('Audit checklist', 'Liste de contrôle d’audit'), ref: 'DEMO-24' }] }),
      task(25, 11, 'governance', t('Publish the review calendar', 'Publier le calendrier de revue'), t('Agree the dates for the first governance reviews.', 'Arrêter les dates des premières revues de gouvernance.'), -2, 'done',
        { assignee: { kind: 'employee', id: 'sarah' } }),
      task(26, 11, 'platform', t('Set up evidence indexing', 'Configurer l’indexation des preuves'), t('Assign the work to a person or an eligible preparation agent.', 'Confier le travail à une personne ou à un agent de préparation autorisé.'), 9, 'planned', { execution: 'prepare', technical: true }),
      task(27, 10, 'delivery', t('Measure retrieval consistency', 'Mesurer la cohérence de recherche'), '', 2, 'in_progress',
        { execution: 'prepare', technical: true, assignee: { kind: 'agent', id: 'retrieval-agent' }, parent_task_id: demoId(21), delegation: 'automatic' }),
      task(28, 10, 'delivery', t('Inventory RAG sources', 'Inventorier les sources RAG'), '', 2, 'done',
        { execution: 'prepare', technical: true, assignee: { kind: 'agent', id: 'retrieval-agent' }, parent_task_id: demoId(21), delegation: 'manual',
          tokens: { input: 590, output: 130, total: 720, source: 'agent_reported' }, evidence: [{ kind: 'file', label: t('Source inventory', 'Inventaire des sources'), ref: 'DEMO-RAG-01' }] }),
    ];
    return { goals, projects, tasks, units, people, agents, truncated: false, view: '0'.repeat(64),
      capabilities: { global_admin: true, roles_by_unit: Object.fromEntries(units.map(unit => [unit.id, ROLES])), autonomy_by_unit, employee_id: null, agent_id: null } };
  }

  const rowById = (rows, key) => key ? rows.find(row => row.id === key) : undefined;
  const role = (data, unit, name) => Object.hasOwn(data.capabilities.roles_by_unit, unit) && data.capabilities.roles_by_unit[unit].includes(name);
  const leadUnits = data => data.units.filter(unit => role(data, unit.id, 'project_lead'));
  function assignees(data, task) {
    return task.execution === 'human'
      ? data.people.filter(row => row.unit === task.unit).map(row => ({ ...row, kind: 'employee' }))
      : data.agents.filter(row => row.unit === task.unit && row.ready && task.autonomy !== 'blocked').map(row => ({ ...row, kind: 'agent' }));
  }
  const myTask = (task, data, fictional) => !!task.assignee && (fictional ||
    data.capabilities[task.assignee.kind === 'agent' ? 'agent_id' : 'employee_id'] === task.assignee.id);
  const finished = task => ['done', 'cancelled'].includes(task.status);
  const runnerActive = task => task.status === 'in_progress' && task.runner_started_at && !task.runner_finished_at;
  const childrenOf = (data, task) => data.tasks.filter(child => child.parent_task_id === task.id);
  function delegationOptions(data, task, fictional) {
    const manager = role(data, task.unit, 'manager'), mode = task.execution === 'read_only' ? 'read_only' : 'prepare';
    const policy = data.capabilities.autonomy_by_unit?.[task.unit]?.[mode] || 'human_review';
    const parent = task.assignee?.kind === 'agent' && rowById(data.agents, task.assignee.id);
    if (task.parent_task_id || task.status !== 'in_progress' || !parent?.ready || task.autonomy === 'blocked' ||
        policy === 'blocked' || childrenOf(data, task).length >= 4) return { modes: [], peers: [] };
    const peers = data.agents.filter(agent => agent.ready && agent.unit === task.unit && agent.id !== parent.id);
    // A service principal can delegate automatically without reading the peer roster.
    const modes = manager ? (peers.length ? ['manual', 'automatic'] : [])
      : policy === 'autonomous' && myTask(task, data, fictional) ? ['automatic'] : [];
    return { modes, peers };
  }
  function commands(data, task, fictional) {
    const actions = [];
    const agent = task.assignee?.kind === 'agent';
    const eligible = !agent || (rowById(data.agents, task.assignee.id)?.ready && task.autonomy !== 'blocked');
    const canStart = eligible && (!agent || task.autonomy === 'autonomous' || (task.autonomy === 'human_review' && task.start_authorized));
    if (['planned', 'ready', 'blocked'].includes(task.status) && role(data, task.unit, 'manager') && assignees(data, task).length) actions.push('assign');
    if (task.status === 'ready' && agent && eligible && task.autonomy === 'human_review' && !task.start_authorized && role(data, task.unit, 'manager')) actions.push('authorize_start');
    if (myTask(task, data, fictional)) {
      if (task.status === 'ready' && task.dependencies_ready && canStart && !task.runner_managed) actions.push('start');
      if (task.status === 'in_progress') {
        actions.push('block');
        if (!task.runner_managed && !runnerActive(task)) {
          if (agent && eligible) actions.push('report_usage');
          if (eligible && childrenOf(data, task).every(finished)) actions.push('submit');
        }
      }
      if (task.status === 'blocked' && task.dependencies_ready && eligible) actions.push('unblock');
    }
    if (task.status === 'in_progress' && agent && !eligible && role(data, task.unit, 'manager') && !actions.includes('block')) actions.push('block');
    if (task.status === 'review' && role(data, task.unit, 'architect') && (fictional || !myTask(task, data, false))) actions.push('approve', 'request_changes');
    if (task.parent_task_id && !finished(task) && role(data, task.unit, 'manager')) actions.push('cancel');
    return actions;
  }

  function applyDemo(data, mutation) {
    const rows = data[mutation.collection], previous = rowById(rows, mutation.key);
    if (mutation.action === 'delegate') {
      if (!previous || previous.revision !== mutation.body.revision) return false;
      const { modes, peers } = delegationOptions(data, previous, true);
      const { id, mode, title, description, agent_id } = mutation.body;
      if (!modes.includes(mode) || rowById(rows, id)) return false;
      const load = peer => data.tasks.filter(task => !finished(task) && task.assignee?.kind === 'agent' && task.assignee.id === peer.id).length;
      const peer = mode === 'manual' ? rowById(peers, agent_id) : [...peers].sort((a, b) => load(a) - load(b) || a.id.localeCompare(b.id))[0];
      if (!peer) return false;
      const execution = previous.execution === 'read_only' ? 'read_only' : 'prepare';
      rows.push({ id, title, description, project_id: previous.project_id, unit: previous.unit, execution, technical: previous.technical,
        due_on: previous.due_on, priority: previous.priority, dependency_ids: [], dependencies_ready: true, parent_task_id: previous.id,
        delegation: mode, tokens: null, autonomy: data.capabilities.autonomy_by_unit[previous.unit][execution], start_authorized: false,
        assignee: { kind: 'agent', id: peer.id }, status: 'ready', revision: 1, history: [], evidence: [] });
      previous.revision++;
    } else if (mutation.action) {
      if (!previous || !commands(data, previous, true).includes(mutation.action) || previous.revision !== mutation.body.revision) return false;
      const { action, assignee, evidence: submitted } = mutation.body;
      if (action === 'assign') {
        if (!assignees(data, previous).some(row => row.id === assignee?.id && row.kind === assignee.kind)) return false;
        if (previous.assignee?.id !== assignee.id || previous.assignee?.kind !== assignee.kind) previous.tokens = null;
        previous.assignee = assignee; previous.evidence = [];
      }
      const statuses = { assign: 'ready', authorize_start: 'ready', start: 'in_progress', block: 'blocked', unblock: 'ready', cancel: 'cancelled',
        report_usage: 'in_progress',
        approve: previous.execution === 'sensitive' ? 'workbench' : 'done', request_changes: 'ready',
        submit: previous.technical ? 'review' : previous.execution === 'sensitive' ? 'workbench' : 'done' };
      const tokens = mutation.body.tokens;
      if (tokens && previous.tokens && (tokens.input < previous.tokens.input || tokens.output < previous.tokens.output)) return false;
      if (action === 'report_usage' && tokens && previous.tokens?.input === tokens.input && previous.tokens?.output === tokens.output) return true;
      if (action === 'submit') previous.evidence = submitted || [];
      if (tokens) previous.tokens = { ...tokens, total: tokens.input + tokens.output, source: 'agent_reported' };
      previous.start_authorized = action === 'authorize_start';
      previous.status = statuses[action]; previous.revision++;
      previous.history = [{ action, status: previous.status, actor: 'demo', revision: previous.revision, at: new Date().toISOString() }, ...previous.history].slice(0, 20);
    } else if (mutation.collection === 'tasks') {
      const project = rowById(data.projects, previous?.project_id || mutation.body.project_id);
      if (!project?.unit_ids.includes(previous?.unit || mutation.body.unit)) return false;
      const reaches = (key, seen) => {
        if (key === mutation.key) return true;
        if (seen.has(key)) return false;
        seen.add(key);
        return rowById(data.tasks, key)?.dependency_ids.some(next => reaches(next, seen)) || false;
      };
      if (mutation.body.dependency_ids.length > 12 || mutation.body.dependency_ids.some(key => {
        const dependency = rowById(data.tasks, key);
        return !dependency || dependency.project_id !== project.id || dependency.unit !== (previous?.unit || mutation.body.unit) || reaches(key, new Set());
      })) return false;
      if (previous) {
        if (previous.revision !== mutation.body.revision || !['planned', 'ready'].includes(previous.status)) return false;
        Object.assign(previous, mutation.body, { start_authorized: false, revision: previous.revision + 1 });
      } else {
        const { execution, unit } = mutation.body;
        rows.push({ ...mutation.body, revision: 1, status: 'planned', assignee: null, evidence: [], history: [], start_authorized: false,
          parent_task_id: null, delegation: null, tokens: null,
          autonomy: execution === 'human' ? null : data.capabilities.autonomy_by_unit[unit]?.[execution] || 'human_review' });
      }
    } else if (mutation.collection === 'projects' && !previous &&
      (!rowById(data.goals, mutation.body.goal_id) || !mutation.body.unit_ids.includes(mutation.body.owner_unit))) {
      return false;
    } else if (previous) {
      if (previous.revision !== mutation.body.revision) return false;
      Object.assign(previous, mutation.body, { revision: previous.revision + 1 });
    } else {
      rows.push({ ...mutation.body, revision: 1 });
    }
    for (const task of data.tasks) task.dependencies_ready = task.dependency_ids.every(key => rowById(data.tasks, key)?.status === 'done');
    return true;
  }

  const STATUS = { planned: ['Unassigned', 'À attribuer'], ready: ['Ready', 'Prête'], in_progress: ['In progress', 'En cours'], blocked: ['Blocked', 'Bloquée'],
    review: ['Technical review', 'Revue technique'], workbench: ['Workbench handoff', 'Relais Workbench'], done: ['Done', 'Terminée'], cancelled: ['Cancelled', 'Annulée'] };
  const PRIORITY = { low: ['Low', 'Basse'], normal: ['Normal', 'Normale'], high: ['High', 'Haute'], critical: ['Critical', 'Critique'] };
  const EXECUTION = { human: ['Human work', 'Travail humain'], read_only: ['Read only', 'Lecture seule'], prepare: ['Agent preparation', 'Préparation IA'],
    sensitive: ['Sensitive handoff', 'Relais sensible'] };
  const PROJECT = { planned: ['Planned', 'Planifié'], active: ['Active', 'Actif'], done: ['Done', 'Terminé'], archived: ['Archived', 'Archivé'] };
  const DIRECTION = { at_least: ['At least', 'Au moins'], at_most: ['At most', 'Au plus'] };
  const ROLE = { manager: ['Manager', 'Manager'], project_lead: ['Project lead', 'Chef de projet'], architect: ['Architect', 'Architecte'] };
  const DELEGATION = { manual: ['Manual', 'Manuelle'], automatic: ['Automatic', 'Automatique'] };
  const ACTION = { assign: ['Assign', 'Attribuer'], authorize_start: ['Authorize start', 'Autoriser le démarrage'], start: ['Start', 'Démarrer'],
    block: ['Block', 'Bloquer'], unblock: ['Unblock', 'Débloquer'], submit: ['Submit', 'Soumettre'], approve: ['Approve', 'Valider'],
    request_changes: ['Request changes', 'Demander des modifications'], cancel: ['Cancel subtask', 'Annuler la sous-tâche'],
    delegate: ['Delegate to a subagent', 'Déléguer à un sous-agent'], report_usage: ['Report cumulative tokens', 'Déclarer les tokens cumulés'],
    workbench_approve: WORKBENCH_DECISION.approve, workbench_reject: WORKBENCH_DECISION.reject,
    runner_start: ['Local execution started', 'Exécution locale démarrée'], runner_block: ['Local execution blocked', 'Exécution locale bloquée'],
    workbench_request_changes: WORKBENCH_DECISION.request_changes };
  const AUTONOMY = { autonomous: ['Autonomous', 'Autonome'], human_review: ['Human authorization', 'Autorisation humaine'], blocked: ['Forbidden by BU', 'Interdit par la BU'] };
  const LANES = [
    ['queue', ['Queue', 'À lancer'], ['planned', 'ready']],
    ['doing', ['In progress', 'En cours'], ['in_progress']],
    ['gate', ['Gates', 'Blocages et revues'], ['blocked', 'review', 'workbench']],
    ['closed', ['Closed', 'Clôturées'], ['done', 'cancelled']],
  ];

  function helpers(language, esc) {
    const t = (en, french) => copy(language, en, french);
    const l = (dict, key) => Object.hasOwn(dict, key) ? t(...dict[key]) : '—';
    const num = new Intl.NumberFormat(localeOf(language));
    const dates = new Intl.DateTimeFormat(localeOf(language), { day: 'numeric', month: 'short', timeZone: 'UTC' });
    const due = value => value ? dates.format(new Date(`${value}T00:00:00Z`)) : t('No due date', 'Sans échéance');
    const tokensLabel = task => task.tokens?.source === 'provider_reported' ? t('tokens measured by the gateway', 'tokens mesurés par la passerelle')
      : task.tokens?.source === 'mixed_reported' ? t('measured and reported tokens', 'tokens mesurés et déclarés')
      : t('reported tokens', 'tokens déclarés');
    const option = (id, label, chosen) => `<option value="${esc(id)}"${chosen ? ' selected' : ''}>${esc(label)}</option>`;
    const select = (label, attributes, choices, value, many = false) => `<label>${label}<select ${attributes}>${choices.map(([id, label]) =>
      option(id, label, many ? (value || []).includes(id) : value === id)).join('')}</select></label>`;
    const button = (attribute, value, label) => `<button type="button" class="mini" ${attribute}="${esc(value)}">${label}</button>`;
    return { t, l, num, due, select, button, tokensLabel };
  }

  function renderEditor(view, h, esc) {
    const { data, editor, pending } = view, { t, l, select, button } = h;
    if (!editor) return '';
    const { collection, key, action, values } = editor;
    const row = rowById(data[collection], key), unitRows = leadUnits(data);
    const value = (name, fallback = '') => Object.hasOwn(values, name) ? values[name] : ((!action ? row?.[name] : undefined) ?? fallback);
    const attr = name => `data-portfolio-field="${name}" name="${name}"`;
    const field = (name, label, type = 'text', extra = '') => `<label>${label}<input ${attr(name)} type="${type}" value="${esc(value(name))}" ${extra}></label>`;
    const pick = (name, label, options, fallback = '', extra = '') => select(label, `${attr(name)} ${extra}`, options, value(name, fallback), extra.includes('multiple'));
    const options = (dict, values) => values.map(key => [key, l(dict, key)]);
    const title = field('title', t('Title', 'Intitulé'), 'text', 'required maxlength="120"');
    const due = field('due_on', t('Due date', 'Échéance'), 'date');
    const description = `<label class="portfolio-wide">${t('Description', 'Description')}<textarea ${attr('description')} maxlength="1200" rows="2">${esc(value('description'))}</textarea></label>`;
    const units = data.units.map(unit => [unit.id, unit.label]);
    const editableUnits = unitRows.map(unit => [unit.id, unit.label]);
    let fields = '', name = '';
    if (action === 'assign' && row) {
      name = row.execution === 'human' ? t('Assign an employee', 'Attribuer à un employé') : t('Assign an eligible agent', 'Attribuer à un agent éligible');
      fields = pick('assignee', t('Assignee', 'Responsable'), [['', t('Choose an assignee', 'Choisir un responsable')], ...assignees(data, row).map(who =>
        [`${who.kind}:${who.id}`, `${who.label} · ${who.kind === 'agent' ? t('AI agent', 'Agent IA') : t('Employee', 'Employé')}`])], '', 'required');
    } else if (action === 'delegate' && row) {
      name = l(ACTION, 'delegate');
      const { modes, peers } = delegationOptions(data, row, view.source === 'demo');
      const mode = value('mode', modes[0]);
      fields = `${title}${pick('mode', t('Delegation', 'Délégation'), options(DELEGATION, modes), mode, 'required')}
        ${mode === 'manual' ? pick('agent_id', t('Eligible agent', 'Agent éligible'), peers.map(agent => [agent.id,
          agent.runner_managed ? `${agent.label} · ${agent.runner_available ? t('Local engine', 'Moteur local') : t('Local engine unavailable', 'Moteur local indisponible')}` : agent.label]), peers[0]?.id || '', 'required') : ''}
        ${description}<p class="portfolio-note portfolio-wide">${t('A child task is queued for an eligible agent in the same BU. The child follows its BU policy.',
          'Une sous-tâche est mise en file pour un agent éligible de la même BU. Elle suit la politique de cette BU.')}</p>`;
    } else if (action === 'report_usage' && row) {
      name = l(ACTION, 'report_usage');
      const minimum = key => row.tokens?.[key] || 0;
      fields = `${field('input', t('Input tokens since assignment', 'Tokens d’entrée depuis l’attribution'), 'number', `required min="${minimum('input')}" max="1000000000" step="1"`)}
        ${field('output', t('Output tokens since assignment', 'Tokens de sortie depuis l’attribution'), 'number', `required min="${minimum('output')}" max="1000000000" step="1"`)}
        <p class="portfolio-note portfolio-wide">${t('Report the cumulative counters, including earlier reports. Use the latest value from your runtime.',
          'Déclarez les compteurs cumulés, y compris les déclarations précédentes. Utilisez la dernière mesure de votre environnement d’exécution.')}</p>`;
    } else if (action === 'submit' && row) {
      name = row.execution === 'sensitive' ? t('Prepare the Workbench handoff', 'Préparer le relais Workbench') : t('Submit the work', 'Soumettre le travail');
      fields = `${field('label', t('Evidence label', 'Intitulé de la preuve'), 'text', 'maxlength="120"')}
        ${field('ref', t('Opaque reference', 'Référence opaque'), 'text', 'maxlength="64" pattern="[A-Za-z0-9][A-Za-z0-9._\\-]{0,63}"')}
        ${row.assignee?.kind === 'agent' ? `${field('input', t('Reported input tokens', 'Tokens d’entrée déclarés'), 'number', 'min="0" max="1000000000" step="1"')}
          ${field('output', t('Reported output tokens', 'Tokens de sortie déclarés'), 'number', 'min="0" max="1000000000" step="1"')}` : ''}
        ${row.execution === 'sensitive' ? `<p class="portfolio-note portfolio-wide">${t('This records a handoff for human review. Any external action needs separate Workbench authorization.',
          'Le relais est consigné pour une revue humaine. Toute action externe doit être autorisée séparément dans Workbench.')}</p>` : ''}`;
    } else if (collection === 'goals') {
      name = row ? t('Edit the objective', 'Modifier l’objectif') : t('Create an objective', 'Créer un objectif');
      fields = `${title}${field('metric', t('Indicator', 'Indicateur'), 'text', 'required maxlength="120"')}${due}${description}
        ${pick('direction', t('Target direction', 'Sens de la cible'), options(DIRECTION, DIRECTIONS), 'at_least')}
        ${field('current', t('Latest measurement', 'Mesure actuelle'), 'number', 'min="0" max="1000000000000" step="any"')}
        ${field('target', t('Target', 'Cible'), 'number', 'required min="0" max="1000000000000" step="any"')}`;
    } else if (collection === 'projects') {
      name = row ? t('Edit the project', 'Modifier le projet') : t('Create a project', 'Créer un projet');
      const owner = value('owner_unit', units[0]?.[0]);
      fields = `${title}${due}${pick('state', t('State', 'État'), options(PROJECT, STATES), 'planned')}${description}
        ${row ? '' : `
        ${pick('goal_id', t('Objective', 'Objectif'), data.goals.map(goal => [goal.id, goal.title]), view.filters.goal || data.goals[0]?.id || '', 'required')}
        ${pick('owner_unit', t('Lead BU', 'BU pilote'), units, owner, 'required')}
        ${pick('unit_ids', t('Participating BUs', 'BU participantes'), units, units.length ? [units[0][0]] : [], 'multiple required')}
        ${pick('usecase_id', t('Linked use case', 'Cas d’usage lié'), [['', t('None', 'Aucun')], ...view.usecases.filter(uc => uc.unit === owner).map(uc => [uc.id, uc.name])])}`}`;
    } else if (collection === 'tasks') {
      name = row ? t('Replan the task', 'Replanifier la tâche') : t('Create a task', 'Créer une tâche');
      const projects = data.projects.filter(project => project.unit_ids?.some(unit => editableUnits.some(([id]) => id === unit)));
      const projectId = value('project_id', projects.some(project => project.id === view.filters.project) ? view.filters.project : projects[0]?.id || '');
      const project = rowById(data.projects, projectId);
      const allowed = editableUnits.filter(([unit]) => project?.unit_ids.includes(unit));
      const unitId = value('unit', allowed[0]?.[0] || '');
      fields = `${title}${due}${pick('priority', t('Priority', 'Priorité'), options(PRIORITY, PRIORITIES), 'normal')}${description}
        ${row ? '' : `${pick('project_id', t('Project', 'Projet'), projects.map(project => [project.id, project.title]), projectId, 'required')}
          ${pick('unit', t('BU', 'BU'), allowed, unitId, 'required')}
          ${pick('execution', t('Execution', 'Exécution'), options(EXECUTION, EXECUTIONS), 'human')}
          ${pick('technical', t('Technical review', 'Revue technique'), [['no', t('No', 'Non')], ['yes', t('Yes', 'Oui')]], 'no')}`}
        ${pick('dependency_ids', t('Blocking dependencies', 'Dépendances bloquantes'), data.tasks.filter(task => task.id !== row?.id && task.project_id === projectId && task.unit === unitId)
          .map(task => [task.id, task.title]), [], 'multiple')}`;
    }
    return `<form class="portfolio-form" data-portfolio-form autocomplete="off"><h4>${name}</h4>
      <div class="portfolio-form-grid">${fields}</div><div class="portfolio-actions">
      ${!pending && collection === 'tasks' && !action ? button('data-assist-open', 'draft_task', t('Help me write', 'M’aider à rédiger')) : ''}
      <button type="submit" class="mini"${pending ? ' disabled' : ''}>${t('Confirm', 'Confirmer')}</button>${pending ? '' : button('data-portfolio-action', 'cancel', t('Cancel', 'Annuler'))}</div></form>`;
  }

  function mutation(view, form) {
    const editor = view.editor;
    if (!editor) return null;
    const { collection, key, action } = editor;
    const row = rowById(view.data[collection], key);
    const values = new FormData(form);
    const val = name => String(values.get(name) || '').trim();
    let body;
    if (action === 'delegate' && row) {
      body = { revision: editor.revision, mode: val('mode'), title: val('title'), description: val('description') };
      if (!body.title || !delegationOptions(view.data, row, view.source === 'demo').modes.includes(body.mode)) return null;
      if (body.mode === 'manual') body.agent_id = val('agent_id');
      if (view.source === 'demo') body.id = uuid();
    } else if (action && row) {
      body = { revision: editor.revision, action };
      if (action === 'assign') {
        const [kind, ...parts] = val('assignee').split(':');
        body.assignee = { kind, id: parts.join(':') };
      } else if (action === 'submit' && (val('label') || val('ref'))) {
        if (!val('label') || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(val('ref')) || /[\/:\\\n]|\.\./.test(val('label') + val('ref'))) return null;
        body.evidence = [{ kind: 'work', label: val('label'), ref: val('ref') }];
      }
      if ((action === 'report_usage' || (action === 'submit' && (val('input') || val('output')))) && row.assignee?.kind === 'agent') {
        if (val('input') === '' || val('output') === '' || !amount(Number(val('input'))) || !amount(Number(val('output')))) return null;
        body.tokens = { input: Number(val('input')), output: Number(val('output')) };
      }
    } else {
      body = { title: val('title'), description: val('description'), due_on: val('due_on') || null };
      if (!body.title) return null;
      if (collection === 'goals') Object.assign(body, { metric: val('metric'), direction: val('direction'), target: Number(val('target')), current: val('current') === '' ? null : Number(val('current')) });
      if (collection === 'projects') {
        body.state = val('state');
        if (!row) {
          const unit_ids = values.getAll('unit_ids');
          if (!unit_ids.includes(val('owner_unit'))) return null;
          Object.assign(body, { goal_id: val('goal_id'), owner_unit: val('owner_unit'), unit_ids, usecase_id: val('usecase_id') || null });
        }
      }
      if (collection === 'tasks') Object.assign(body, { priority: val('priority'), dependency_ids: values.getAll('dependency_ids'),
        ...(row ? {} : { project_id: val('project_id'), unit: val('unit'), execution: val('execution'), technical: val('technical') === 'yes' }) });
      if (row) body.revision = editor.revision;
      else if (collection !== 'tasks' || view.source === 'demo') body.id = uuid();
    }
    return { collection, key: key || body.id, action, method: row && !action ? 'PUT' : 'POST', body };
  }

  function render(view, language, esc) {
    const h = helpers(language, esc), { t, l, num, due, select, button } = h;
    const { data, filters, source, status } = view;
    const title = t('Portfolio and tasks', 'Portefeuille et tâches');
    const header = `<header class="portfolio-hero"><div><span class="portfolio-kicker">${t('Delivery operations', 'Pilotage des opérations')}</span>
      <h3>${title}</h3><p>${t('Connect each objective to the projects, people and agents delivering it.', 'Reliez chaque objectif aux projets, aux personnes et aux agents qui le réalisent.')}</p></div>
      <span class="portfolio-badge">${source === 'demo' ? t('Fictional demo', 'Démo fictive') : source === 'api' ? t('Protected workspace', 'Espace protégé') : t('Unavailable', 'Indisponible')}</span></header>`;
    const top = `<section class="portfolio" data-portfolio-state="${status}"${status === 'loading' || view.pending ? ' aria-busy="true"' : ''}>${header}`;
    if (!data) {
      const note = { loading: t('Loading the authorized portfolio…', 'Chargement du portefeuille autorisé…'),
        unavailable: t('A protected workspace with a BU portfolio policy is required.', 'Un espace protégé avec une politique de portefeuille par BU est requis.'),
        blocked: t('This portfolio is unavailable for your current access.', 'Ce portefeuille est indisponible pour vos droits actuels.'),
        error: t('The portfolio could not be loaded.', 'Le portefeuille n’a pas pu être chargé.') }[status];
      return `${top}<p class="portfolio-alert" role="status">${note || ''} ${source === 'api' && status !== 'loading' ? button('data-portfolio-action', 'retry', t('Retry', 'Réessayer')) : ''}</p></section>`;
    }
    const unit = key => rowById(data.units, key)?.label || key;
    const owner = task => !task.assignee ? t('Unassigned', 'Sans responsable') : rowById(task.assignee.kind === 'agent' ? data.agents : data.people, task.assignee.id)?.label || t('Authorized assignee', 'Responsable autorisé');
    const ownerText = task => task.assignee ? `${owner(task)} · ${task.assignee.kind === 'agent' ? t('Agent', 'Agent IA') : t('Employee', 'Employé')}` : owner(task);
    const section = (number, label, body, extra = '') => `<section class="portfolio-section"><div class="portfolio-caption"><h4><span class="portfolio-index">${number}</span>${label}</h4>${extra}</div>${body}</section>`;
    const card = (kind, row, active, html) => `<button class="portfolio-card" type="button" data-portfolio-choice="${kind}:${row.id}" aria-pressed="${active}">${html}</button>`;
    const summaryCard = (row, status) => `<article class="portfolio-card"><b>${esc(row.title)}</b><small>${status}</small></article>`;
    const empty = label => `<p class="portfolio-note">${label}</p>`;
    const visibleProjects = data.projects.filter(project => !filters.goal || project.goal_id === filters.goal);
    const visibleTasks = data.tasks.filter(task => visibleProjects.some(project => project.id === task.project_id) && (!filters.project || task.project_id === filters.project));
    const today = dateOf(Date.now());
    const late = task => !finished(task) && task.due_on && task.due_on < today;
    const strip = [[data.goals.length, t('objectives', 'objectifs')], [data.projects.length, t('projects', 'projets')],
      [data.tasks.filter(task => task.status !== 'planned' && !finished(task)).length, t('open tasks', 'tâches actives')],
      [data.tasks.filter(late).length, t('overdue', 'en retard')]];
    const summary = `<div class="portfolio-strip" aria-label="${t('Portfolio overview', 'Vue d’ensemble du portefeuille')}">${strip.map(([count, label]) =>
      `<span><b>${num.format(count)}</b>${label}</span>`).join('')}</div>`;
    const goals = data.goals.length ? `<div class="portfolio-cards">${data.goals.map(goal => {
      if (!goal.id) return summaryCard(goal, l(GOAL_STATUS, goal.status));
      const result = goal.current === null ? null : goal.direction === 'at_most' ? (goal.current <= goal.target ? 100 : 100 * goal.target / goal.current)
        : goal.target === 0 ? 100 : Math.min(100, 100 * goal.current / goal.target);
      return card('goal', goal, filters.goal === goal.id, `<b>${esc(goal.title)}</b><small>${esc(goal.metric)} · <strong>${goal.current === null ? '—' : num.format(goal.current)}</strong>
        / ${goal.direction === 'at_most' ? '≤' : '≥'} ${num.format(goal.target)} · ${due(goal.due_on)}</small>
        ${result === null ? '' : `<progress aria-label="${t('Indicator progress', 'Progression de l’indicateur')}" max="100" value="${Math.round(result)}"></progress>`}`);
    }).join('')}</div>` : empty(t('No objective in your current scope.', 'Aucun objectif dans votre périmètre.'));
    const selectedGoal = rowById(data.goals, filters.goal), selectedProject = rowById(data.projects, filters.project);
    const projects = visibleProjects.length ? `<div class="portfolio-cards">${visibleProjects.map(project => !project.id ? summaryCard(project, l(PROJECT, project.state)) : card('project', project, filters.project === project.id,
      `<b>${esc(project.title)}</b><small>${esc(unit(project.owner_unit))} · ${l(PROJECT, project.state)} · ${due(project.due_on)}</small>
      <small>${esc(rowById(data.goals, project.goal_id)?.title || '')}</small>`)).join('')}</div>` : empty(t('No project in this selection.', 'Aucun projet dans cette sélection.'));
    const ownerOptions = [...new Map(visibleTasks.filter(task => task.assignee).map(task => [`${task.assignee.kind}:${task.assignee.id}`, ownerText(task)])).entries()];
    const filter = (key, label, options) => select(label, `data-portfolio-filter="${key}"`, [['', t('All', 'Tous')], ...options], filters[key] || '');
    const controls = `<div class="portfolio-controls">
      ${filter('unit', 'BU', data.units.map(row => [row.id, row.label]))}
      ${filter('owner', t('Assignee', 'Responsable'), ownerOptions)}
      ${filter('status', t('Status', 'Statut'), STATUSES.map(key => [key, l(STATUS, key)]))}
    </div>`;
    const filtered = visibleTasks.filter(task => (!filters.unit || task.unit === filters.unit) &&
      (!filters.owner || (task.assignee && filters.owner === `${task.assignee.kind}:${task.assignee.id}`)) && (!filters.status || task.status === filters.status))
      .sort((a, b) => (a.due_on || '9999').localeCompare(b.due_on || '9999') || a.title.localeCompare(b.title));
    const shown = new Set(filtered.map(task => task.id));
    const taskCard = task => `<button type="button" class="portfolio-task" data-portfolio-choice="task:${task.id}" aria-pressed="${view.selected === task.id}">
        <span class="portfolio-status" data-status="${task.status}">${l(STATUS, task.status)} · ${esc(unit(task.unit))}</span><b>${esc(task.title)}</b>
        <small>${esc(ownerText(task))}</small><small class="${late(task) ? 'portfolio-overdue' : ''}">${due(task.due_on)} · ${l(PRIORITY, task.priority)}</small>
        ${task.autonomy ? `<small>${t('Agent policy', 'Politique agent')} · ${l(AUTONOMY, task.autonomy)}</small>` : ''}
        ${task.parent_task_id ? `<small>${l(DELEGATION, task.delegation)} · ${task.evidence.length} ${t('evidence item(s)', 'preuve(s)')}</small>` : ''}
        ${task.runner_managed ? `<small>${t('Local engine', 'Moteur local')}</small>` : ''}
        ${task.tokens ? `<small>${num.format(task.tokens.total)} ${h.tokensLabel(task)}</small>` : ''}</button>`;
    const board = filtered.length ? `<div class="portfolio-board">${LANES.map(([key, label, states]) => {
      const rows = filtered.filter(task => states.includes(task.status) && (!task.parent_task_id || !shown.has(task.parent_task_id)));
      return `<section class="portfolio-lane" data-lane="${key}"><h5>${t(...label)} <span>${rows.length}</span></h5>
      ${rows.map(task => {
        const children = filtered.filter(child => child.parent_task_id === task.id);
        if (!children.length) return taskCard(task);
        const running = children.filter(child => child.status === 'in_progress').length;
        const reported = children.filter(child => child.tokens !== null);
        const total = reported.reduce((sum, child) => sum + child.tokens.total, 0);
        return `<div class="portfolio-task-group">${taskCard(task)}
          <details class="portfolio-children" data-portfolio-parent="${task.id}"${view.expanded.has(task.id) ? ' open' : ''}>
          <summary>${children.length} ${t('subagent tasks', 'tâches déléguées')} · ${running} ${t('running', 'en cours')}<small>
          ${reported.length ? `${num.format(total)} tokens${reported.length < children.length ? t(' reported, partial', ' déclarés, partiel') : t(' reported', ' déclarés')}` : t('Tokens unknown', 'Tokens non renseignés')}</small></summary>
          <div>${children.map(taskCard).join('')}</div></details></div>`;
      }).join('')}</section>`;
    }).join('')}</div>` : empty(t('No task matches this selection.', 'Aucune tâche dans cette sélection.'));
    const selectedTask = rowById(data.tasks, view.selected);
    const detail = selectedTask ? taskDetail(view, selectedTask, h, esc, unit, ownerText, language) : '';
    const usage = new Map();
    for (const task of filtered) {
      if (task.assignee?.kind !== 'agent') continue;
      const key = task.assignee.id;
      const row = usage.get(key) || { label: owner(task), total: 0, running: 0, reported: 0, tasks: 0 };
      row.tasks++; row.running += Number(task.status === 'in_progress');
      if (task.tokens) { row.reported++; row.total += task.tokens.total; }
      usage.set(key, row);
    }
    const usageView = usage.size ? `<details class="portfolio-agent-usage" data-portfolio-usage${view.usageExpanded ? ' open' : ''}><summary>${t('Reported usage by agent', 'Utilisation déclarée par agent')}</summary>
      <p class="portfolio-note">${t('Latest cumulative counters for tasks in this view.', 'Derniers compteurs cumulés des tâches de cette vue.')}</p>
      <ul>${[...usage.values()].map(row => `<li><b>${esc(row.label)}</b><span>${row.running} ${t('running', 'en cours')} ·
        ${row.reported ? `${num.format(row.total)} tokens${row.reported < row.tasks ? t(' (partial)', ' (partiel)') : ''}` : t('tokens unknown', 'tokens non renseignés')}</span></li>`).join('')}</ul></details>` : '';
    const central = (collection, label, selected) => data.capabilities.global_admin
      ? `<div class="portfolio-actions">${button('data-portfolio-action', `edit:${collection}`, label)}
        ${selected ? button('data-portfolio-action', `edit:${collection}:${selected.id}`, t('Edit selection', 'Modifier la sélection')) : ''}</div>` : '';
    return `${top}${summary}<div class="portfolio-actions">
      ${button('data-assist-open', 'explain_process', t('Understand this workflow', 'Comprendre ce parcours'))}
      ${data.capabilities.global_admin ? button('data-assist-open', 'decision_outline', t('Prepare a decision', 'Préparer une décision')) : ''}</div>
      ${data.truncated && !data.next_cursor ? `<p class="portfolio-alert" role="status">${t('Partial portfolio. Some authorized records were omitted.', 'Portefeuille partiel. Certains éléments autorisés ne sont pas affichés.')}</p>` : ''}
      ${view.message ? `<p class="portfolio-alert" role="status">${t(...view.message)}</p>` : ''}
      ${renderEditor(view, h, esc)}
      ${section('01', t('Objectives and indicators', 'Objectifs et indicateurs'), goals + (selectedGoal?.description ? empty(esc(selectedGoal.description)) : ''), central('goals', t('New objective', 'Nouvel objectif'), selectedGoal))}
      ${section('02', t('Projects', 'Projets'), projects + (selectedProject?.description ? empty(esc(selectedProject.description)) : ''),
        data.goals.length && data.units.length ? central('projects', t('New project', 'Nouveau projet'), selectedProject) : '')}
      ${section('03', t('BU tasks', 'Tâches par BU'), controls + detail + board
        + (data.next_cursor ? button('data-portfolio-action', 'more', t('Load more tasks', 'Charger plus de tâches')) : '') + usageView,
        leadUnits(data).some(unit => visibleProjects.some(project => project.unit_ids?.includes(unit.id)))
        ? button('data-portfolio-action', 'edit:tasks', t('New task', 'Nouvelle tâche')) : '')}</section>`;
  }

  function taskDetail(view, task, h, esc, unit, ownerText, language) {
    const { t, l, due, button, num, tokensLabel } = h;
    const { data, source } = view;
    const project = rowById(data.projects, task.project_id);
    const parent = rowById(data.tasks, task.parent_task_id);
    const children = childrenOf(data, task);
    const roles = ROLES.filter(name => role(data, task.unit, name)).map(name => l(ROLE, name));
    const next = !finished(task) && task.autonomy === 'blocked' ? t('The BU policy blocks this agent execution.', 'La politique de la BU bloque cette exécution par agent.')
      : task.status === 'ready' && !task.dependencies_ready ? t('Complete the blocking dependencies first.', 'Terminer d’abord les dépendances bloquantes.')
      : task.status === 'ready' && task.assignee?.kind === 'agent' && task.autonomy === 'human_review' && !task.start_authorized
        ? t('A BU manager must authorize this start.', 'Le manager de la BU doit autoriser ce démarrage.')
      : task.runner_error ? l(RUNNER_ERROR, task.runner_error)
      : task.status === 'ready' && task.runner_managed ? (task.runner_available
        ? t('The local engine will claim this task.', 'Le moteur local prendra cette tâche en charge.')
        : t('The configured local engine is unavailable for this agent.', 'Le moteur local configuré est indisponible pour cet agent.'))
      : runnerActive(task) ? t('The local engine is executing this task.', 'Le moteur local exécute cette tâche.')
      : task.status === 'in_progress' && children.some(child => !finished(child)) ? t('Complete or cancel the delegated tasks before submitting.',
        'Terminer ou annuler les tâches déléguées avant de soumettre.')
      : ({
        planned: t('The BU manager assigns the work.', 'Le manager de la BU attribue le travail.'),
        ready: t('The assignee can start when all gates are satisfied.', 'Le responsable peut démarrer quand les conditions sont réunies.'),
        in_progress: t('The assignee can submit the work and evidence.', 'Le responsable peut soumettre le travail et ses preuves.'),
        blocked: t('Resolve the blocker before resuming.', 'Lever le blocage avant de reprendre.'),
        review: t('A separate architect reviews the technical evidence.', 'Un architecte distinct examine les preuves techniques.'),
        workbench: t('Handoff recorded. An authorized reviewer decides in the Workbench Companion.', 'Relais consigné. Une personne habilitée décide dans le Compagnon Workbench.'),
        done: t('The task is closed.', 'La tâche est clôturée.'),
        cancelled: t('The task was cancelled.', 'La tâche a été annulée.'),
      })[task.status];
    const list = (label, rows) => rows.length ? `<div><b>${label}</b><ul>${rows.map(row => `<li>${esc(row)}</li>`).join('')}</ul></div>` : '';
    return `<article class="portfolio-detail" data-portfolio-detail tabindex="-1">
      <div class="portfolio-caption"><h4>${esc(task.title)}</h4>${button('data-portfolio-action', 'clear', t('Close details', 'Fermer le détail'))}</div>
      <p class="portfolio-note">${esc(project?.title || '')} · ${esc(unit(task.unit))} · ${due(task.due_on)} · ${esc(ownerText(task))}</p>
      ${task.description ? `<p>${esc(task.description)}</p>` : ''}
      ${task.parent_task_id ? `<p class="portfolio-note">${t('Delegation', 'Délégation')} · ${l(DELEGATION, task.delegation)}${parent ? ` · ${esc(parent.title)}` : ''}</p>` : ''}
      ${task.assignee?.kind === 'agent' ? `<p class="portfolio-note">${task.tokens
        ? `${num.format(task.tokens.total)} ${tokensLabel(task)} · ${t('Input', 'Entrée')} ${num.format(task.tokens.input)} · ${t('Output', 'Sortie')} ${num.format(task.tokens.output)}`
        : t('Tokens not reported.', 'Tokens non renseignés.')}</p>` : ''}
      ${task.runner_started_at ? `<p class="portfolio-note">${t('Run started', 'Exécution démarrée')} ${esc(new Date(task.runner_started_at).toLocaleString(localeOf(language)))}
        ${task.runner_finished_at ? ` · ${t('Duration', 'Durée')} ${num.format(Math.max(0, Math.round((Date.parse(task.runner_finished_at) - Date.parse(task.runner_started_at)) / 1000)))} s` : ''}</p>` : ''}
      ${task.runner_result ? `<details><summary>${t('Local execution result', 'Résultat de l’exécution locale')}</summary><pre class="portfolio-result">${esc(task.runner_result)}</pre></details>` : ''}
      <p class="portfolio-note"><strong>${l(STATUS, task.status)}</strong> · ${l(EXECUTION, task.execution)}${task.autonomy ? ` · ${l(AUTONOMY, task.autonomy)}` : ''}
        ${roles.length ? ` · ${t('Your BU roles', 'Vos rôles dans la BU')} ${roles.join(', ')}` : ''}</p>
      <p><b>${t('Next action', 'Prochaine action')}</b> ${next}</p>
      ${task.workbench_decision && task.status !== 'workbench' ? `<p class="portfolio-note"><b>${t('Latest Workbench decision', 'Dernière décision Workbench')}</b> ·
        ${l(WORKBENCH_DECISION, task.workbench_decision.outcome)} · ${esc(new Intl.DateTimeFormat(localeOf(language),
          { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(task.workbench_decision.at)))}</p>` : ''}
      ${list(t('Blocking dependencies', 'Dépendances bloquantes'), task.dependency_ids.map(key => {
        const dependency = rowById(data.tasks, key);
        return dependency ? `${dependency.title} · ${l(STATUS, dependency.status)}` : t('Dependency outside your current view', 'Dépendance hors de votre vue actuelle');
      }))}
      ${list(t('Evidence', 'Preuves'), task.evidence.map(row => row.ref ? `${row.label} · ${row.ref}` : row.label))}
      ${task.history.length ? `<details><summary>${t('Task history', 'Historique de la tâche')}</summary>
        ${list(t('Recent changes', 'Dernières modifications'), task.history.map(row => `${l(ACTION, row.action)} · ${l(STATUS, row.status)} · ${row.actor || '—'} · r${row.revision}`))}</details>` : ''}
      <div class="portfolio-actions">${view.pending ? '' : commands(data, task, source === 'demo').map(action => button('data-portfolio-action', `command:${action}:${task.id}`,
        action === 'submit' && task.execution === 'sensitive' ? t('Prepare the handoff', 'Préparer le relais') : l(ACTION, action))).join('')}
        ${!view.pending && delegationOptions(data, task, source === 'demo').modes.length ? button('data-portfolio-action', `command:delegate:${task.id}`, l(ACTION, 'delegate')) : ''}
        ${!view.pending && role(data, task.unit, 'project_lead') && ['planned', 'ready'].includes(task.status) ? button('data-portfolio-action', `edit:tasks:${task.id}`, t('Replan', 'Replanifier')) : ''}
        ${task.status === 'workbench' ? `<a class="mini" href="./companion/index.html?lang=${language === 'fr' ? 'fr' : 'en'}" target="_blank" rel="noopener noreferrer">
          ${t('Open Companion', 'Ouvrir Companion')}</a>` : ''}</div></article>`;
  }

  window.FactoryPortfolio = Object.freeze({ parse, demo, commands, delegationOptions, leadUnits, role, applyDemo, render, mutation });
})();
