/* SPDX-License-Identifier: MIT */
(() => {
  'use strict';

  const categories = {
    security: ['Security', 'Sécurité'],
    automation: ['Automation', 'Automatisation'],
    data: ['Data & quality', 'Données & qualité'],
    experience: ['Experience', 'Expérience'],
    adoption: ['Learning & adoption', 'Formation & adoption'],
    uncategorized: ['To explore', 'À explorer'],
  };
  const tags = {
    access: ['Access', 'Accès'], compliance: ['Compliance', 'Conformité'],
    workflow: ['Workflow', 'Workflow'], integration: ['Integration', 'Intégration'],
    analytics: ['Analytics', 'Analyse'], quality: ['Quality', 'Qualité'],
    usability: ['Usability', 'Ergonomie'], feedback: ['Feedback', 'Retours'],
    training: ['Training', 'Formation'], documentation: ['Documentation', 'Documentation'],
  };
  const tagCategories = {
    access: 'security', compliance: 'security', workflow: 'automation', integration: 'automation',
    analytics: 'data', quality: 'data', usability: 'experience', feedback: 'experience',
    training: 'adoption', documentation: 'adoption',
  };
  // The static demo mirrors the keyword rules in api/app/ideas.py.
  const signals = {
    access: 'acces access authentification authentication identite identity permission permissions sso mfa',
    compliance: 'audit conformite compliance cyber rgpd gdpr confidentialite privacy securite security',
    workflow: 'automatiser automatisation automate automation processus process workflow orchestration',
    integration: 'api connecteur connecteurs connector connectors crm erp integration integrations',
    analytics: 'analyse analytics donnees data metrique metriques metrics reporting tableau dashboard',
    quality: 'qualite quality validation nettoyage cleaning doublon doublons duplicate duplicates',
    usability: 'accessibilite accessibility ergonomie interface navigation utilisateur users ux ui',
    feedback: 'avis feedback enquete survey satisfaction retour retours',
    training: 'adoption formation former onboarding training tutoriel tutoriels tutorial atelier ateliers workshop',
    documentation: 'catalogue catalog documentation guide guides savoir knowledge',
  };
  const storeKey = 'ai-factory-coffee-demo-v1';
  const demoUnit = 'coffee-demo';
  const id = value => typeof value === 'string' && /^[a-f0-9]{32}$/.test(value);
  const bounded = (value, max) => typeof value === 'string' && value.length <= max * 2
    && Array.from(value).length <= max && value.trim().length > 0;
  const key = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$/.test(value);
  const fold = value => value.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();
  const tokens = value => new Set(fold(value).match(/[a-z0-9]+/g) || []);
  const tx = (language, english, french) => language === 'fr' ? french : english;

  function idea(row) {
    return row && id(row.id) && key(row.bu) && bounded(row.title, 120) && bounded(row.text, 1000)
      && Object.hasOwn(categories, row.category) && Array.isArray(row.tags) && row.tags.length <= 10
      && row.tags.every(tag => Object.hasOwn(tags, tag)) && row.visibility === 'factory'
      && (row.bu_label === undefined || typeof row.bu_label === 'string')
      && bounded(row.created_at, 40) && Number.isFinite(Date.parse(row.created_at));
  }

  function parse(data) {
    const permissions = data?.permissions;
    if (!permissions || typeof permissions.create !== 'boolean'
      || typeof data.view !== 'string' || !/^[a-f0-9]{64}$/.test(data.view)
      || !Array.isArray(data.items) || data.items.length > 120 || !data.items.every(idea)
      || !Array.isArray(data.units)
      || !data.units.every(unit => unit && key(unit.id) && typeof unit.label === 'string')
      || typeof data.truncated !== 'boolean' || !Number.isSafeInteger(data.total) || data.total < data.items.length) return null;
    if (new Set(data.items.map(row => row.id)).size !== data.items.length
      || new Set(data.units.map(unit => unit.id)).size !== data.units.length) return null;
    return data;
  }

  function classify(title, text) {
    const head = tokens(title), body = tokens(text);
    const scores = new Map();
    for (const [tag, words] of Object.entries(signals)) {
      const weight = words.split(' ').reduce((score, word) => score + (head.has(word) ? 2 : 0) + (body.has(word) ? 1 : 0), 0);
      if (!weight) continue;
      const category = tagCategories[tag];
      if (!scores.has(category)) scores.set(category, { total: 0, tags: [] });
      const match = scores.get(category);
      match.total += weight; match.tags.push(tag);
    }
    const [category, match] = [...scores.entries()].sort((a, b) => b[1].total - a[1].total)[0] || ['uncategorized', { tags: [] }];
    return { category, tags: match.tags };
  }

  function demoNotes() {
    try {
      const raw = localStorage.getItem(storeKey) || '[]';
      if (raw.length > 120000) return [];
      const notes = JSON.parse(raw);
      return Array.isArray(notes) ? notes.slice(0, 40).filter(row => idea(row) && row.bu === demoUnit) : [];
    } catch { return []; }
  }

  function saveDemo(notes) {
    try { localStorage.setItem(storeKey, JSON.stringify(notes.slice(0, 40))); return true; }
    catch { return false; }
  }

  function demo(language, notes = demoNotes()) {
    const t = (en, fr) => tx(language, en, fr);
    const seeds = [
      ['automation', 'workflow', 'Automate incident summaries', 'Automatiser le résumé des incidents',
        'Prepare a short draft from the ticket so the on-call team can review it before handover.',
        'Préparer un brouillon à partir du ticket pour que l’astreinte puisse le relire avant la passation.'],
      ['adoption', 'training', 'A 20-minute AI workshop', 'Un atelier IA de 20 minutes',
        'Use a real example from the team and leave everyone with a short practice exercise.',
        'Partir d’un exemple de l’équipe et laisser à chacun un exercice court à refaire.'],
      ['data', 'quality', 'Improve the CRM source data', 'Fiabiliser les données du CRM',
        'Flag duplicates and incomplete fields before they reach the reporting dashboard.',
        'Signaler les doublons et les champs incomplets avant leur arrivée dans le tableau de bord.'],
    ];
    const items = seeds.map(([category, tag, en, fr, enText, frText], index) => ({
      id: String(index + 1).padStart(32, '0'), bu: demoUnit, title: t(en, fr), text: t(enText, frText),
      category, tags: [tag], visibility: 'factory',
      created_at: `2026-11-0${5 - index}T09:00:00Z`,
    }));
    return { items: [...notes, ...items], units: [{ id: demoUnit, label: t('Demo team', 'Équipe démo') }],
      permissions: { create: true }, truncated: false, total: notes.length + items.length };
  }

  function demoCreate(fields, notes) {
    const current = demoNotes();
    const currentIds = new Set(current.map(note => note.id));
    const saved = [...current, ...notes.filter(note => !currentIds.has(note.id))];
    const note = {
      id: Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join(''),
      bu: demoUnit, title: fields.title, text: fields.text, ...classify(fields.title, fields.text),
      visibility: 'factory', created_at: new Date().toISOString(),
    };
    const next = [note, ...saved].slice(0, 40);
    return { notes: next, saved: saveDemo(next), note };
  }

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text) element.textContent = text;
    return element;
  }

  function button(className, text) {
    const element = node('button', className, text);
    element.type = 'button';
    return element;
  }

  function create(language, actions) {
    const t = (en, fr) => tx(language, en, fr);
    const label = pair => t(...pair);
    const root = node('section', 'ideas');
    const hero = node('header', 'ideas-hero');
    const intro = node('div', 'ideas-intro');
    const mode = node('span', 'ideas-mode');
    const title = node('h3', '', t('The idea wall', 'Le mur des idées'));
    intro.append(node('span', 'ideas-kicker', t('Coffee corner', 'Coin café')), title,
      node('p', '', t('An improvement, a friction point, a hunch? Leave a note and let the board sort it.',
        'Une amélioration, une friction, une intuition ? Déposez un post-it, le tableau le classe.')));
    const add = button('ideas-primary', t('+ Add an idea', '+ Ajouter une idée'));
    add.setAttribute('aria-controls', 'ideas-compose');
    add.setAttribute('aria-expanded', 'false');
    const heroActions = node('div', 'ideas-hero-actions');
    heroActions.append(mode, add);
    hero.append(intro, heroActions);

    const composer = node('form', 'ideas-compose');
    composer.id = 'ideas-compose';
    const caption = node('h4', '', t('A fresh note', 'Un nouveau post-it'));
    const dismiss = button('ideas-quiet', t('Close form', 'Fermer le formulaire'));
    const composeHead = node('div', 'ideas-compose-head');
    composeHead.append(caption, dismiss);
    const name = node('input');
    name.id = 'idea-title'; name.name = 'title'; name.maxLength = 120; name.required = true;
    name.placeholder = t('Example: faster handovers', 'Exemple : des passations plus rapides');
    const body = node('textarea');
    body.id = 'idea-text'; body.name = 'text'; body.maxLength = 1000; body.required = true; body.rows = 3;
    body.placeholder = t('What could we improve, and for whom?', 'Qu’améliorer, et pour qui ?');
    const unit = node('select'); unit.id = 'idea-unit'; unit.name = 'bu'; unit.required = true;
    const field = (fieldLabel, control) => {
      const container = node('label', 'ideas-field');
      container.htmlFor = control.id;
      container.append(node('span', '', fieldLabel), control);
      return container;
    };
    const unitField = field(t('Business unit', 'BU'), unit);
    const submit = node('button', 'ideas-primary', t('Post my idea', 'Déposer mon idée'));
    submit.type = 'submit';
    const composeFoot = node('div', 'ideas-compose-foot');
    const sharing = node('p');
    composeFoot.append(sharing, submit);
    composer.append(composeHead, unitField, field(t('Title', 'Titre'), name),
      field(t('Idea', 'Idée'), body), composeFoot);

    const status = node('p', 'ideas-status'); status.setAttribute('role', 'status');
    const retry = button('ideas-quiet', t('Try again', 'Réessayer'));
    const search = node('input'); search.type = 'search'; search.id = 'ideas-search'; search.maxLength = 120;
    search.placeholder = t('Search notes', 'Rechercher un post-it');
    const searchField = field(t('Search', 'Recherche'), search);
    const filters = node('div', 'ideas-filters');
    filters.setAttribute('role', 'group'); filters.setAttribute('aria-label', t('Categories', 'Catégories'));
    const tools = node('div', 'ideas-tools'); tools.append(searchField, filters);
    const count = node('p', 'ideas-count'); count.setAttribute('aria-live', 'polite');
    const wall = node('div', 'ideas-wall');
    root.append(hero, composer, status, retry, tools, count, wall);

    let current = null, active = '', composing = false;
    const categoryButtons = new Map();
    for (const category of ['', ...Object.keys(categories)]) {
      const control = button('ideas-filter', category ? label(categories[category]) : t('All', 'Toutes'));
      control.dataset.ideaFilter = category || 'all';
      control.addEventListener('click', () => { active = category; paintWall(); });
      categoryButtons.set(category, control); filters.append(control);
    }

    const showComposer = show => {
      composing = show;
      composer.hidden = !show;
      add.setAttribute('aria-expanded', String(show));
      if (show) name.focus(); else add.focus();
    };
    add.addEventListener('click', () => showComposer(true));
    dismiss.addEventListener('click', () => showComposer(false));
    retry.addEventListener('click', actions.retry);
    search.addEventListener('input', paintWall);
    composer.addEventListener('submit', event => {
      event.preventDefault();
      if (!current?.data?.permissions.create || current.pending) return;
      const fields = { bu: unit.value, title: name.value.trim(), text: body.value.trim() };
      if (!bounded(fields.title, 120) || !bounded(fields.text, 1000)) {
        status.textContent = t('Give your idea a title and a short description.', 'Donnez un titre et une courte description à votre idée.');
        (fields.title ? body : name).focus();
        return;
      }
      actions.create(fields);
    });

    function card(row, units) {
      const note = node('article', `idea-note idea-note-${row.category}`);
      note.dataset.ideaId = row.id;
      const category = node('span', 'idea-category', label(categories[row.category]));
      const heading = node('h4', '', row.title);
      const content = node('p', 'idea-text', row.text);
      const list = node('ul', 'idea-tags');
      for (const tag of row.tags) list.append(node('li', '', label(tags[tag])));
      list.hidden = !row.tags.length;
      const date = node('time', '', new Intl.DateTimeFormat(language, { day: 'numeric', month: 'short', year: 'numeric' }).format(new Date(row.created_at)));
      date.dateTime = row.created_at;
      const meta = node('div', 'idea-meta');
      meta.append(node('span', 'idea-unit', row.bu_label || units.get(row.bu) || row.bu), date);
      note.append(category, heading, content, list, meta);
      return note;
    }

    function paintWall() {
      const data = current?.data;
      const items = data?.items || [];
      const unitLabels = new Map((data?.units || []).map(item => [item.id, item.label]));
      const query = fold(search.value.trim());
      const searched = query ? items.filter(row =>
        fold([row.title, row.text, label(categories[row.category]), ...row.tags.map(tag => label(tags[tag])),
          row.bu_label || unitLabels.get(row.bu) || row.bu].join(' ')).includes(query)) : items;
      const totals = new Map();
      for (const row of searched) totals.set(row.category, (totals.get(row.category) || 0) + 1);
      for (const [category, control] of categoryButtons) {
        control.setAttribute('aria-pressed', String(active === category));
        control.textContent = `${category ? label(categories[category]) : t('All', 'Toutes')} · ${category ? totals.get(category) || 0 : searched.length}`;
      }
      const shown = searched.filter(row => !active || row.category === active);
      const visibleCount = shown.length === 1 ? t('1 idea', '1 idée') : t(`${shown.length} ideas`, `${shown.length} idées`);
      count.textContent = !data ? '' : data.truncated ? t(`${visibleCount} · ${data.items.length} latest of ${data.total}`,
        `${visibleCount} · parmi les ${data.items.length} dernières sur ${data.total}`) : visibleCount;
      wall.replaceChildren();
      if (!data) return;
      if (shown.length) {
        wall.append(...shown.map(row => card(row, unitLabels)));
      } else {
        const empty = node('div', 'ideas-empty');
        empty.append(node('h4', '', query || active ? t('No matching notes', 'Aucun post-it correspondant') : t('The wall is clear', 'Le mur est encore vide')),
          node('p', '', query || active ? t('Try another search or category.', 'Essayez une autre recherche ou catégorie.')
            : t('Ideas will appear here once they are posted.', 'Les idées apparaîtront ici après leur dépôt.')));
        wall.append(empty);
      }
    }

    function update(view) {
      const previous = current;
      current = { ...view };
      const data = current.data;
      const canCreate = !!(data?.permissions.create && data.units.length);
      root.dataset.ideasState = view.status;
      mode.textContent = view.source === 'demo' ? t('Local demo', 'Démo locale') : t('Factory board', 'Mur de la Factory');
      sharing.textContent = view.source === 'demo' ? t('Demo note saved in this browser.', 'Post-it de démo enregistré dans ce navigateur.')
        : t('Visible to the whole Factory as soon as you post.', 'Visible par toute la Factory dès l’envoi.');
      add.hidden = !canCreate; add.disabled = !!view.pending;
      composer.hidden = !canCreate || !composing;
      const unitKey = JSON.stringify(data?.units || []);
      if (unit.dataset.options !== unitKey) {
        const selected = unit.value;
        unit.replaceChildren();
        for (const item of data?.units || []) unit.add(new Option(item.label || item.id, item.id));
        if (data?.units.length > 1) unit.prepend(new Option(t('Choose a business unit', 'Choisir une BU'), ''));
        if (data?.units.some(item => item.id === selected)) unit.value = selected;
        else if (data?.units.length > 1) unit.value = '';
        unit.dataset.options = unitKey;
      }
      unitField.hidden = !data || data.units.length <= 1;
      name.disabled = body.disabled = unit.disabled = submit.disabled = !!view.pending;
      if (view.completed && view.completed !== previous?.completed) {
        composer.reset(); active = ''; search.value = ''; showComposer(false);
      }
      const states = {
        loading: t('Loading the idea wall…', 'Chargement du mur des idées…'),
        denied: t('Access to this idea wall has expired or is restricted.', 'L’accès à ce mur a expiré ou est restreint.'),
        unavailable: t('The idea wall needs the protected workspace.', 'Ce mur d’idées nécessite l’espace protégé.'),
        error: t('The wall could not be loaded.', 'Impossible de charger le mur.'),
      };
      status.textContent = view.message || states[view.status] || (view.source === 'demo'
        ? t('Fictional examples. Your notes stay in this browser.', 'Exemples fictifs. Vos post-it restent dans ce navigateur.') : '');
      retry.hidden = view.source !== 'api' || !['denied', 'error'].includes(view.status) || !!view.pending;
      tools.hidden = !data; count.hidden = !data;
      if (previous?.data !== data || previous?.pending !== view.pending || previous?.completed !== view.completed) paintWall();
    }
    return { root, update };
  }

  window.FactoryIdeas = Object.freeze({ parse, idea, categoryName: (value, language) => tx(language, ...categories[value]),
    create, demo, demoNotes, demoCreate, storeKey });
})();
