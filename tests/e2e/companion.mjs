// SPDX-License-Identifier: MIT
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

let chromium;
try { ({ chromium } = await import('playwright')); } catch { ({ chromium } = createRequire(import.meta.url)('playwright')); }
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const sources = new Map([
  'companion/index.html', 'companion/companion.css', 'companion/companion.js', 'companion/demo.js',
  'companion/handoff.css', 'companion/handoff.js', 'companion/vendor/qrcode.js',
  'exercise/index.html', 'exercise/uc04.css', 'exercise/uc04.js'
].map(name => [`/project/${name}`, name]));
const contentTypes = { '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'application/javascript' };
const browser = await chromium.launch();
const caseFixture = {
  id: 'WK-42', title: 'Protected <img src=x> action', kind: 'action', status: 'waiting_review',
  revision: 4, proposal_hash: 'f'.repeat(64), priority: 88, reason: 'Review a simulated CRM event.',
  evidence: ['<b>Evidence</b>'], action: { type: 'erp.order.create_draft', fields: { amount: 42 } }, can_decide: true
};

async function serve(page, api, staticMode = false) {
  await page.route('https://factory.test/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/api/')) return api(route, url);
    const source = sources.get(url.pathname);
    if (!source) return route.fulfill({ status: 404 });
    const bytes = await readFile(path.join(root, 'dashboard', source));
    const body = staticMode && source === 'companion/index.html'
      ? bytes.toString().replace('<html ', '<html data-static-demo="1" ') : bytes;
    return route.fulfill({ status: 200, contentType: contentTypes[path.extname(source)], body });
  });
}

