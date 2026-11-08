/* SPDX-License-Identifier: MIT */
'use strict';

const links = document.querySelectorAll('.toc a, .section-nav a');
const languageLink = document.querySelector('[data-guide-language]');
const fragments = new Map(Array.from(document.querySelectorAll('[id]'), item => ['#' + item.id, item]));
const french = document.documentElement.lang === 'fr';

function normalized(text) {
  return text.toLowerCase().normalize('NFD').replace(/\p{Diacritic}/gu, '');
}

function updateCurrentSection() {
  const target = fragments.get(location.hash);
  for (const link of links) {
    if (link.hash === location.hash) link.setAttribute('aria-current', 'location');
    else link.removeAttribute('aria-current');
  }
  if (target?.matches('details')) target.open = true;
  if (languageLink) languageLink.hash = target?.id ?? '';
}

window.addEventListener('hashchange', updateCurrentSection);
updateCurrentSection();

const searchForm = document.querySelector('#guide-search');
if (searchForm) {
  const queryField = document.querySelector('#guide-query');
  const statusOutput = document.querySelector('#guide-status');
  const results = document.querySelector('#guide-results');
  const sections = Array.from(links).flatMap(link => {
    const section = fragments.get(link.hash);
    if (!section) return [];
    return [{ section, label: link.textContent, text: normalized(section.textContent).replace(/\s+/g, ' ') }];
  });

  function updateSearch(query) {
    const phrase = normalized(query.trim()).replace(/\s+/g, ' ');
    const terms = phrase ? phrase.split(' ') : [];
    const rank = item => item.text.includes(phrase) ? 0 : 1;
    const matches = terms.length ? sections.filter(item => terms.every(term => item.text.includes(term)))
      .sort((left, right) => rank(left) - rank(right)) : [];
    results.replaceChildren();
    for (const item of matches) {
      const row = document.createElement('li');
      const link = document.createElement('a');
      link.href = '#' + item.section.id;
      link.textContent = item.label;
      link.addEventListener('click', () => {
        for (const panel of item.section.querySelectorAll('details')) {
          const text = normalized(panel.textContent);
          if (terms.every(term => text.includes(term))) panel.open = true;
        }
      });
      row.append(link);
      results.append(row);
    }
    results.hidden = !matches.length;
    const noResults = french ? 'Aucune section trouvée. Essayez un autre terme.' : 'No sections found. Try another term.';
    statusOutput.textContent = !terms.length ? '' : matches.length
      ? `${matches.length} section${matches.length > 1 ? 's' : ''}`
      : noResults;
  }

  queryField.addEventListener('input', () => updateSearch(queryField.value));
  searchForm.addEventListener('submit', event => {
    event.preventDefault();
    results.querySelector('a')?.focus();
  });
  searchForm.addEventListener('reset', () => {
    updateSearch('');
    queryField.focus();
  });
  searchForm.hidden = false;
}
