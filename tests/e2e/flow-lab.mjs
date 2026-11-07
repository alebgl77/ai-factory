import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { join } from 'node:path';

const { chromium } = createRequire(import.meta.url)('playwright');
const files = new Map([
  ['/flow-lab.html', 'text/html'], ['/flow-lab.css', 'text/css'],
  ['/flow-lab.js', 'application/javascript'], ['/flow-sim.js', 'application/javascript'],
]);
let browser;
try {
  browser = await chromium.launch({ executablePath: process.env.FLOW_LAB_CHROME || undefined });
  const origin = 'http://flow-lab.test';
  const url = origin + '/flow-lab.html';
  async function labPage(options) {
    const page = await browser.newPage(options);
    await page.route(origin + '/**', async route => {
      const pathname = new URL(route.request().url()).pathname;
      const type = files.get(pathname);
      if (!type) return route.fulfill({ status: 404 });
      const body = await readFile(new URL('../../dashboard/docs' + pathname, import.meta.url));
      return route.fulfill({ contentType: type + '; charset=utf-8', body });
    });
    return page;
  }
  const page = await labPage({ viewport: { width: 1440, height: 1000 } });
  const requests = [];
  const errors = [];
  page.on('request', request => requests.push(request.url()));
  page.on('pageerror', error => errors.push(error.message));

  await page.goto(url);
  assert.equal(await page.locator('#outcome').textContent(), 'Lot livré');
  assert.match(await page.locator('#model-split').textContent(), /100 distante/);
  await page.getByRole('button', { name: 'Simuler le lot' }).click();
  assert.equal(await page.locator('#history-list li').count(), 1);
  await page.getByRole('button', { name: 'Données sensibles C4' }).click();
  await page.getByRole('button', { name: 'Simuler le lot' }).click();
  assert.match(await page.locator('#model-split').textContent(), /100 inférence\(s\) locale\(s\) · 0 distante/);
  assert.match(await page.locator('#comparison').textContent(), /Lot courant comparé/);
  if (process.env.FLOW_LAB_SCREENSHOT_DIR) {
    await page.screenshot({ path: join(process.env.FLOW_LAB_SCREENSHOT_DIR, 'ai-factory-flow-lab-desktop.png'), fullPage: true });
  }

  await page.locator('#edge-summary').click();
  await page.getByRole('button', { name: 'Retirer liaison #3 vers #5, C3/C4' }).click();
  await page.getByRole('button', { name: 'Simuler le lot' }).click();
  assert.equal(await page.locator('#outcome').textContent(), 'Lot bloqué');
  assert.match(await page.locator('#warnings').textContent(), /avant toute tentative/);

  await page.getByRole('button', { name: 'Ajouter Modèle CPU' }).press('Enter');
  const node = page.getByRole('button', { name: 'Modèle CPU · 7', exact: true });
  assert.equal(await node.getAttribute('data-col'), '0');
  await page.getByRole('button', { name: 'Déplacer vers droite' }).press('Enter');
  assert.equal(await node.getAttribute('data-col'), '1');
  await page.locator('#edge-from').selectOption('7');
  await page.locator('#edge-to').selectOption('6');
  await page.getByRole('button', { name: 'Relier', exact: true }).press('Enter');
  assert.match(await page.locator('#edge-list').textContent(), /#7 → #6/);
  await page.getByRole('button', { name: 'Retirer le nœud' }).click();
  assert.equal(await node.count(), 0);
  assert.doesNotMatch(await page.locator('#edge-list').textContent(), /#7 → #6/);

  await page.reload();
  assert.equal(await page.locator('#history-list li').count(), 3);
  await page.locator('#history-pick').selectOption('0');
  await page.getByRole('button', { name: 'Rejouer la sélection' }).click();
  assert.equal(await page.locator('#outcome').textContent(), 'Lot bloqué');
  assert.equal(await page.locator('#history-list li').count(), 3);
  assert.deepEqual(errors, []);
  assert.ok(requests.length >= 4);
  assert.ok(requests.every(request => new URL(request).origin === origin && files.has(new URL(request).pathname)));

  const mobile = await labPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  await mobile.goto(url);
  assert.equal(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  const canvas = mobile.getByRole('region', { name: 'Canevas de topologie défilable' });
  assert.equal(await canvas.evaluate(element => element.scrollWidth > element.clientWidth), true);
  await mobile.getByRole('button', { name: 'Panne du modèle distant' }).click();
  await mobile.getByRole('button', { name: 'Simuler le lot' }).click();
  assert.equal(await mobile.locator('#outcome').textContent(), 'Lot livré');
  assert.match(await mobile.locator('#warnings').textContent(), /activé son repli/);
  if (process.env.FLOW_LAB_SCREENSHOT_DIR) {
    await mobile.screenshot({ path: join(process.env.FLOW_LAB_SCREENSHOT_DIR, 'ai-factory-flow-lab-mobile.png'), fullPage: true });
  }
  await page.goto(url + '?lang=en');
  assert.equal(await page.locator('html').getAttribute('lang'), 'en');
  assert.match(await page.title(), /^AI Flow Lab/);
  assert.equal(await page.getByRole('heading', { name: 'AI Flow Lab' }).count(), 1);
  assert.equal(await page.locator('#dashboard-link').getAttribute('href'), '../index.html?lang=en');
  assert.equal(await page.locator('#history-list li').count(), 3);
  await page.getByRole('button', { name: 'Remote model outage' }).click();
  await page.getByRole('button', { name: 'Simulate the batch' }).click();
  assert.equal(await page.locator('#outcome').textContent(), 'Batch delivered');
  assert.match(await page.locator('#warnings').textContent(), /used its fallback/);
  assert.equal(await page.evaluate(async () => {
    try { await fetch('./flow-lab.js'); return true; } catch { return false; }
  }), false);
  assert.deepEqual(errors, []);
  assert.ok(requests.every(request => new URL(request).origin === origin && files.has(new URL(request).pathname)));
  console.log('OK : topologie, clavier, blocage, repli, historique, FR/EN, affichage mobile et CSP hors réseau.');
} finally {
  await browser?.close();
}
