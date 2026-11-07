/* SPDX-License-Identifier: MIT */
(() => {
  'use strict';

  const types = {
    draft_task: ['Draft a task', 'Rédiger une tâche'],
    explain_process: ['Understand the workflow', 'Comprendre le parcours'],
    decision_outline: ['Prepare a decision', 'Préparer une décision'],
  };
  const sources = { public: ['Public web', 'Web public'], internal: ['Internal sources', 'Sources internes'] };
  const tr = (language, en, fr) => language === 'fr' ? fr : en;
  const limit = (value, maximum) => typeof value === 'string' && value.length <= maximum * 2
    && Array.from(value).length <= maximum && !!value.trim();
  const id = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$/.test(value);
  const validType = kind => Object.hasOwn(types, kind);
  const validSource = source => Object.hasOwn(sources, source);
  const hasView = (data, viewer) => typeof viewer === 'string' && /^[a-f0-9]{64}$/.test(viewer) && data?.view === viewer;
  function parseConfig(data, viewer) {
    if (!hasView(data, viewer) || !Array.isArray(data.units) || !data.units.every(unit =>
      unit && id(unit.id) && typeof unit.label === 'string') || new Set(data.units.map(unit => unit.id)).size !== data.units.length) return null;
    return data.units;
  }
  function parseResult(data, viewer) {
    const usage = data?.usage;
    if (!hasView(data, viewer) || !limit(data.text, 4000) || usage?.source !== 'provider_reported'
      || !['input', 'output', 'total'].every(key => Number.isSafeInteger(usage[key]) && usage[key] >= 0)
      || usage.input + usage.output !== usage.total) return null;
    return { text: data.text, tokens: usage.total };
  }
  function steps(language) {
    return language === 'fr'
      ? ['Relier la tâche à un objectif et à un projet', 'Planifier dans la BU et attribuer le travail', 'Soumettre les preuves et les résultats', 'Vérifier ou transmettre au Workbench selon la politique']
      : ['Link the task to an objective and project', 'Plan within the BU and assign the work', 'Submit evidence and outcomes', 'Review or hand off to Workbench as policy requires'];
  }
  function template(request, language) {
    const t = (en, fr) => tr(language, en, fr);
    if (request.kind === 'draft_task') return t(
      `Objective — ${request.title}\nDeliverable — Describe the result to hand over.\nVerification — List the evidence and acceptance criteria.`,
      `Objectif — ${request.title}\nLivrable — Préciser le résultat à transmettre.\nVérification — Lister les preuves et les critères d’acceptation.`);
    if (request.kind === 'explain_process') return steps(language).map((step, index) => `${index + 1}. ${step}`).join('\n');
    const evidence = request.source === 'public'
      ? t('Gather official publications and dated, independent comparisons.', 'Recueillir des publications officielles et des comparatifs indépendants datés.')
      : t('Gather approved procedures, internal indicators and prior decisions.', 'Recueillir les procédures autorisées, les indicateurs internes et les décisions précédentes.');
    return t(
      `Hypotheses to verify\n• Identify the expected gain and what could disprove it.\n\nQuestions\n• Who owns the decision? What are the thresholds, risks and alternatives?\n\nSources to gather\n• ${evidence}`,
      `Hypothèses à vérifier\n• Identifier le gain attendu et ce qui pourrait l’infirmer.\n\nQuestions\n• Qui décide ? Quels sont les seuils, les risques et les alternatives ?\n\nSources à recueillir\n• ${evidence}`);
  }
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }
  function button(text) {
    const node = el('button', 'mini', text);
    node.type = 'button';
    return node;
  }
  function field(label, control) {
    const node = el('label', 'assist-field');
    node.append(el('span', '', label), control);
    return node;
  }

  function create(kind, language, actions) {
    const t = (en, fr) => tr(language, en, fr);
    const decision = kind === 'decision_outline', draft = kind === 'draft_task';
    const section = el('section', 'assist-surface');
    section.setAttribute('aria-labelledby', 'assistTitle'); section.setAttribute('role', 'region');
    const heading = el('h3', '', t(...types[kind])); heading.id = 'assistTitle'; heading.tabIndex = -1;
    const close = button(t('Close assistance', 'Fermer l’assistance'));
    close.addEventListener('click', actions.close);
    const header = el('header', 'assist-head'); header.append(heading, close);
    const mode = el('p', 'assist-mode');
    section.append(header, mode);
    if (kind === 'explain_process') {
      const process = el('ol', 'assist-steps');
      for (const step of steps(language)) process.append(el('li', '', step));
      section.append(process);
    }
    const form = el('form', 'assist-form');
    form.autocomplete = 'off';
    const title = el('input'); title.name = 'title'; title.maxLength = 120; title.required = true;
    const details = el('textarea'); details.name = 'text'; details.maxLength = 2000; details.rows = 3; details.required = decision;
    const unit = el('select'); unit.name = 'bu'; unit.required = true;
    const unitField = field(t('Business unit', 'BU'), unit);
    const sourceFields = [];
    if (draft) form.append(el('p', 'assist-note', t('The current title and description are used only when you ask for a suggestion. The task stays editable before saving.',
      'L’intitulé et la description actuels sont utilisés uniquement à votre demande. La tâche reste modifiable avant l’enregistrement.')));
    else {
      form.append(unitField);
      if (decision) {
        form.append(field(t('Decision to prepare', 'Décision à préparer'), title));
        const sourceGroup = el('fieldset', 'assist-sources');
        sourceGroup.append(el('legend', '', t('Sources to consider for this analysis', 'Sources à envisager pour cette analyse')));
        for (const [value, names] of Object.entries(sources)) {
          const input = el('input'); input.type = 'radio'; input.name = 'source'; input.value = value; input.required = true;
          const label = el('label'); label.append(input, document.createTextNode(t(...names))); sourceGroup.append(label);
          sourceFields.push(input);
        }
        form.append(sourceGroup, el('p', 'assist-note', t('This prepares a research brief. No sources are retrieved or verified in this interface.',
          'Cette analyse prépare un brief. Cette interface ne consulte ni ne vérifie de sources en direct.')));
      }
      form.append(field(decision ? t('Context and sourced evidence', 'Contexte et éléments sourcés') : t('Point to clarify', 'Point à éclaircir'), details));
      if (decision) form.append(el('p', 'assist-note', t('For facts already gathered, include the source title and date.',
        'Pour les faits déjà recueillis, indiquez le titre et la date de la source.')));
    }
    const generate = button(draft ? t('Generate a suggestion', 'Générer une suggestion')
      : decision ? t('Prepare the brief', 'Préparer le brief') : t('Explain with assistance', 'Demander une explication'));
    generate.type = 'submit'; form.append(generate);
    form.addEventListener('submit', event => {
      event.preventDefault();
      const source = decision ? sourceFields.find(input => input.checked)?.value : 'internal';
      if (decision && !source) return;
      if (actions.generate({ bu: unit.value, title: title.value.trim(), text: details.value.trim(), source }) && decision) {
        for (const input of sourceFields) input.checked = false;
      }
    });
    form.addEventListener('input', actions.discard);
    section.append(form);
    const status = el('p', 'assist-status'); status.setAttribute('role', 'status');
    const result = el('div', 'assist-result');
    const resultTitle = el('h4', '', draft ? t('Suggested description', 'Description proposée')
      : decision ? t('Research brief', 'Brief de recherche') : t('Suggested explanation', 'Explication proposée'));
    const provenance = el('p', 'assist-note');
    const output = el('p', 'assist-output');
    const apply = button(t('Use', 'Utiliser'));
    apply.addEventListener('click', actions.apply);
    result.append(resultTitle, provenance, output);
    if (draft) result.append(apply, el('p', 'assist-note', t('Appends the suggestion to the description. Saving stays in your hands.',
      'Ajoute la suggestion à la description. Vous gardez la main sur l’enregistrement.')));
    section.append(status, result);

    let priorUnits;
    function update(view) {
      mode.textContent = view.source === 'demo' ? t('Offline structured template', 'Trame structurée hors ligne')
        : view.status === 'ready' ? t('Authorized local model', 'Modèle local autorisé')
          : t('Local assistance', 'Assistance locale');
      if (priorUnits !== view.units && !draft) {
        const selected = unit.value;
        unit.replaceChildren();
        if (view.units.length > 1) unit.add(new Option(t('Choose a business unit', 'Choisir une BU'), ''));
        for (const row of view.units) unit.add(new Option(row.label || row.id, row.id));
        if (view.units.some(row => row.id === selected)) unit.value = selected;
        unitField.hidden = view.units.length === 1;
        priorUnits = view.units;
      }
      form.hidden = view.status !== 'ready';
      generate.disabled = !!view.pending;
      apply.disabled = !!view.pending;
      status.textContent = view.message || (view.pending ? t('Preparing the suggestion…', 'Préparation de la suggestion…') : '');
      result.hidden = !view.result;
      output.textContent = view.result?.text || '';
      provenance.textContent = view.result ? [
        view.source === 'demo' ? t('Offline template', 'Trame hors ligne') : t('Local suggestion to review', 'Suggestion locale à relire'),
        decision ? t(...sources[view.result.source]) : '',
        Number.isSafeInteger(view.result.tokens) ? t(`${view.result.tokens} measured tokens`, `${view.result.tokens} tokens mesurés`) : '',
      ].filter(Boolean).join(' · ') : '';
    }
    return { section, heading, update };
  }
  window.FactoryAssist = Object.freeze({ create, validType, validSource, limit, parseConfig, parseResult, template });
})();
