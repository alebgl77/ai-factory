import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { join } from 'node:path';

const { chromium } = createRequire(import.meta.url)('playwright');
const files = new Map([
  ['/flow-lab.html', 'text/html'], ['/flow-lab.css', 'text/css'],
  ['/flow-lab.js', 'application/javascript'], ['/flow-sim.js', 'application/javascript'],
]);
const server = createServer(async (request, response) => {
  const contentType = files.get(request.url);
  if (!contentType) return response.writeHead(404).end();
  try {
    const content = await readFile(new URL('../../dashboard/docs' + request.url, import.meta.url));
    response.writeHead(200, { 'Content-Type': contentType + '; charset=utf-8' }).end(content);
  } catch { response.writeHead(500).end(); }
});
await new Promise((resolve, reject) => server.once('error', reject).listen(0, '127.0.0.1', resolve));

let browser;
try {
  browser = await chromium.launch({ executablePath: process.env.FLOW_LAB_CHROME || undefined });
  const origin = 'http://127.0.0.1:' + server.address().port;
  const url = origin + '/flow-lab.html';
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
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

  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
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
  console.log('OK : topologie, clavier, blocage, repli, historique, affichage mobile et absence de requêtes de simulation.');
} finally {
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
}
