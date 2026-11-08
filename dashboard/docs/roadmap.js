/* SPDX-License-Identifier: MIT */
'use strict';

const language = new URLSearchParams(location.search).get('lang') === 'en' ? 'en' : 'fr';
const english = language === 'en';
document.documentElement.lang = language;
document.documentElement.dataset.localized = '';
document.title = english ? 'Roadmap · AI Factory' : 'Feuille de route · AI Factory';
document.querySelector('meta[name="description"]').content = english
  ? 'AI Factory’s public roadmap for enterprise agents.'
  : 'Feuille de route publique d’AI Factory pour les agents en entreprise.';
document.getElementById('roadmap-nav').setAttribute('aria-label', english ? 'Roadmap navigation' : 'Navigation de la feuille de route');
document.getElementById('roadmap-horizons').setAttribute('aria-label', english ? 'Roadmap horizons' : 'Horizons de la feuille de route');
document.getElementById('roadmap-home').href = english ? '../index.html?lang=en' : '../index.html?lang=fr';
document.getElementById('roadmap-guide').href = english ? './index.en.html' : './index.html';

const languageLinks = document.querySelectorAll('[data-language]');
function syncLanguageLinks() {
  const target = document.getElementById(location.hash.slice(1));
  for (const link of languageLinks) link.hash = target?.id ?? '';
}
document.querySelector('[data-language="' + language + '"]').setAttribute('aria-current', 'page');
syncLanguageLinks();
window.addEventListener('hashchange', syncLanguageLinks);
