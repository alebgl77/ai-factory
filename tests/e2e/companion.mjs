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
  'companion/fonts/fonts.css', 'companion/fonts/Geist-Variable.woff2', 'companion/fonts/GeistMono-Variable.woff2',
  'exercise/index.html', 'exercise/uc04.css', 'exercise/uc04.js'
].map(name => [`/project/${name}`, name]));
const contentTypes = { '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'application/javascript', '.woff2': 'font/woff2' };
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

async function checkFonts(page) {
  await page.evaluate(() => document.fonts.ready);
  const loaded = await page.evaluate(() => [...document.fonts].filter(face => face.status === 'loaded').map(face => face.family));
  assert.ok(loaded.includes('Geist'));
  assert.ok(loaded.includes('Geist Mono'));
}

async function checkNarrow(page) {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
}

const caseList = cases => ({ json: { mode: 'protected_demo', cases, capabilities: { can_decide: true } } });

async function decisionRace(context) {
  const page = await context.newPage();
  await page.setViewportSize({ width: 1440, height: 900 });
  let listReply = caseList([caseFixture]);
  const submitted = Promise.withResolvers();
  const decisionReply = Promise.withResolvers();
  await serve(page, async (route, url) => {
    if (url.pathname.endsWith('/cases')) return route.fulfill(await listReply);
    if (url.pathname.endsWith('/decisions')) {
      submitted.resolve();
      return route.fulfill({ json: await decisionReply.promise });
    }
    return route.fulfill({ status: 404 });
  });
  await page.goto('https://factory.test/project/companion/index.html?lang=en');
  await page.getByLabel('Reason for the decision').fill('Reason for the submitted proposal.');
  await page.getByRole('button', { name: 'Approve', exact: true }).click();
  await submitted.promise;
  return {
    page,
    async refresh(reply) {
      listReply = reply;
      const requested = page.waitForRequest(req => new URL(req.url()).pathname.endsWith('/cases'));
      await page.locator('#refresh').click();
      await requested;
    },
    async finishDecision(record) {
      decisionReply.resolve(record);
      await page.locator('#decision-form [aria-busy="true"]').waitFor({ state: 'detached' });
    }
  };
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
  await checkFonts(demo);
  await checkNarrow(demo);
  const firstCase = demo.locator('#cases button').first();
  await firstCase.waitFor();
  assert.equal(await demo.locator('#case-detail').isVisible(), false);
  assert.ok((await firstCase.boundingBox()).height >= 44);
  await firstCase.press('Enter');
  assert.equal(await demo.locator('#case-title').evaluate(node => node === document.activeElement), true);
  assert.equal(await demo.locator('.inbox').isVisible(), false);
  await demo.getByRole('button', { name: 'Retour aux dossiers' }).press('Enter');
  assert.equal(await firstCase.evaluate(node => node === document.activeElement), true);
  await firstCase.press('Enter');
  await demo.setViewportSize({ width: 320, height: 700 });
  await checkNarrow(demo);
  const mobileBack = demo.getByRole('button', { name: 'Retour aux dossiers' });
  assert.ok((await mobileBack.boundingBox()).height >= 44);
  await demo.getByLabel('Motif de la décision').fill('   ');
  await demo.getByRole('button', { name: 'Approuver', exact: true }).click();
  await demo.locator('#decision-notice').getByText('Saisissez le motif de votre décision.').waitFor();
  await demo.getByLabel('Motif de la décision').fill('Éléments CRM et ERP concordants.');
  await demo.getByRole('button', { name: 'Approuver', exact: true }).click();
  await demo.getByText('ERP-DRAFT-104').waitFor();
  assert.equal(await demo.locator('#decision-notice').evaluate(node => node === document.activeElement), true);
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
  assert.equal(await demo.locator('#case-title').evaluate(node => node === document.activeElement), true);
  assert.match(await demo.locator('#evidence-items').innerText(), /<b>Texte brut<\/b>/);
  assert.equal(await demo.locator('#evidence-items b').count(), 0);
  assert.deepEqual(demoRequests, []);
  assert.deepEqual(errors, []);

  const workshop = await context.newPage();
  await workshop.setViewportSize({ width: 320, height: 700 });
  const external = [];
  workshop.on('request', req => { if (!req.url().startsWith('https://factory.test/')) external.push(req.url()); });
  await serve(workshop, route => { throw new Error(`Unexpected API request in static mode: ${route.request().url()}`); }, true);
  await workshop.goto('https://factory.test/project/exercise/index.html?lang=fr&token=fixture-only#private');
  const qrButton = workshop.getByRole('button', { name: 'Code QR' });
  await qrButton.click();
  const qrDialog = workshop.getByRole('dialog', { name: 'Ouvrir sur votre téléphone' });
  await qrDialog.locator('canvas').waitFor({ state: 'visible' });
  await checkFonts(workshop);
  assert.equal(await qrDialog.evaluate(node => node.scrollWidth <= node.clientWidth), true);
  await workshop.keyboard.press('Escape');
  await qrDialog.waitFor({ state: 'detached' });
  assert.equal(await qrButton.evaluate(node => node === document.activeElement), true);
  await qrButton.press('Enter');
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
  await checkFonts(workshop);
  assert.deepEqual(external, []);

  const protectedPage = await context.newPage();
  await protectedPage.setViewportSize({ width: 1440, height: 900 });
  const payloads = [];
  let allowRead = false;
  let cases = [caseFixture];
  let pendingFeedback;
  let releaseFeedback;
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
      cases = [{ ...cases[0], status: 'executed', receipt: { id: 'ERP-DRAFT-42' } }];
      return route.fulfill({ json: cases[0] });
    }
    if (url.pathname.endsWith('/feedback')) {
      if (!pendingFeedback) return route.fulfill({ json: { recorded: true } });
      await pendingFeedback;
      return route.fulfill({ status: 500, json: { detail: 'fixture failure' } });
    }
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
  assert.equal(await protectedPage.locator('.inbox').isVisible(), true);
  assert.equal(await protectedPage.locator('#back-to-cases').isVisible(), false);
  await checkFonts(protectedPage);
  assert.equal(await protectedPage.locator('#case-title img').count(), 0);
  await protectedPage.getByLabel('Reason for the decision').fill('Draft reason for an earlier revision.');
  cases = [{ ...caseFixture, revision: 5, proposal_hash: 'a'.repeat(64), approval_expired: true }];
  await protectedPage.setViewportSize({ width: 390, height: 844 });
  await protectedPage.locator('#back-to-cases').waitFor();
  await protectedPage.getByLabel('Language').selectOption('fr');
  await protectedPage.getByText(/Cette proposition a expiré/).waitFor();
  await protectedPage.locator('#case-notice').getByText(/Examinez la nouvelle révision avant de décider/).waitFor();
  assert.equal(await protectedPage.getByLabel('Motif de la décision').inputValue(), '');
  assert.equal(await protectedPage.getByRole('button', { name: 'Approuver', exact: true }).isEnabled(), false);
  cases = [{ ...caseFixture, revision: 5, proposal_hash: 'a'.repeat(64) }];
  await protectedPage.getByLabel('Langue').selectOption('en');
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
  await protectedPage.getByRole('button', { name: 'Decision inbox' }).click();
  pendingFeedback = new Promise(resolve => { releaseFeedback = resolve; });
  const feedbackSent = protectedPage.waitForRequest(req => req.url().endsWith('/feedback'));
  await protectedPage.getByRole('button', { name: 'Helpful', exact: true }).click();
  await feedbackSent;
  await protectedPage.getByRole('button', { name: 'Back to cases' }).click();
  releaseFeedback();
  await protectedPage.locator('#workspace-notice').getByText(/WK-42 · The workspace could not complete this request/).waitFor();
  await checkNarrow(protectedPage);

  const applying = { ...caseFixture, status: 'applying', decision_rationale: 'Reason for the submitted proposal.' };
  const executed = { ...caseFixture, status: 'executed', receipt: { id: 'ERP-DRAFT-RACE' }, can_decide: false };
  const held = { ...applying, reconciliation_required: true, can_decide: false };

  const revised = await decisionRace(context);
  const newer = { ...caseFixture, title: 'New CRM proposal', revision: 5, proposal_hash: 'a'.repeat(64) };
  await revised.refresh(caseList([newer]));
  await revised.page.getByRole('heading', { name: newer.title }).waitFor();
  await revised.page.getByLabel('Reason for the decision').fill('Draft for the new proposal.');
  await revised.finishDecision(applying);
  assert.equal(await revised.page.locator('#case-title').textContent(), newer.title);
  assert.equal(await revised.page.getByLabel('Reason for the decision').inputValue(), 'Draft for the new proposal.');
  await revised.page.locator('#case-notice').getByText(/Review the new revision before deciding/).waitFor();
  await revised.page.close();

  const heldFirst = await decisionRace(context);
  await heldFirst.refresh(caseList([held]));
  await heldFirst.page.locator('#case-status').getByText('ERP reconciliation required').waitFor();
  await heldFirst.finishDecision({ ...applying, reconciliation_required: false });
  assert.equal(await heldFirst.page.locator('#case-status').textContent(), 'ERP reconciliation required');
  assert.equal(await heldFirst.page.locator('#decision-form').isVisible(), false);
  await heldFirst.refresh(caseList([newer]));
  await heldFirst.page.getByRole('heading', { name: newer.title }).waitFor();
  assert.equal(await heldFirst.page.locator('#decision-form').isVisible(), true);
  await heldFirst.page.close();

  for (const [snapshot, status] of [[caseFixture, 'Executed'], [held, 'ERP reconciliation required']]) {
    const decisionFirst = await decisionRace(context);
    const delayed = Promise.withResolvers();
    await decisionFirst.refresh(delayed.promise);
    await decisionFirst.finishDecision(executed);
    assert.equal(await decisionFirst.page.locator('#refresh').getAttribute('aria-disabled'), 'true');
    delayed.resolve(caseList([snapshot]));
    await decisionFirst.page.locator('#refresh[aria-disabled="false"]').waitFor();
    assert.equal(await decisionFirst.page.locator('#case-status').textContent(), status);
    assert.equal(await decisionFirst.page.locator('#receipt-id').textContent(), executed.receipt.id);
    assert.equal(await decisionFirst.page.locator('#decision-form').isVisible(), false);
    await decisionFirst.page.close();
  }

  const denied = await decisionRace(context);
  const delayedDenial = Promise.withResolvers();
  await denied.refresh(delayedDenial.promise);
  await denied.finishDecision(executed);
  delayedDenial.resolve({ status: 403, json: { detail: 'blocked' } });
  await denied.page.locator('#connection').getByText('Workspace unavailable').waitFor();
  assert.equal(await denied.page.locator('#case-detail').isVisible(), false);
  assert.equal(await denied.page.locator('#cases button').count(), 0);
  await denied.page.close();

  for (const missing of [caseList([]), { status: 403, json: { detail: 'blocked' } }]) {
    const reopened = await decisionRace(context);
    await reopened.refresh(missing);
    await reopened.page.locator('#case-detail').waitFor({ state: 'hidden' });
    await reopened.refresh(caseList([caseFixture]));
    await reopened.page.getByRole('heading', { name: caseFixture.title }).waitFor();
    await reopened.finishDecision(executed);
    assert.equal(await reopened.page.locator('#case-status').textContent(), 'Review required');
    assert.equal(await reopened.page.locator('#receipt').isVisible(), false);
    await reopened.page.close();
  }

  console.log('Companion passed desktop and mobile navigation, local fonts, QR, escalation, exact-revision decisions, delayed responses, feedback and protected mode.');
} finally {
  await browser.close();
}