try {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const demo = await context.newPage();
  const demoRequests = [];
  const errors = [];
  demo.on('request', req => { if (/^https?:/.test(req.url())) demoRequests.push(req.url()); });
  demo.on('pageerror', error => errors.push(error.message));
  const file = pathToFileURL(path.join(root, 'dashboard', 'companion', 'index.html'));
  await demo.goto(`${file.href}?lang=fr`);
  await demo.getByText(/Simulation locale/).waitFor();
  assert.equal(await demo.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  await demo.getByLabel('Motif de la décision').fill('Éléments CRM et ERP concordants.');
  await demo.getByRole('button', { name: 'Approuver', exact: true }).click();
  await demo.getByText('ERP-DRAFT-104').waitFor();
  await demo.getByLabel('Commentaire (facultatif)').fill('Rapprochement utile.');
  await demo.getByRole('button', { name: 'Utile', exact: true }).click();
  await demo.getByText('Avis enregistré.').waitFor();
  await demo.getByRole('button', { name: 'Afficher l’historique' }).click();
  await demo.locator('#history-items').getByText('Éléments CRM et ERP concordants.').waitFor();
  assert.match(await demo.locator('#history-feedback').innerText(), /Votre avis Utile · Rapprochement utile/);
  await demo.getByRole('button', { name: 'Conversation avec un agent' }).click();
  await demo.getByLabel('Agent', { exact: true }).selectOption('finance');
  assert.match(await demo.locator('#chat-privacy').innerText(), /partagées/);
  await demo.getByLabel('Votre message').fill('Quel est le montant ?');
  await demo.getByRole('button', { name: 'Envoyer' }).click();
  await demo.getByText('Finance · Simulation').waitFor();
  await demo.getByLabel('Votre message').fill('Cette décision stratégique est non documentée. <b>Texte brut</b>');
  await demo.getByRole('button', { name: 'Envoyer' }).click();
  await demo.getByRole('button', { name: 'Ouvrir la revue humaine' }).click();
  await demo.getByRole('heading', { name: 'Revue demandée dans la conversation' }).waitFor();
  assert.match(await demo.locator('#evidence-items').innerText(), /<b>Texte brut<\/b>/);
  assert.equal(await demo.locator('#evidence-items b').count(), 0);
  assert.deepEqual(demoRequests, []);
  assert.deepEqual(errors, []);

  const workshop = await context.newPage();
  const external = [];
  workshop.on('request', req => { if (!req.url().startsWith('https://factory.test/')) external.push(req.url()); });
  await serve(workshop, route => { throw new Error(`Unexpected API request in static mode: ${route.request().url()}`); }, true);
  await workshop.goto('https://factory.test/project/exercise/index.html?lang=fr&token=fixture-only#private');
  await workshop.getByRole('button', { name: 'Code QR' }).click();
  const qrDialog = workshop.getByRole('dialog', { name: 'Ouvrir sur votre téléphone' });
  await qrDialog.locator('canvas').waitFor({ state: 'visible' });
  const qrLink = qrDialog.getByRole('link');
  assert.equal(await qrLink.getAttribute('href'), 'https://factory.test/project/companion/index.html?lang=fr');
  assert.equal(await qrLink.getAttribute('referrerpolicy'), 'no-referrer');
  const matrixMatches = await qrDialog.locator('canvas').evaluate((canvas, target) => {
    const qr = window.qrcode(0, 'M');
    qr.addData(target);
    qr.make();
    const ctx = canvas.getContext('2d');
    for (let r = 0; r < qr.getModuleCount(); r++) {
      for (let c = 0; c < qr.getModuleCount(); c++) {
        if ((ctx.getImageData(22 + c * 5, 22 + r * 5, 1, 1).data[0] === 0) !== qr.isDark(r, c)) return false;
      }
    }
    return canvas.width === qr.getModuleCount() * 5 + 40;
  }, 'https://factory.test/project/companion/index.html?lang=fr');
  assert.equal(matrixMatches, true);
  await qrLink.click();
  await workshop.getByText(/Simulation locale/).waitFor();
  assert.deepEqual(external, []);

  const protectedPage = await context.newPage();
  const payloads = [];
  let allowRead = false;
  let cases = [caseFixture];
  await serve(protectedPage, async (route, url) => {
    const req = route.request();
    if (url.pathname.endsWith('/cases')) {
      return allowRead ? route.fulfill({ json: { mode: 'protected_demo', cases, capabilities: { can_decide: true } } }) :
        route.fulfill({ status: 403, json: { detail: 'blocked' } });
    }
    if (url.pathname.endsWith('/history')) return route.fulfill({ json: {
      decisions: [{ decision: 'approve', revision: 5, created_at: '2026-11-05T10:00:00Z', rationale: '<b>Recorded reason</b>' }],
      feedback_counts: { helpful: 1 }, my_feedback: { rating: 'helpful', comment: '' }
    } });
    payloads.push(req.postDataJSON());
    if (url.pathname.endsWith('/decisions')) {
      cases = [{ ...caseFixture, status: 'executed', receipt: { id: 'ERP-DRAFT-42' } }];
      return route.fulfill({ json: cases[0] });
    }
    if (url.pathname.endsWith('/feedback')) return route.fulfill({ json: { recorded: true } });
    if (url.pathname.endsWith('/chat')) return route.fulfill({ json: { answer: '<b>Advice</b>', source: 'local', escalated: false } });
    return route.fulfill({ status: 404 });
  });
  await protectedPage.goto('https://factory.test/project/companion/index.html?lang=en');
  await protectedPage.getByText('Workspace unavailable').first().waitFor();
  await protectedPage.getByText(/Sign in with an authorized identity/).waitFor();
  assert.equal(await protectedPage.getByRole('button', { name: 'Agent conversation' }).isEnabled(), false);
  assert.equal(await protectedPage.getByText(/Local simulation/).count(), 0);
  allowRead = true;
  await protectedPage.getByRole('button', { name: 'Refresh' }).click();
  await protectedPage.getByText(/Authenticated workspace/).waitFor();
  await protectedPage.getByRole('heading', { name: caseFixture.title }).waitFor();
  assert.equal(await protectedPage.locator('#case-title img').count(), 0);
  await protectedPage.getByLabel('Reason for the decision').fill('Draft reason for an earlier revision.');
  cases = [{ ...caseFixture, revision: 5, proposal_hash: 'a'.repeat(64), approval_expired: true }];
  await protectedPage.getByRole('button', { name: 'Refresh' }).click();
  await protectedPage.getByText(/proposal has expired/).waitFor();
  assert.equal(await protectedPage.getByLabel('Reason for the decision').inputValue(), '');
  assert.equal(await protectedPage.getByRole('button', { name: 'Approve', exact: true }).isEnabled(), false);
  cases = [{ ...caseFixture, revision: 5, proposal_hash: 'a'.repeat(64) }];
  await protectedPage.getByRole('button', { name: 'Refresh' }).click();
  await protectedPage.getByText(/Revision 5/).waitFor();
  await protectedPage.waitForFunction(() => !document.querySelector('#decision-form [value="approve"]').disabled);
  await protectedPage.getByLabel('Reason for the decision').fill('Confirmed the exact proposal.');
  await protectedPage.getByRole('button', { name: 'Approve', exact: true }).click();
  await protectedPage.getByText('ERP-DRAFT-42').waitFor();
  assert.deepEqual(payloads[0], { decision: 'approve', revision: 5, proposal_hash: 'a'.repeat(64), rationale: 'Confirmed the exact proposal.' });
  await protectedPage.getByRole('button', { name: 'Helpful', exact: true }).click();
  await protectedPage.getByText('Feedback recorded.').waitFor();
  assert.deepEqual(payloads[1], { rating: 'helpful', comment: '' });
  await protectedPage.getByRole('button', { name: 'Show history' }).click();
  await protectedPage.locator('#history-items').getByText('<b>Recorded reason</b>').waitFor();
  assert.equal(await protectedPage.locator('#history-items b').count(), 0);
  await protectedPage.getByRole('button', { name: 'Agent conversation' }).click();
  await protectedPage.getByLabel('Your message').fill('What changed?');
  await protectedPage.getByRole('button', { name: 'Send' }).click();
  await protectedPage.getByText('Operations · Local model').waitFor();
  assert.equal(await protectedPage.locator('#messages b').count(), 0);
  assert.deepEqual(payloads[2], { agent: 'operations', message: 'What changed?', case_id: 'WK-42' });
  console.log('Mobile Companion passed local simulation, exact-revision decisions, feedback, escalation, QR and protected mode.');
} finally {
  await browser.close();
}
