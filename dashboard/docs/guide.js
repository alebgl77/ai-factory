/* SPDX-License-Identifier: MIT */
'use strict';

const searchForm = document.querySelector('#guide-search');
const queryField = document.querySelector('#guide-query');
const statusOutput = document.querySelector('#guide-status');
const results = document.querySelector('#guide-results');
const links = document.querySelectorAll('.toc a');

function normalized(text) {
  return text.toLowerCase().normalize('NFD').replace(/\p{Diacritic}/gu, '');
}

const sections = Array.from(links, link => {
  const section = document.getElementById(link.hash.slice(1));
  return { id: section.id, label: link.textContent, text: normalized(section.textContent).replace(/\s+/g, ' '), section };
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
    link.href = '#' + item.id;
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
  statusOutput.textContent = !terms.length ? '' : matches.length
    ? `${matches.length} section${matches.length > 1 ? 's' : ''}`
    : 'Aucune section trouvée. Essayez un autre terme.';
}

function updateCurrentSection() {
  for (const link of links) {
    if (link.hash === location.hash) link.setAttribute('aria-current', 'location');
    else link.removeAttribute('aria-current');
  }
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
window.addEventListener('hashchange', updateCurrentSection);
updateCurrentSection();
searchForm.hidden = false;
