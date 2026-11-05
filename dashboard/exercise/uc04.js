// SPDX-License-Identifier: MIT
// Shared, offline tabletop exercise. All evidence and decisions are local to this scenario.
(() => {
  'use strict';

  const scenario = {
    id: 'UC-04', classification: 'C3', baseline: '2026-05', candidate: '2026-10',
    scores: [{ key: 'faithfulness', score: 0.916, threshold: 0.90 }, { key: 'redteam', score: 0.995, threshold: 0.99 }],
  };
  const sections = ['registry', 'pipeline', 'placement'];
  const copy = {
    en: {
      workshop: 'Release workshop', badge: 'Fictional exercise', title: 'Would you release UC-04?',
      intro: 'Run a 15-minute decision exercise with business, risk and platform. Work through the file and leave with a decision you can explain.',
      caseName: 'Job application pre-screening', candidate: 'Candidate model', baseline: 'Last reviewed model',
      sensitivity: 'Data class', language: 'Workshop language', steps: 'Workshop stages', dashboard: 'Open the 3D factory',
      close: 'Close workshop', inspect: 'Inspect in the 3D console', previous: 'Back', reset: 'Restart exercise',
      roles: ['Business', 'Risk', 'Platform', 'Committee'],
      stageNames: ['Release request', 'Evidence review', 'GPU outage', 'Decision record'],
      requestTitle: 'Open the release request',
      requestBody: 'Recruiting wants to put a new model into service for pre-screening job applications. A human reviews each recommendation. The candidate release changes the model version and handles personal data on the local GPU.',
      requestPrompt: 'Ask the business owner to state the intended use and confirm who will review each recommendation.',
      requestAction: 'Request release',
      riskTitle: 'Read the evaluation and risk file',
      riskBody: 'The candidate clears both sample thresholds. The risk review on file covers the previous model. The candidate still needs its own assessment, including bias testing.',
      measure: 'Measure', threshold: 'Threshold', score: 'Candidate score', evidence: 'Sample evaluation results',
      faithfulness: 'Faithfulness', redteam: 'Red-team pass rate', riskStatus: 'Candidate risk review pending',
      riskAction: 'Record the review gap',
      platformTitle: 'Take the local GPU offline',
      platformBody: 'Trigger a simulated outage to test what happens to a C3 request. In this exercise, the only eligible destination is the local GPU.',
      local: 'Local GPU', vps: 'VPS', cloud: 'Cloud', available: 'Available', unavailable: 'Unavailable',
      ineligible: 'Ineligible for C3', outageAction: 'Simulate GPU outage', restore: 'Restore simulated GPU',
      outageObserved: 'Outage injected during exercise', outageStatus: 'Request blocked. No external fallback.',
      normalStatus: 'Local route eligible. Risk review still pending.', platformAction: 'Take evidence to committee',
      decisionTitle: 'Record the committee decision',
      decisionBody: 'The candidate is blocked in this scenario. Decide whether to suspend it or record conditional authorization for a later review.',
      decisionField: 'Committee decision', suspend: 'Suspend the candidate', conditional: 'Authorize only once conditions are met',
      reasonLabel: 'Reason for the decision', reasonHint: 'Explain which evidence drove the decision.',
      save: 'Record decision', edit: 'Edit decision', blocked: 'Current release status: blocked',
      recorded: 'Decision recorded for this exercise', export: 'Download decision record',
      owner: 'Accountable roles and next steps',
      next: [
        'Business — confirm the intended use and keep human review of each recommendation.',
        'Risk — complete and document candidate-specific bias tests and risk review.',
        'Platform — verify local GPU readiness and test that a C3 request stays blocked during an outage.',
        'Committee — reconvene against the evidence for the exact candidate version.',
      ],
      footer: 'Scenario data and decisions are simulated. This exercise does not change the AI Factory registry or deploy a model.',
    },
    fr: {
      workshop: 'Atelier de mise en service', badge: 'Exercice fictif', title: 'Mettriez-vous UC-04 en service ?',
      intro: 'En quinze minutes, réunissez le métier, le risque et la plateforme. Examinez le dossier et repartez avec une décision que vous pouvez expliquer.',
      caseName: 'Présélection de candidatures', candidate: 'Modèle candidat', baseline: 'Dernier modèle évalué',
      sensitivity: 'Classe de données', language: 'Langue de l’atelier', steps: 'Étapes de l’atelier', dashboard: 'Ouvrir la scène 3D',
      close: 'Fermer l’atelier', inspect: 'Examiner dans la console 3D', previous: 'Retour', reset: 'Recommencer l’exercice',
      roles: ['Métier', 'Risque', 'Plateforme', 'Comité'],
      stageNames: ['Demande', 'Revue des preuves', 'Panne GPU', 'Décision'],
      requestTitle: 'Ouvrir la demande de mise en service',
      requestBody: 'L’équipe recrutement souhaite mettre en service une nouvelle version de son modèle de présélection. Une personne examine chaque recommandation. Le modèle candidat change de version et traite des données personnelles sur le GPU local.',
      requestPrompt: 'Demandez au responsable métier de préciser l’usage prévu et de confirmer qui examine chaque recommandation.',
      requestAction: 'Demander la mise en service',
      riskTitle: 'Examiner les évaluations et le dossier de risque',
      riskBody: 'Le modèle candidat dépasse les deux seuils de l’exercice. La revue de risque disponible porte sur la version précédente. Le modèle candidat doit faire l’objet de sa propre revue, avec des tests de biais.',
      measure: 'Mesure', threshold: 'Seuil', score: 'Score du candidat', evidence: 'Résultats fictifs d’évaluation',
      faithfulness: 'Fidélité aux sources', redteam: 'Réussite au red teaming', riskStatus: 'Revue de risque du candidat en attente',
      riskAction: 'Consigner la revue manquante',
      platformTitle: 'Simuler l’arrêt du GPU local',
      platformBody: 'Déclenchez une panne simulée pour tester le sort d’une requête C3. Dans cet exercice, le GPU local est la seule destination autorisée.',
      local: 'GPU local', vps: 'VPS', cloud: 'Cloud', available: 'Disponible', unavailable: 'Indisponible',
      ineligible: 'Interdit pour C3', outageAction: 'Simuler la panne GPU', restore: 'Rétablir le GPU simulé',
      outageObserved: 'Panne déclenchée pendant l’exercice', outageStatus: 'Requête bloquée. Aucun repli externe.',
      normalStatus: 'Routage local possible. Revue de risque toujours en attente.', platformAction: 'Présenter les preuves au comité',
      decisionTitle: 'Consigner la décision du comité',
      decisionBody: 'La mise en service est bloquée dans ce scénario. Décidez de suspendre le candidat ou de donner un accord sous conditions pour une revue ultérieure.',
      decisionField: 'Décision du comité', suspend: 'Suspendre le candidat', conditional: 'Autoriser une fois les conditions remplies',
      reasonLabel: 'Motif de la décision', reasonHint: 'Expliquez quelles preuves ont motivé la décision.',
      save: 'Consigner la décision', edit: 'Modifier la décision', blocked: 'Statut actuel de la mise en service : bloquée',
      recorded: 'Décision consignée pour cet exercice', export: 'Télécharger le dossier de décision',
      owner: 'Responsables et prochaines étapes',
      next: [
        'Métier — confirmer l’usage prévu et maintenir une revue humaine de chaque recommandation.',
        'Risque — réaliser et documenter les tests de biais et la revue de la version candidate.',
        'Plateforme — vérifier le GPU local et tester le blocage d’une requête C3 pendant une panne.',
        'Comité — réexaminer les preuves de la version candidate exacte.',
      ],
      footer: 'Les données et les décisions de l’atelier sont simulées. L’exercice ne modifie ni le registre AI Factory ni le déploiement d’un modèle.',
    },
  };

  const standalone = document.getElementById('uc04-standalone');
  const launcher = document.querySelector('[data-exercise-open]');
  if (!standalone && !launcher) return;

  const initialState = () => ({ step: 0, gpuUp: true, outage: false, decision: '', reason: '', recorded: false });
  const defaultLanguage = () => document.documentElement.lang.toLowerCase().startsWith('fr') ? 'fr' : 'en';
  const requestedLanguage = new URLSearchParams(window.location.search).get('lang');
  let state = initialState();
  let lang = standalone && ['en', 'fr'].includes(requestedLanguage) ? requestedLanguage : defaultLanguage();
  let selectedLanguage = false;
  let opener;
  const host = standalone || document.createElement('dialog');
  if (!standalone) {
    host.id = 'uc04-dialog';
    host.className = 'uc04';
    host.setAttribute('aria-labelledby', 'uc04-title');
    document.body.append(host);
    for (const button of document.querySelectorAll('[data-exercise-open]')) {
      button.setAttribute('aria-haspopup', 'dialog');
      button.setAttribute('aria-controls', host.id);
      button.addEventListener('click', () => {
        opener = button;
        if (!selectedLanguage) lang = defaultLanguage();
        render();
        if (!host.open) host.show();
        focusStage();
      });
    }
    host.addEventListener('close', () => opener?.focus());
    host.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        event.preventDefault();
        host.close();
      }
    });
  }

  const percent = value => new Intl.NumberFormat(lang, { style: 'percent', maximumFractionDigits: 1 }).format(value);
  const stageAction = (label, action, disabled = false) => `<button type="button" class="uc04-primary" data-uc04-action="${action}"${disabled ? ' disabled' : ''}>${label}<span aria-hidden="true">↗</span></button>`;
  const status = (label, tone = 'warning') => `<p class="uc04-status" data-tone="${tone}">${label}</p>`;
  const focusStage = () => host.querySelector('#uc04-stage-title')?.focus();
  const chosen = value => state.decision === value ? ' checked' : '';

  function renderStage(t) {
    if (state.step === 0) {
      return `<h2 id="uc04-stage-title" tabindex="-1">${t.requestTitle}</h2><p>${t.requestBody}</p>
        <p class="uc04-note">${t.requestPrompt}</p>${stageAction(t.requestAction, 'next')}`;
    }
    if (state.step === 1) {
      const rows = scenario.scores.map(row => `<tr><th scope="row">${t[row.key]}</th><td>${percent(row.score)}</td><td>${percent(row.threshold)}</td></tr>`).join('');
      return `<h2 id="uc04-stage-title" tabindex="-1">${t.riskTitle}</h2><p>${t.riskBody}</p>
        <table class="uc04-evidence"><caption>${t.evidence}</caption><thead><tr><th scope="col">${t.measure}</th><th scope="col">${t.score}</th><th scope="col">${t.threshold}</th></tr></thead><tbody>${rows}</tbody></table>
        ${status(t.riskStatus)}${stageAction(t.riskAction, 'next')}`;
    }
    if (state.step === 2) {
      return `<h2 id="uc04-stage-title" tabindex="-1">${t.platformTitle}</h2><p>${t.platformBody}</p>
        <dl class="uc04-routing"><div><dt>${t.local}</dt><dd>${state.gpuUp ? t.available : t.unavailable}</dd></div><div><dt>${t.vps}</dt><dd>${t.ineligible}</dd></div><div><dt>${t.cloud}</dt><dd>${t.ineligible}</dd></div></dl>
        <div role="status">${status(state.gpuUp ? t.normalStatus : t.outageStatus, state.gpuUp ? 'warning' : 'danger')}</div>
        <div class="uc04-buttons"><button type="button" class="uc04-secondary" data-uc04-action="gpu">${state.gpuUp ? t.outageAction : t.restore}</button>${stageAction(t.platformAction, 'next', !state.outage)}</div>`;
    }
    const heading = `<h2 id="uc04-stage-title" tabindex="-1">${t.decisionTitle}</h2><p>${t.decisionBody}</p>${status(t.blocked, 'danger')}`;
    if (state.recorded) {
      const next = t.next.map(item => `<li>${item}</li>`).join('');
      return `${heading}<h3>${t.recorded}</h3><p><strong>${t[state.decision]}</strong></p><p class="uc04-reason"></p>
        <h3>${t.owner}</h3><ul class="uc04-next">${next}</ul>
        <div class="uc04-buttons">${stageAction(t.export, 'export')}<button type="button" class="uc04-secondary" data-uc04-action="edit">${t.edit}</button></div>`;
    }
    return `${heading}<form class="uc04-form">
      <fieldset><legend>${t.decisionField}</legend><label><input type="radio" name="decision" value="suspend" required${chosen('suspend')}> ${t.suspend}</label>
        <label><input type="radio" name="decision" value="conditional"${chosen('conditional')}> ${t.conditional}</label></fieldset>
      <label for="uc04-reason">${t.reasonLabel}</label><textarea id="uc04-reason" maxlength="500" required aria-describedby="uc04-reason-hint"></textarea>
      <small id="uc04-reason-hint">${t.reasonHint}</small><button type="submit" class="uc04-primary">${t.save}<span aria-hidden="true">↗</span></button>
    </form>`;
  }

  function render() {
    const t = copy[lang];
    host.lang = lang;
    if (standalone) document.documentElement.lang = lang;
    const steps = t.stageNames.map((label, index) => `<li${index === state.step ? ' aria-current="step"' : ''}${index < state.step ? ' data-complete="true"' : ''}><span>0${index + 1}</span>${label}</li>`).join('');
    host.innerHTML = `<header class="uc04-header"><div><p class="uc04-kicker">Virtual AI Factory / ${t.workshop}</p><h1 id="uc04-title">${t.title}</h1></div>
      <div class="uc04-tools"><select aria-label="${t.language}" data-uc04-lang><option value="en" lang="en"${lang === 'en' ? ' selected' : ''}>EN</option><option value="fr" lang="fr"${lang === 'fr' ? ' selected' : ''}>FR</option></select>
      ${standalone ? `<a href="../index.html?lang=${lang}">${t.dashboard} ↗</a>` : `<button type="button" class="uc04-close" data-uc04-action="close" aria-label="${t.close}">×</button>`}</div>
      <p class="uc04-intro">${t.intro}</p></header>
      <div class="uc04-file"><div><p class="uc04-badge">${t.badge}</p><strong>${scenario.id} / ${t.caseName}</strong></div>
        <dl><div><dt>${t.baseline}</dt><dd>${scenario.baseline}</dd></div><div><dt>${t.candidate}</dt><dd>${scenario.candidate}</dd></div><div><dt>${t.sensitivity}</dt><dd>${scenario.classification}</dd></div></dl></div>
      <div class="uc04-layout"><nav aria-label="${t.steps}"><ol class="uc04-steps">${steps}</ol></nav>
        <section class="uc04-stage" aria-labelledby="uc04-stage-title"><p class="uc04-kicker">0${state.step + 1} / ${t.roles[state.step]}</p>${renderStage(t)}
        ${!standalone && state.step < sections.length ? `<button type="button" class="uc04-inspect" data-uc04-action="inspect">${t.inspect} ↗</button>` : ''}</section></div>
      <footer class="uc04-footer"><div class="uc04-buttons">
        ${state.step ? `<button type="button" class="uc04-secondary" data-uc04-action="back">← ${t.previous}</button>` : ''}
        ${state.step || state.outage ? `<button type="button" class="uc04-secondary" data-uc04-action="reset">${t.reset}</button>` : ''}</div><p>${t.footer}</p></footer>`;
    const reason = host.querySelector('#uc04-reason');
    if (reason) reason.value = state.reason;
    const recordedReason = host.querySelector('.uc04-reason');
    if (recordedReason) recordedReason.textContent = state.reason;
  }

  function decisionRecord() {
    const t = copy[lang];
    const values = scenario.scores.map(row => `- ${t[row.key]}: ${percent(row.score)} / ${t.threshold.toLowerCase()} ${percent(row.threshold)}`);
    return [
      `${scenario.id} / ${t.caseName}`, t.badge.toUpperCase(), '',
      `${t.baseline}: ${scenario.baseline}`, `${t.candidate}: ${scenario.candidate}`, `${t.sensitivity}: ${scenario.classification}`, '',
      t.evidence, ...values, t.riskStatus, '', t.outageObserved, t.outageStatus,
      `${t.local}: ${state.gpuUp ? t.available : t.unavailable}`, `${t.vps}, ${t.cloud}: ${t.ineligible}`, '',
      t.blocked, `${t.decisionField}: ${t[state.decision]}`, `${t.reasonLabel}: ${state.reason}`, '',
      t.owner, ...t.next.map(item => `- ${item}`), '', t.footer, '',
    ].join('\n');
  }

  function downloadRecord() {
    const url = URL.createObjectURL(new Blob([decisionRecord()], { type: 'text/plain;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `UC-04-${lang}.txt`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  host.addEventListener('change', event => {
    if (event.target.matches('[data-uc04-lang]')) {
      lang = event.target.value === 'fr' ? 'fr' : 'en';
      selectedLanguage = true;
      render();
      host.querySelector('[data-uc04-lang]').focus();
    } else if (event.target.name === 'decision') {
      state.decision = event.target.value;
    }
  });
  host.addEventListener('input', event => {
    if (event.target.id === 'uc04-reason') {
      state.reason = event.target.value;
      event.target.setCustomValidity('');
    }
  });
  host.addEventListener('submit', event => {
    event.preventDefault();
    const reason = host.querySelector('#uc04-reason');
    if (!state.reason.trim()) {
      reason.setCustomValidity(copy[lang].reasonHint);
      reason.reportValidity();
      return;
    }
    state.reason = state.reason.trim();
    state.recorded = true;
    render();
    focusStage();
  });
  host.addEventListener('click', event => {
    const button = event.target.closest('[data-uc04-action]');
    if (!button) return;
    switch (button.dataset.uc04Action) {
      case 'close': host.close(); return;
      case 'inspect': {
        const section = sections[state.step];
        opener = undefined;
        host.close();
        document.dispatchEvent(new CustomEvent('factory:exercise-inspect', { detail: { section } }));
        return;
      }
      case 'export': if (state.recorded) downloadRecord(); return;
      case 'gpu': state.gpuUp = !state.gpuUp; state.outage = true; state.recorded = false; break;
      case 'next': state.step += 1; break;
      case 'back': state.step -= 1; break;
      case 'edit': state.recorded = false; break;
      case 'reset': state = initialState(); break;
    }
    render();
    if (button.dataset.uc04Action === 'gpu') host.querySelector('[data-uc04-action="gpu"]').focus();
    else focusStage();
  });

  if (standalone) render();
})();
