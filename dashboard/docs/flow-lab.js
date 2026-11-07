(function () {
  'use strict';

  const sim = globalThis.FactoryFlowSim;
  const byId = id => document.getElementById(id);
  const english = new URLSearchParams(location.search).get('lang') === 'en';
  const ui = (fr, en) => english ? en : fr;
  const locale = ui('fr-FR', 'en-GB');
  if (english) {
    document.documentElement.lang = 'en';
    document.title = 'AI Flow Lab · AI Factory';
    document.querySelector('meta[name="description"]').content = 'Build AI topologies and simulate synthetic traffic, token counts and routing locally.';
    byId('dashboard-link').href = '../index.html?lang=en';
    for (const node of document.querySelectorAll('[data-en]')) node.textContent = node.dataset.en;
    for (const node of document.querySelectorAll('[data-en-label]')) node.setAttribute('aria-label', node.dataset.enLabel);
  }
  const element = (tag, text) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const svg = (tag, attrs = {}) => {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    return node;
  };

  const STORE = 'ai-factory.flow-lab.history.v1';
  const LABELS = {
    source: ['IN', 'Source', ui('Injecte un lot fictif et sa classification.', 'Injects a synthetic batch and its classification.')],
    policy: ['SEC', ui('Passerelle', 'Gateway'), ui('Vérifie la classification et le budget de tokens en entrée.', 'Checks data classification and the input token budget.')],
    router: ['RTE', ui('Routeur', 'Router'), ui('Choisit une liaison par défaut ou une liaison C3/C4, avec repli possible après une panne.', 'Chooses the default or C3/C4 route and can fall back after an outage.')],
    retrieval: ['CTX', ui('Recherche', 'Retrieval'), ui('Ajoute des tokens de contexte synthétiques.', 'Adds synthetic context tokens.')],
    cpu: ['CPU', ui('Modèle CPU', 'CPU model'), ui('Calcule des tokens et un coût fictifs sur un profil local CPU.', 'Estimates tokens and costs for a local CPU profile.')],
    gpu: ['GPU', ui('Modèle GPU', 'GPU model'), ui('Calcule des tokens et un coût fictifs sur un profil local GPU.', 'Estimates tokens and costs for a local GPU profile.')],
    remote: ['EXT', ui('Modèle distant', 'Remote model'), ui('Simule un profil distant. C3 et C4 sont toujours bloqués ici.', 'Simulates a remote profile. It always blocks C3 and C4 data.')],
    sink: ['OUT', 'Destination', ui('Compte les demandes parvenues en bout de trajet.', 'Counts requests that reach the end of the path.')],
  };
  const ROUTE_LABELS = { default: ui('Défaut', 'Default'), restricted: 'C3/C4', fallback: ui('Repli', 'Fallback') };
  const OUTCOMES = { delivered: ui('Lot livré', 'Batch delivered'), blocked: ui('Lot bloqué', 'Batch blocked'),
    failed: ui('Échec simulé', 'Simulated failure'), unrouted: ui('Trajet incomplet', 'Incomplete path') };
  const EVENTS = {
    forward: ui('Transfert', 'Forwarded'), delivered: ui('Lot reçu', 'Batch received'), policy_class: ui('Classification refusée', 'Classification denied'),
    policy_tokens: ui('Budget de tokens dépassé', 'Token budget exceeded'), policy_pass: ui('Contrôles passés', 'Checks passed'),
    restricted_route: ui('Liaison C3/C4', 'C3/C4 route'), default_route: ui('Liaison par défaut', 'Default route'), retrieved: ui('Contexte ajouté', 'Context added'),
    restricted_remote: ui('Accès distant refusé', 'Remote access denied'), unavailable: ui('Panne simulée', 'Simulated outage'),
    model_remote: ui('Inférence distante simulée', 'Simulated remote inference'), model_local: ui('Inférence locale simulée', 'Simulated local inference'),
  };
  const WARNINGS = {
    policy_class: ui('La passerelle bloque la classe injectée.', 'The gateway blocks this data class.'),
    policy_tokens: ui('Le nombre de tokens dépasse la limite de la passerelle.', 'The token count exceeds the gateway limit.'),
    restricted_remote: ui('La règle C3/C4 bloque le modèle distant avant toute tentative.', 'The C3/C4 policy blocks the remote model before any attempt.'),
    unavailable: ui('Un modèle indisponible ajoute du délai, sans tokens consommés.', 'An unavailable model adds delay and consumes zero tokens.'),
    fallback_used: ui('Le dernier routeur a activé son repli.', 'The last router used its fallback.'),
    unrouted: ui('Le trajet se termine sans atteindre une destination.', 'The path ends before reaching a destination.'),
    execution_limit: ui('La limite de parcours est atteinte.', 'The path reached its traversal limit.'),
  };
  const ERRORS = {
    invalid_graph: ui('La topologie dépasse les limites du laboratoire.', 'The topology exceeds the lab limits.'),
    invalid_node: ui('La configuration du nœud est invalide.', 'The node configuration is invalid.'),
    invalid_policy: ui('Vérifiez la classe et le budget de la passerelle.', 'Check the gateway class and token budget.'),
    invalid_retrieval: ui('Vérifiez les tokens de contexte.', 'Check the context token count.'),
    occupied_position: ui('Cette case est déjà occupée.', 'This cell is already occupied.'),
    single_source: ui('Le scénario utilise une seule source.', 'The scenario uses a single source.'),
    invalid_edge: ui('Choisissez deux nœuds distincts.', 'Choose two distinct nodes.'),
    invalid_route: ui('Cette liaison est incompatible avec les types de nœud.', 'This connection is incompatible with the node types.'),
    duplicate_route: ui('Une liaison de ce type existe déjà pour ce départ.', 'This node already has a route of that type.'),
    cycle: ui('Une boucle n’est pas autorisée dans ce graphe.', 'This graph cannot contain cycles.'),
    invalid_scenario: ui('Vérifiez les bornes des paramètres du scénario.', 'Check the scenario parameter limits.'),
    missing_source: ui('Ajoutez une source pour injecter le lot.', 'Add a source to inject the batch.'),
    missing_sink: ui('Ajoutez une destination pour observer la livraison.', 'Add a destination to observe delivery.'),
  };
  const PRESETS = {
    nominal: { ...sim.DEFAULT_SCENARIO },
    restricted: { ...sim.DEFAULT_SCENARIO, classification: 4 },
    fault: { ...sim.DEFAULT_SCENARIO, fault: 'remote' },
  };
  const number = new Intl.NumberFormat(locale);
  const money = new Intl.NumberFormat(locale, { style: 'currency', currency: 'EUR', maximumFractionDigits: 6 });
  const date = new Intl.DateTimeFormat(locale, { dateStyle: 'short', timeStyle: 'medium' });
  const directionLabels = [[ui('Haut', 'Up'), 0, -1], [ui('Gauche', 'Left'), -1, 0], [ui('Droite', 'Right'), 1, 0], [ui('Bas', 'Down'), 0, 1]];
  const scenarioFields = {
    classification: byId('classification'), requests: byId('requests'), input: byId('input-tokens'),
    output: byId('output-tokens'), fault: byId('fault'),
  };

  let graph = sim.template();
  let selected = 3;
  let result = null;
  let comparisonIndex = null;
  let history = [];
  const memoryOnly = ui('Le stockage du navigateur est indisponible. L’historique reste en mémoire pour cette session.',
    'Browser storage is unavailable. History stays in memory for this session.');
  try { history = sim.readHistory(localStorage.getItem(STORE)); } catch { status('storage-status', memoryOnly, true); }

  function status(id, message, error = false) {
    const box = byId(id);
    box.textContent = message;
    box.dataset.error = String(error);
  }
  function showError(error, id) {
    status(id, ERRORS[error?.code] || ui('L’opération est indisponible pour cette configuration.', 'This operation is unavailable for the current configuration.'), true);
  }
  function nodeLabel(node) { return LABELS[node.type][1] + ' · ' + node.id; }
  function selectedNode() { return graph.nodes.find(node => node.id === selected); }
  function button(label, action, className) {
    const control = element('button', label);
    control.type = 'button';
    if (className) control.className = className;
    control.addEventListener('click', action);
    return control;
  }
  function option(value, label) {
    const item = element('option', label);
    item.value = value;
    return item;
  }
  function fillSelect(control, items, previous) {
    control.replaceChildren(...items.map(([value, label]) => option(value, label)));
    if (items.some(([value]) => String(value) === String(previous))) control.value = previous;
    control.disabled = !items.length;
  }

  function free(x, y, except) {
    return x >= 0 && x <= 4 && y >= 0 && y <= 3 &&
      !graph.nodes.some(node => node.id !== except && node.x === x && node.y === y);
  }

  function setGraph(next, message, focusSelector) {
    try {
      sim.validateGraph(next);
      graph = next;
      clearResult();
      renderEditor();
      if (focusSelector) document.querySelector(focusSelector)?.focus();
      status('editor-status', message);
    } catch (error) { showError(error, 'editor-status'); }
  }

  function addNode(type) {
    let cell;
    for (let y = 0; y <= 3 && !cell; y++) {
      for (let x = 0; x <= 4 && !cell; x++) if (free(x, y)) cell = { x, y };
    }
    if (!cell) return status('editor-status', ui('Le canevas est plein.', 'The canvas is full.'), true);
    const id = Array.from({ length: sim.MAX_NODES }, (_, index) => index + 1)
      .find(candidate => !graph.nodes.some(node => node.id === candidate));
    const node = sim.makeNode(type, id, cell.x, cell.y);
    selected = id;
    setGraph({ ...graph, nodes: [...graph.nodes, node] }, ui(nodeLabel(node) + ' ajouté.', 'Added ' + nodeLabel(node) + '.'));
  }

  const palette = new Map();
  for (const type of sim.TYPES) {
    const control = button('', () => addNode(type), 'palette-button');
    const glyph = element('span', LABELS[type][0]);
    glyph.className = 'glyph';
    glyph.setAttribute('aria-hidden', 'true');
    control.append(glyph, element('span', LABELS[type][1]));
    control.setAttribute('aria-label', ui('Ajouter ', 'Add ') + LABELS[type][1]);
    palette.set(type, control);
    byId('palette').append(control);
  }

  function selectNode(id) {
    selected = id;
    for (const control of byId('nodes').children) control.setAttribute('aria-pressed', String(Number(control.dataset.node) === id));
    renderInspector();
    renderEdgeOptions(id);
  }

  function moveNode(node, dx, dy, moveId) {
    const next = { ...node, x: node.x + dx, y: node.y + dy };
    if (!free(next.x, next.y, node.id)) return status('editor-status', ui('Cette case est indisponible.', 'This cell is unavailable.'), true);
    setGraph({ ...graph, nodes: graph.nodes.map(entry => entry.id === node.id ? next : entry) },
      ui(nodeLabel(node) + ' déplacé.', 'Moved ' + nodeLabel(node) + '.'), '#inspector [data-move="' + moveId + '"]:not(:disabled), #inspector-title');
  }

  function renderInspector() {
    const panel = byId('inspector');
    const node = selectedNode();
    panel.replaceChildren();
    if (!node) {
      panel.append(element('p', ui('Sélectionnez un nœud dans le canevas.', 'Select a node on the canvas.')));
      return;
    }
    const name = element('strong', nodeLabel(node));
    name.className = 'inspect-name';
    panel.append(name, element('p', LABELS[node.type][2]));
    const arrows = element('div');
    arrows.className = 'nudge';
    directionLabels.forEach(([label, dx, dy], index) => {
      const control = button(label, () => moveNode(node, dx, dy, index));
      control.dataset.move = index;
      control.disabled = !free(node.x + dx, node.y + dy, node.id);
      control.setAttribute('aria-label', ui('Déplacer vers ', 'Move ') + label.toLocaleLowerCase(locale));
      arrows.append(control);
    });
    panel.append(arrows);
    const fields = node.type === 'policy' ? [['classMax', ui('Classe maximale', 'Maximum classification'), 1, 4], ['promptMax', ui('Tokens maximum', 'Maximum tokens'), 1, 16384]] :
      node.type === 'retrieval' ? [['contextTokens', ui('Tokens de contexte', 'Context tokens'), 0, 4096]] : [];
    if (fields.length) {
      const form = element('form');
      form.className = 'config-form';
      const inputs = new Map();
      for (const [key, text, minimum, maximum] of fields) {
        const label = element('label', text);
        const input = element('input');
        input.type = 'number';
        input.min = minimum;
        input.max = maximum;
        input.required = true;
        input.value = node[key];
        inputs.set(key, input);
        label.append(input);
        form.append(label);
      }
      const submit = element('button', ui('Appliquer', 'Apply'));
      submit.type = 'submit';
      form.append(submit);
      form.addEventListener('submit', event => {
        event.preventDefault();
        const next = { ...node };
        for (const [key, input] of inputs) next[key] = input.valueAsNumber;
        setGraph({ ...graph, nodes: graph.nodes.map(entry => entry.id === node.id ? next : entry) },
          ui('Paramètres appliqués.', 'Settings applied.'), '#inspector .config-form button');
      });
      panel.append(form);
    }
    panel.append(button(ui('Retirer le nœud', 'Remove node'), () => {
      const next = { nodes: graph.nodes.filter(entry => entry.id !== node.id),
        edges: graph.edges.filter(edge => edge.from !== node.id && edge.to !== node.id) };
      selected = next.nodes[0]?.id ?? null;
      setGraph(next, ui(nodeLabel(node) + ' retiré avec ses liaisons.', 'Removed ' + nodeLabel(node) + ' and its connections.'), '#inspector-title');
    }, 'remove'));
  }

  function drawDiagram() {
    const paths = byId('paths');
    const defs = svg('defs');
    for (const style of [...sim.ROUTES, 'taken']) {
      const marker = svg('marker', { id: 'head-' + style, viewBox: '0 0 10 10', refX: 9, refY: 5,
        markerWidth: 7, markerHeight: 7, orient: 'auto' });
      marker.append(svg('path', { d: 'M0 0 L10 5 L0 10 Z', class: 'head-' + style }));
      defs.append(marker);
    }
    paths.replaceChildren(defs);
    const map = new Map(graph.nodes.map(node => [node.id, node]));
    const touched = new Set(result?.steps.map(step => step.id) || []);
    const used = new Set(result?.followed.map(edge => edge.from + ':' + edge.route) || []);
    for (const edge of graph.edges) {
      const from = map.get(edge.from);
      const to = map.get(edge.to);
      const offset = edge.route === 'fallback' ? 13 : edge.route === 'restricted' ? -6 : 0;
      let x1 = from.x * 200 + 100, y1 = from.y * 110 + 55 + offset;
      let x2 = to.x * 200 + 100, y2 = to.y * 110 + 55 + offset;
      let d;
      if (from.x === to.x) {
        const sign = Math.sign(y2 - y1);
        y1 += sign * 34;
        y2 -= sign * 34;
        x1 += offset;
        x2 += offset;
        d = ['M', x1, y1, 'L', x2, y2].join(' ');
      } else {
        const sign = Math.sign(x2 - x1);
        x1 += sign * 74;
        x2 -= sign * 74;
        const mid = (x1 + x2) / 2;
        d = ['M', x1, y1, 'C', mid, y1, mid, y2, x2, y2].join(' ');
      }
      const active = used.has(edge.from + ':' + edge.route);
      paths.append(svg('path', { d, class: 'wire ' + edge.route + (active ? ' taken' : ''),
        'marker-end': 'url(#head-' + (active ? 'taken' : edge.route) + ')' }));
    }

    const nodes = byId('nodes');
    nodes.replaceChildren();
    for (const node of [...graph.nodes].sort((a, b) => a.y - b.y || a.x - b.x)) {
      const control = button('', () => selectNode(node.id), 'node');
      control.dataset.node = node.id;
      control.dataset.col = node.x;
      control.dataset.row = node.y;
      control.dataset.traversed = String(touched.has(node.id));
      control.setAttribute('aria-label', nodeLabel(node));
      control.setAttribute('aria-pressed', String(node.id === selected));
      control.append(element('span', LABELS[node.type][1]), element('small', '#' + node.id + ' / ' + LABELS[node.type][0]));
      nodes.append(control);
    }
  }

  const edgeFrom = byId('edge-from');
  const edgeTo = byId('edge-to');
  const edgeRoute = byId('edge-route');
  const edgeSubmit = byId('edge-form').querySelector('button');

  function renderEdgeOptions(preferredFrom) {
    const existingRoute = edgeRoute.value;
    const existingTo = edgeTo.value;
    fillSelect(edgeFrom, graph.nodes.filter(node => node.type !== 'sink').map(node => [node.id, nodeLabel(node)]),
      preferredFrom ?? edgeFrom.value);
    const from = graph.nodes.find(node => node.id === Number(edgeFrom.value));
    const choices = from?.type === 'router' ? sim.ROUTES : ['default'];
    const routes = choices.filter(route => from && !graph.edges.some(edge => edge.from === from.id && edge.route === route));
    fillSelect(edgeRoute, routes.map(route => [route, ROUTE_LABELS[route]]), existingRoute);
    const targets = graph.nodes.filter(node => from && node.id !== from.id && node.type !== 'source' &&
      (edgeRoute.value !== 'fallback' || Object.hasOwn(sim.PROFILES, node.type)));
    fillSelect(edgeTo, targets.map(node => [node.id, nodeLabel(node)]), existingTo);
    edgeSubmit.disabled = !from || !targets.length || !routes.length;
  }

  function renderEditor() {
    drawDiagram();
    renderInspector();
    renderEdgeOptions();
    const full = graph.nodes.length >= sim.MAX_NODES;
    for (const [type, control] of palette) control.disabled = full || type === 'source' && graph.nodes.some(node => node.type === 'source');
    byId('topology-count').textContent = graph.nodes.length + ui(' nœud(s) · ', ' nodes · ') + graph.edges.length + ui(' liaison(s)', ' connections');
    const list = byId('edge-list');
    list.replaceChildren();
    byId('edge-summary').textContent = ui('Liaisons configurées · ', 'Configured connections · ') + graph.edges.length;
    for (const edge of graph.edges) {
      const row = element('li');
      row.append(element('span', '#' + edge.from + ' → #' + edge.to + ' · ' + ROUTE_LABELS[edge.route]));
      const remove = button(ui('Retirer', 'Remove'), () => setGraph({ ...graph, edges: graph.edges.filter(entry => entry !== edge) },
        ui('Liaison retirée.', 'Connection removed.'), '#edge-summary'));
      remove.setAttribute('aria-label', ui('Retirer liaison #', 'Remove connection #') + edge.from + ui(' vers #', ' to #') + edge.to + ', ' + ROUTE_LABELS[edge.route]);
      row.append(remove);
      list.append(row);
    }
  }

  edgeFrom.addEventListener('change', () => renderEdgeOptions());
  edgeRoute.addEventListener('change', () => renderEdgeOptions());
  byId('edge-form').addEventListener('submit', event => {
    event.preventDefault();
    const edge = { from: Number(edgeFrom.value), to: Number(edgeTo.value), route: edgeRoute.value };
    setGraph({ ...graph, edges: [...graph.edges, edge] }, ui('Liaison ajoutée.', 'Connection added.'), '#edge-from');
  });

  function clearResult() {
    result = null;
    byId('results').hidden = true;
    drawDiagram();
    renderComparison();
    status('run-status', ui('Relancez la simulation pour actualiser les mesures.', 'Run the simulation again to update the metrics.'));
  }

  function setScenario(scenario) {
    for (const [key, control] of Object.entries(scenarioFields)) control.value = scenario[key];
  }

  function getScenario() {
    return {
      classification: Number(scenarioFields.classification.value), requests: scenarioFields.requests.valueAsNumber,
      input: scenarioFields.input.valueAsNumber, output: scenarioFields.output.valueAsNumber, fault: scenarioFields.fault.value,
    };
  }

  byId('reset-topology').addEventListener('click', () => {
    selected = 3;
    setScenario(sim.DEFAULT_SCENARIO);
    setGraph(sim.template(), ui('Topologie d’exemple chargée.', 'Example topology loaded.'));
  });
  document.querySelectorAll('[data-preset]').forEach(control => control.addEventListener('click', () => {
    setScenario(PRESETS[control.dataset.preset]);
    clearResult();
    status('run-status', ui('Scénario prêt. Lancez le lot sur la topologie courante.', 'Scenario ready. Run the batch on the current topology.'));
  }));
  byId('scenario-form').addEventListener('input', clearResult);

  function renderResult(next) {
    result = next;
    byId('results').hidden = false;
    const outcome = byId('outcome');
    outcome.textContent = OUTCOMES[result.outcome];
    outcome.dataset.outcome = result.outcome;
    const values = [
      [ui('Tokens totaux', 'Total tokens'), number.format(result.tokens)], [ui('Demandes livrées', 'Delivered requests'), number.format(result.delivered)],
      [ui('Demandes bloquées', 'Blocked requests'), number.format(result.blocked)], [ui('Tentatives modèle', 'Model attempts'), number.format(result.attempts)],
      [ui('Latence / demande', 'Latency / request'), number.format(result.latencyMs) + ' ms'], [ui('Coût du lot', 'Batch cost'), money.format(result.eur)],
    ];
    byId('metric-list').replaceChildren(...values.map(([label, value]) => {
      const cell = element('div');
      cell.append(element('dt', label), element('dd', value));
      return cell;
    }));
    byId('model-split').textContent = [
      number.format(result.input) + ui(' entrée', ' input'), number.format(result.output) + ui(' sortie', ' output'),
      number.format(result.local) + ui(' inférence(s) locale(s)', ' local inferences'), number.format(result.remote) + ui(' distante(s)', ' remote'),
      number.format(result.failed) + ui(' demande(s) en échec', ' failed requests'),
    ].join(' · ');
    byId('steps').replaceChildren(...result.steps.map(step => {
      const row = element('li');
      row.append(element('strong', nodeLabel(step)), document.createTextNode(' · ' + EVENTS[step.event]));
      if (step.tokens !== undefined) row.append(document.createTextNode(' · ' + number.format(step.tokens) + ' tokens'));
      return row;
    }));
    byId('warnings').replaceChildren(...result.warnings.map(code => element('li', WARNINGS[code])));
    drawDiagram();
    renderComparison();
  }

  function scenarioLabel(scenario) {
    const fault = scenario.fault === 'none' ? '' : ui(' · panne ', ' · outage at ') + LABELS[scenario.fault][1];
    return 'C' + scenario.classification + ' · ' + number.format(scenario.requests) + ui(' demande(s)', ' requests') + fault;
  }

  function persist() {
    try {
      localStorage.setItem(STORE, JSON.stringify({ version: sim.VERSION, entries: history }));
    } catch {
      status('storage-status', memoryOnly, true);
    }
  }

  const historyPick = byId('history-pick');
  const replay = byId('replay');

  function renderHistory() {
    historyPick.replaceChildren(option('', history.length ? ui('Choisir un point de comparaison', 'Choose a baseline') : ui('Aucune simulation enregistrée', 'No saved simulations')));
    const list = byId('history-list');
    list.replaceChildren();
    history.forEach((entry, index) => {
      const measured = sim.run(entry.graph, entry.scenario);
      const label = date.format(entry.timestamp) + ' · ' + scenarioLabel(entry.scenario);
      historyPick.append(option(index, label));
      list.append(element('li', label + ' · ' + number.format(measured.tokens) + ' tokens · ' + OUTCOMES[measured.outcome]));
    });
    historyPick.value = comparisonIndex === null ? '' : comparisonIndex;
    historyPick.disabled = !history.length;
    replay.disabled = comparisonIndex === null;
    renderComparison();
  }

  function renderComparison() {
    const baseline = comparisonIndex === null ? null : history[comparisonIndex];
    const target = byId('comparison');
    if (!baseline) {
      target.textContent = '';
      return;
    }
    const before = sim.run(baseline.graph, baseline.scenario);
    if (!result) {
      target.textContent = ui('Référence sélectionnée. Simulez le lot courant pour comparer.', 'Baseline selected. Simulate the current batch to compare.');
      return;
    }
    const delta = (after, prior, format) => (after > prior ? '+' : '') + format(after - prior);
    target.textContent = [
      ui('Lot courant comparé à la référence', 'Current batch compared with the baseline'),
      delta(result.tokens, before.tokens, value => number.format(value)) + ' tokens',
      delta(result.latencyMs, before.latencyMs, value => number.format(value)) + ui(' ms / demande', ' ms / request'),
      delta(result.eur, before.eur, value => money.format(value)) + ui(' de coût', ' in cost'),
      delta(result.blocked, before.blocked, value => number.format(value)) + ui(' demandes bloquées', ' blocked requests'),
    ].join(' · ');
  }

  historyPick.addEventListener('change', () => {
    comparisonIndex = historyPick.value === '' ? null : Number(historyPick.value);
    replay.disabled = comparisonIndex === null;
    renderComparison();
  });

  replay.addEventListener('click', () => {
    const entry = comparisonIndex === null ? null : history[comparisonIndex];
    if (!entry) return;
    graph = sim.snapshot(entry.graph, entry.scenario, entry.timestamp).graph;
    selected = graph.nodes[0]?.id ?? null;
    setScenario(entry.scenario);
    renderEditor();
    renderResult(sim.run(graph, entry.scenario));
    status('run-status', ui('Simulation rejouée. L’historique reste inchangé.', 'Simulation replayed. History was kept.'));
    byId('injection').scrollIntoView({ block: 'start' });
  });

  byId('scenario-form').addEventListener('submit', event => {
    event.preventDefault();
    const scenario = getScenario();
    try {
      const next = sim.run(graph, scenario);
      history = [sim.snapshot(graph, scenario, Date.now()), ...history].slice(0, sim.MAX_HISTORY);
      comparisonIndex = history.length > 1 ? Math.min((comparisonIndex ?? 0) + 1, history.length - 1) : null;
      persist();
      renderResult(next);
      renderHistory();
      status('run-status', ui('Simulation terminée · ', 'Simulation complete · ') + OUTCOMES[next.outcome] + '.');
    } catch (error) { showError(error, 'run-status'); }
  });

  renderEditor();
  renderResult(sim.run(graph, sim.DEFAULT_SCENARIO));
  renderHistory();
  status('run-status', ui('Résultats du scénario d’exemple. Lancez un lot pour l’ajouter à l’historique.', 'Example results. Run a batch to add it to your history.'));
})();
