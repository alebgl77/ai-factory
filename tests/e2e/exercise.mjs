// SPDX-License-Identifier: MIT
// Workshop checks against the same static assets shipped by Pages and Docker.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

let chromium;
try { ({ chromium } = await import('playwright')); } catch { ({ chromium } = createRequire(import.meta.url)('playwright')); }
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const dashboardFile = name => pathToFileURL(path.join(root, 'dashboard', name)).href;
const browser = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });

try {
  const context = await browser.newContext({ viewport: { width: 360, height: 800 }, acceptDownloads: true });
  const standalone = await context.newPage();
  const externalRequests = [];
  const errors = [];
  standalone.on('request', request => { if (/^https?:/.test(request.url())) externalRequests.push(request.url()); });
  standalone.on('pageerror', error => errors.push(error.message));
  await standalone.goto(`${dashboardFile('exercise/index.html')}?lang=fr`);
  await standalone.getByRole('heading', { name: 'Mettriez-vous UC-04 en service ?' }).waitFor();
  assert.equal(await standalone.getByRole('link', { name: 'Ouvrir la scène 3D' }).getAttribute('href'), '../index.html?lang=fr');
  await standalone.getByRole('combobox').selectOption('en');
  await standalone.getByRole('heading', { name: 'Would you release UC-04?' }).waitFor();
  assert.equal(await standalone.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  await standalone.getByRole('button', { name: 'Request release' }).click();
  await standalone.getByRole('table', { name: 'Sample evaluation results' }).waitFor();
  assert.match(await standalone.locator('table').innerText(), /91\.6%/);
  await standalone.getByText('Candidate risk review pending', { exact: true }).waitFor();
  await standalone.getByRole('button', { name: 'Record the review gap' }).click();
  assert.equal(await standalone.getByRole('button', { name: 'Take evidence to committee' }).isDisabled(), true);
  await standalone.getByRole('button', { name: 'Simulate GPU outage' }).click();
  await standalone.getByText('Request blocked. No external fallback.', { exact: true }).waitFor();
  assert.equal(await standalone.getByText('Ineligible for C3', { exact: true }).count(), 2);
  await standalone.getByRole('button', { name: 'Take evidence to committee' }).click();
  await standalone.getByRole('radio', { name: 'Authorize only once conditions are met' }).check();
  await standalone.getByLabel('Reason for the decision').fill('   ');
  await standalone.getByRole('button', { name: 'Record decision' }).click();
  assert.equal(await standalone.getByRole('button', { name: 'Download decision record' }).count(), 0);
  const reason = 'Candidate review pending; local GPU unavailable. <em>Exercise note</em>';
  await standalone.getByLabel('Reason for the decision').fill(reason);
  await standalone.getByRole('combobox').selectOption('fr');
  await standalone.getByRole('heading', { name: 'Consigner la décision du comité' }).waitFor();
  assert.equal(await standalone.getByLabel('Motif de la décision').inputValue(), reason);
  assert.equal(await standalone.getByRole('radio', { name: 'Autoriser une fois les conditions remplies' }).isChecked(), true);
  await standalone.getByRole('combobox').selectOption('en');
  await standalone.getByRole('button', { name: 'Record decision' }).click();
  await standalone.getByText('Current release status: blocked', { exact: true }).waitFor();
  assert.equal(await standalone.locator('.uc04-reason em').count(), 0);
  assert.equal(await standalone.locator('.uc04-reason').innerText(), reason);
  const [download] = await Promise.all([
    standalone.waitForEvent('download'),
    standalone.getByRole('button', { name: 'Download decision record' }).click(),
  ]);
  assert.equal(download.suggestedFilename(), 'UC-04-en.txt');
  const record = await readFile(await download.path(), 'utf8');
  for (const value of ['FICTIONAL EXERCISE', '2026-10', '2026-05', 'C3', '91.6%', 'No external fallback', 'status: blocked', 'Risk —', 'Platform —', reason]) {
    assert.ok(record.includes(value), `Record should contain ${value}`);
  }
  await standalone.getByRole('button', { name: 'Back' }).click();
  await standalone.getByRole('button', { name: 'Restore simulated GPU' }).click();
  await standalone.getByText('Local route eligible. Risk review still pending.', { exact: true }).waitFor();
  await standalone.getByRole('button', { name: 'Take evidence to committee' }).click();
  assert.equal(await standalone.getByRole('button', { name: 'Download decision record' }).count(), 0);
  await standalone.getByRole('button', { name: 'Restart exercise' }).click();
  await standalone.getByRole('button', { name: 'Request release' }).waitFor();
  assert.deepEqual(errors, []);
  assert.deepEqual(externalRequests, []);
  console.log('PASS UC-04 in 2D at 360px: bilingual state, C3 outage, decision, safe export, and restart');

  const scene = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const vendor = process.env.THREE_VENDOR_DIR;
  if (vendor) {
    await scene.route('https://cdn.jsdelivr.net/npm/three@0.186.1/**', route => {
      const relativePath = new URL(route.request().url()).pathname.replace('/npm/three@0.186.1/', '');
      return route.fulfill({ path: path.join(vendor, relativePath), contentType: 'application/javascript' });
    });
  }
  const dashboard = await scene.newPage();
  await dashboard.goto(dashboardFile('index.html'));
  await dashboard.waitForFunction(() => window.__appReady === true);
  const launcher = dashboard.locator('[data-exercise-open]').first();
  await launcher.click();
  const workshop = dashboard.getByRole('dialog');
  await workshop.getByRole('combobox').selectOption('en');
  await workshop.getByRole('button', { name: 'Request release' }).click();
  await workshop.getByRole('table', { name: 'Sample evaluation results' }).waitFor();
  await workshop.getByRole('button', { name: 'Inspect in the 3D console' }).click();
  await dashboard.locator('#console.open').waitFor();
  await dashboard.locator('#console [data-csec="pipeline"][aria-selected="true"]').waitFor();
  await dashboard.keyboard.press('Escape');
  await launcher.click();
  await workshop.getByRole('table', { name: 'Sample evaluation results' }).waitFor();
  await dashboard.keyboard.press('Escape');
  await workshop.waitFor({ state: 'hidden' });
  assert.equal(await launcher.evaluate(el => document.activeElement === el), true);
  console.log('PASS UC-04 in 3D: shared evidence, pipeline handoff, state preserved, Escape and focus restoration');
} finally {
  await browser.close();
}
