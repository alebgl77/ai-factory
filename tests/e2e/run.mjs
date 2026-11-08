// End-to-end checks of the 3D dashboard (MIT). Usage:
//   node tests/e2e/run.mjs                                  -> file:// mode with the embedded demo snapshot
//   E2E_BASE_URL=http://localhost:8080 node tests/e2e/run.mjs -> deployed stack (API mode)
// THREE_VENDOR_DIR=/path/to/three/package serves Three.js locally instead of the CDN (offline CI).
// E2E_VISUAL_DIR=/path/to/output saves Observatory screenshots using the fictional sample.
import { createRequire } from 'node:module';
// ESM imports ignore NODE_PATH: fall back to require() so a global Playwright install also works
let chromium;
try { ({ chromium } = await import('playwright')); } catch { ({ chromium } = createRequire(import.meta.url)('playwright')); }
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import fs from 'node:fs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const dashboardPath = path.join(root, 'dashboard/index.html');
const base = process.env.E2E_BASE_URL || pathToFileURL(dashboardPath).href;
const vendor = process.env.THREE_VENDOR_DIR;
const visualDir = process.env.E2E_VISUAL_DIR;
const results = [];
const check = (name, ok, info = '') => {
  results.push({ name, ok: !!ok, info });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${info ? '  — ' + info : ''}`);
};
const seatDemoRoster = page => page.evaluate(() => {
  const twin = window.__twin, { S, Store, EMPLOYEES } = twin;
  S.live = false; S.t = 600;
  const bookings = EMPLOYEES.map(person => ({ resourceId: person.desk, employeeId: person.id,
    date: S.date, start: 540, end: 660, activity: 'delivery' }));
  Store.load({ persist: false, raw: JSON.stringify({ v: 2, bookings, dayStatus: {}, meta: { autoDemo: false } }) });
  twin.refresh();
});
const sceneReady = page => page.locator('#loading[data-scene="ready"]').waitFor({ state: 'attached', timeout: 30000 });
const mountedSeatPortraits = page => page.evaluate(() => Object.values(window.__twin.VIS)
  .flatMap(space => space.avatars).filter(avatar => avatar.portrait && !avatar.head.visible)
  .map(avatar => avatar.portrait.name).sort());
const encounterPortraits = page => page.evaluate(() => window.__twin.VIS.D1.avatars[0].group.parent.parent.children
  .filter(actor => actor.name === 'factory-encounter-actor')
  .map(actor => actor.children.find(child => child.name.startsWith('factory-portrait-'))?.name || null));

const browser = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'fr-FR', timezoneId: 'Europe/Paris' });
if (vendor) {
  await ctx.route('https://cdn.jsdelivr.net/npm/three@0.186.1/**', route => {
    const rel = new URL(route.request().url()).pathname.replace('/npm/three@0.186.1/', '');
    const file = path.join(vendor, rel);
    return fs.existsSync(file) ? route.fulfill({ path: file, contentType: 'application/javascript' }) : route.fulfill({ status: 404, body: 'not found' });
  });
}
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', e => errors.push(e.message));
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
await page.goto(base);
await page.waitForFunction(() => window.__appReady === true, null, { timeout: 30000 });
await sceneReady(page);

const T = await page.evaluate(() => ({
  source: window.__twin?.source,
  people: window.__twin?.EMPLOYEES.length,
  uc: Object.keys(window.__twin?.UC || {}).length,
  tags: document.querySelectorAll('.tag[data-res]').length,
}));
check('boot without console errors', errors.length === 0, errors.slice(0, 3).join(' | '));
check('snapshot loaded', T.source === (process.env.E2E_BASE_URL ? 'api' : 'embedded'), `source=${T.source}`);
check('configurable profiles loaded', T.people >= 8, `${T.people} people`);
check('registry use cases loaded', T.uc >= 6, `${T.uc} use cases`);
check('17 spaces labelled in 3D (incl. Skills Lab, R&D, Agent Ops)', T.tags === 17, `${T.tags} tags`);
for (const id of ['S1', 'X1', 'X2', 'X3', 'O1', 'O2']) check(`3D space ${id} present`, await page.$(`.tag[data-res="${id}"]`));

await page.click('#tabFactory');
await page.waitForTimeout(200);
check('Usine tab lists 11 console sections', (await page.$$('#factoryPanel [data-csec]')).length === 11);
const coffeeMap = page.locator('#coffeeMapOpen');
await coffeeMap.focus();
await page.keyboard.press('Enter');
check('Coffee corner map label opens the idea wall with the keyboard',
  await page.locator('#cTitle').textContent() === 'Coin café · Boîte à idées');
await page.keyboard.press('Escape');
check('closing the idea wall restores focus to the Coffee corner map label',
  await coffeeMap.evaluate(el => document.activeElement === el));
await page.locator('#viewTop').click();
await coffeeMap.click();
check('Coffee corner also opens from the floor plan', await page.locator('.ideas').isVisible());
await page.keyboard.press('Escape');
await page.locator('#view3d').click();
const shadowMap = page.locator('#shadowMapOpen');
if (!process.env.E2E_BASE_URL && visualDir) await page.screenshot({ path: path.join(visualDir, 'ai-factory-observatoire-carte.png'), animations: 'disabled' });
await shadowMap.focus();
await page.keyboard.press('Enter');
check('Observatory map label opens its console with the keyboard',
  await page.locator('#cTitle').textContent() === 'Observatoire des usages IA');
await page.keyboard.press('Escape');
check('closing the Observatory restores focus to its map label', await shadowMap.evaluate(el => document.activeElement === el));
await shadowMap.click();
check('the 3D map label also opens the Observatory with the pointer', await page.locator('#cTitle').textContent() === 'Observatoire des usages IA');
await page.keyboard.press('Escape');
await page.locator('#factoryPanel [data-csec="shadow"]').click();
check('Observatory has a second entry in the Factory panel', await page.locator('#console').getAttribute('aria-hidden') === 'false');
if (!process.env.E2E_BASE_URL) {
  check('the embedded Observatory sample is labelled fictional and is read-only',
    await page.locator('[data-shadow-state="demo"]').count() === 1 &&
    await page.getByText('Démo fictive', { exact: false }).count() > 0 && await page.locator('#shadowProposal').count() === 0);
  await page.locator('[data-shadow-filter="team"]').selectOption('delivery');
  const teamUsage = page.getByRole('table', { name: 'Signaux par équipe et outil' });
  check('team filter scopes usage without changing the enterprise signal summary',
    await teamUsage.locator('tbody tr').count() === 1 && (await teamUsage.textContent()).includes('demo-writing-assistant') &&
    (await page.locator('.shadow-metrics').textContent()).includes('387'));
  await page.locator('[data-shadow-filter="team"]').selectOption('');
  await page.locator('[data-shadow-filter="tool"]').selectOption('internal-ai-studio');
  check('tool filter applies to observed signals and separately measured adoption', await teamUsage.locator('tbody tr').count() === 1 &&
    await page.getByRole('table', { name: 'Adoption mesurée' }).locator('tbody tr').count() === 2);
  await page.locator('[data-shadow-filter="tool"]').selectOption('');
  if (visualDir) await page.screenshot({ path: path.join(visualDir, 'ai-factory-observatoire-desktop.png'), animations: 'disabled' });
}
await page.keyboard.press('Escape');
for (const sec of ['registry', 'finops', 'pipeline', 'dataloop', 'placement', 'agentops', 'catalog', 'skills', 'grafana']) {
  await page.evaluate(k => window.__twin.openConsole(k), sec);
  await page.waitForTimeout(150);
  const len = await page.evaluate(() => document.getElementById('cBody').textContent.trim().length);
  check(`console section ${sec} renders`, len > 40, `${len} chars`);
}
await page.evaluate(() => window.__twin.openConsole('registry'));
await page.waitForTimeout(150);
const regRows = await page.evaluate(() => document.querySelectorAll('#cBody table.ctable')[0]?.querySelectorAll('tbody tr').length || 0);
check('registry table shows every use case', regRows >= T.uc, `${regRows} rows`);
const reeval = await page.evaluate(() => document.querySelectorAll('#cBody .row-warn').length);
check('re-evaluation required is flagged for changed model versions', reeval >= 1, `${reeval} flagged`);
await page.keyboard.press('Escape');
await page.waitForTimeout(200);
check('Escape closes the console', await page.evaluate(() => !document.getElementById('console').classList.contains('open')));

// A sensitive use case booked on an R&D bench must raise the "Lab only" policy hint
await page.click('.tag[data-res="X1"]');
await page.waitForTimeout(600);
const sens = await page.evaluate(() => Object.values(window.__twin.UC).find(u => u.sensitive)?.id);
if (sens) { await page.selectOption('#fUc', sens); await page.dispatchEvent('#fUc', 'change'); }
const hint = sens ? await page.textContent('#fInfo') : '';
check('sensitive data outside the Lab raises the policy hint', !!sens && hint.includes('Lab'), sens || 'no sensitive use case');
await page.keyboard.press('Escape');

// Visit mode (Street View-like): drag the character onto the floor, walk, collide, interact, leave
{
  const pov = () => page.evaluate(() => { const p = window.__twin.pov; return { active: p.active, anim: p.animating, ...p.pos, zone: p.zone, target: p.target }; });
  await page.click('#view3d');
  await page.waitForFunction(() => {  // let the overview and any drawer camera offset settle before projecting
    const T = window.__twin;
    return T.camera.position.distanceTo(T.viewPose('3d').p) < 0.1 && !T.camera.view?.enabled;
  });
  const bb = await (await page.$('#pegBtn')).boundingBox();
  const drop = await page.evaluate(() => {
    const T = window.__twin, V = T.camera.position.constructor, host = T.renderer.domElement.getBoundingClientRect();
    const spot = T.pov.free(-4.0, 4.6);
    T.camera.updateMatrixWorld();
    const p = new V(spot.x, 0, spot.z).project(T.camera);
    return { x: host.left + (p.x + 1) / 2 * host.width, y: host.top + (1 - p.y) / 2 * host.height, spot };
  });
  await page.mouse.move(bb.x + bb.width / 2, bb.y + bb.height / 2); await page.mouse.down();
  for (let i = 1; i <= 10; i++) await page.mouse.move(bb.x + (drop.x - bb.x) * i / 10, bb.y + (drop.y - bb.y) * i / 10);
  await page.mouse.up();
  await page.waitForFunction(() => window.__twin.pov.active && !window.__twin.pov.animating, null, { timeout: 8000 }).catch(() => {});
  let s = await pov();
  check('visit mode: dropping the character on the floor enters first-person view there', s.active && Math.hypot(s.x - drop.spot.x, s.z - drop.spot.z) < 0.35, `${s.zone} (${s.x.toFixed(1)}, ${s.z.toFixed(1)})`);
  await page.evaluate(() => window.__twin.pov.enter(-4.0, 4.6, -Math.PI / 2));
  await page.waitForFunction(() => !window.__twin.pov.animating, null, { timeout: 8000 });
  const s0 = await pov();
  const walkStart = await page.evaluate(() => performance.now());
  await page.keyboard.down('KeyW'); await page.waitForTimeout(1200); await page.keyboard.up('KeyW');
  const walkSeconds = await page.evaluate(start => (performance.now() - start) / 1000, walkStart);
  s = await pov();
  const walked = Math.hypot(s.x - s0.x, s.z - s0.z);
  check('visit mode: W walks forward at a speed bounded by elapsed time', walked > 0.5 && walked < 1.9 * walkSeconds + 0.5, `${walked.toFixed(2)} m in ${walkSeconds.toFixed(2)} s`);
  await page.evaluate(() => window.__twin.pov.enter(11.6, 7.2, -Math.PI / 2));
  await page.waitForFunction(() => !window.__twin.pov.animating, null, { timeout: 8000 });
  await page.keyboard.down('KeyW'); await page.waitForTimeout(2500); await page.keyboard.up('KeyW');
  s = await pov();
  check('visit mode: walls stop the visitor', s.x < 12.7 && !(await page.evaluate(() => window.__twin.pov.blocked(window.__twin.pov.pos.x, window.__twin.pov.pos.z))), `x=${s.x.toFixed(2)}`);
  await page.evaluate(() => { const v = window.__twin.VIS.D2.focus; window.__twin.pov.enter(v.x, v.z - 1.3, Math.PI); });
  await page.waitForFunction(() => !window.__twin.pov.animating && window.__twin.pov.target, null, { timeout: 8000 }).catch(() => {});
  await page.keyboard.press('KeyE'); await page.waitForTimeout(500);
  check('visit mode: E interacts with the space in front', !!(await page.evaluate(() => window.__twin.S.selected)));
  await page.keyboard.press('Escape'); await page.waitForTimeout(200);
  check('visit mode: first Escape closes the panel and keeps the visit', (await pov()).active);
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !window.__twin.pov.active && !window.__twin.pov.animating, null, { timeout: 8000 }).catch(() => {});
  const cam = await page.evaluate(() => ({ y: window.__twin.camera.position.y, fov: window.__twin.camera.fov, ctrl: window.__twin.controls.enabled }));
  check('visit mode: Escape leaves and restores the overview camera', !(await pov()).active && cam.y > 8 && cam.fov === 38 && cam.ctrl, JSON.stringify(cam));
  const doorOpen = await page.evaluate(() => [-3.6, -4, -4.4, -4.8, -5.2, -5.6].every(z => !window.__twin.pov.blocked(8.55, z)));
  await page.evaluate(() => window.__twin.pov.enter(8.55, -5.9, 0));
  await page.waitForFunction(() => !window.__twin.pov.animating && window.__twin.pov.target?.id === 'shadow', null, { timeout: 8000 }).catch(() => {});
  const observatory = await pov();
  check('visit mode: the former entrance and the Observatory screen are reachable', doorOpen && observatory.zone === 'Observatoire des usages IA' && observatory.target?.id === 'shadow');
  await page.keyboard.press('KeyE');
  check('visit mode: E opens the Observatory', await page.locator('#console').getAttribute('aria-hidden') === 'false' && await page.locator('#cTitle').textContent() === 'Observatoire des usages IA');
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !window.__twin.pov.active && !window.__twin.pov.animating, null, { timeout: 8000 });
}

for (const lens of ['uc', 'heat', 'status']) { await page.click(`[data-lens="${lens}"]`); await page.waitForTimeout(150); }
check('lenses switch without errors', errors.length === 0, errors.slice(0, 2).join(' | '));
const leak = await page.evaluate(() => /glsa_|Bearer\s+[A-Za-z0-9]/.test(document.documentElement.outerHTML));
check('no Grafana token or bearer in the page', !leak);

if (!process.env.E2E_BASE_URL) {
  await page.evaluate(() => window.__twin.openConsole('registry'));
  const frenchName = await page.evaluate(() => window.__twin.UC['uc-04'].name);
  await page.selectOption('#languageSelect', 'en');
  const switched = await page.evaluate(() => ({
    lang: document.documentElement.lang,
    payload: window.__twin.SNAP.meta.lang,
    source: document.getElementById('srcBadge').textContent,
    workshop: document.getElementById('exercise2d').getAttribute('href'),
    registry: document.getElementById('cBody').textContent.includes(window.__twin.UC['uc-04'].name),
    name: window.__twin.UC['uc-04'].name,
  }));
  check('offline EN switch updates the header, source badge and rendered registry immediately',
    switched.lang === 'en' && switched.payload === 'en' && switched.source === 'Offline demo' && switched.registry && switched.name !== frenchName && switched.workshop === 'exercise/index.html?lang=en');
  await page.evaluate(() => window.__twin.openConsole('shadow'));
  check('Observatory label and console are translated after switching to English',
    await shadowMap.textContent() === 'AI Usage Observatory' && await page.locator('#cTitle').textContent() === 'AI Usage Observatory' &&
    await page.getByRole('table', { name: 'Measured adoption' }).count() === 1);
  await page.reload();
  await page.waitForFunction(() => window.__twin?.SNAP.meta.lang === 'en');
  check('saved locale survives a reload', await page.locator('#languageSelect').inputValue() === 'en');
  await page.selectOption('#languageSelect', 'fr');
  check('offline FR switch restores the French registry', await page.evaluate(() => window.__twin.UC['uc-04'].name) === frenchName);

  const fixtures = await page.evaluate(() => Object.fromEntries(['en', 'fr'].map(lang => [lang, JSON.parse(document.getElementById(`demo-snapshot-${lang}`).textContent)])));
  const portfolioFixture = await page.evaluate(() => window.FactoryPortfolio.demo('fr'));
  await page.close();
  const html = fs.readFileSync(dashboardPath, 'utf8');
  const mock = await ctx.newPage();
  mock.setDefaultTimeout(15000);
  await mock.clock.install();
  let shadowStatus = 404, holdShadow = null, shadowReads = 0, shadowRole = 'viewer', proposedPolicy = null, policyDecision = null;
  const shadowProposalId = '9'.repeat(32);
  const shadowMarkup = '<em data-xss-probe="shadow">legal</em>';
  const shadowFixture = () => ({
    privacy_threshold: 5,
    evidence: { window_start: '2026-09-01T00:00:00Z', window_end: '2026-10-01T00:00:00Z', events: 90, unique_users: 16 },
    team_usage: [{ period: '2026-09', team: shadowMarkup, tool: 'external-ai', kind: 'observation', people: 8, events: 33 }],
    adoption: [{ period: '2026-09', team: 'platform', tool: 'internal-ai', active: 10, eligible: 18 }],
    policies: [{ tool: 'internal-ai', status: 'approved', alternative: null, revision: 2 },
      ...(policyDecision ? [{ tool: 'external-ai', status: 'restricted', alternative: 'internal-ai', revision: 1 }] : [])],
    proposals: proposedPolicy && !policyDecision ? [{ ...proposedPolicy, id: shadowProposalId }] : [],
    capabilities: { propose: ['editor', 'admin'].includes(shadowRole), decide: shadowRole === 'approver' },
  });
  let apiReady = false, buMode = false, buAuthorized = true, buWrites = 0, unscopedFirstRead = false;
  const workbenchCase = 'a'.repeat(32);
  let workbenchRevision = 7, workbenchDecision = 'waiting', workbenchStatus = 200;
  const workbenchRequests = [];
  const activityRequests = [];
  const portfolioRequests = [];
  const assistRequests = [];
  const assistMarkup = '<em data-assist-injected>Suggestion du modèle local</em>';
  let assistStatus = 200, assistGetStatus = 200, holdAssist = null;
  let portfolioStatus = 200, portfolioConflict = false, portfolioRole = 'manager';
  let portfolioPaging = true, holdIdeaSearch = null;
  const pageCursor = 'next.' + 'a'.repeat(64);
  const ideaRequests = [];
  const traceRequests = [];
  const ideaMarkup = '<em data-idea-injected>Retour terrain</em>';
  const traceMarkup = '<em data-trace-injected>verified-subject</em>';
  let ideasStatus = 200, ideasViewer = '2'.repeat(64), canPostIdeas = true, canTraceIdeas = false, traceStatus = 200;
  const ideaCard = (id, bu, bu_label, title, category, tags) => ({
    id: id.repeat(32), bu, bu_label, title, text: 'Exemple partagé.',
    category, tags, visibility: 'factory', created_at: '2026-11-07T12:30:00+00:00',
  });
  let ideaItems = [
    ideaCard('1', 'cc.relation:client', 'Relation client', ideaMarkup, 'experience', ['feedback']),
    ideaCard('5', 'cc-ops', 'Opérations', 'Analyse du budget des interventions', 'finance', ['financial_analysis', 'budget']),
    ideaCard('6', 'cc-ops', 'Opérations', 'Automatiser les alertes', 'automation', ['workflow']),
    ideaCard('7', 'cc-finance', 'Finance', 'Automatiser les rapprochements', 'automation', ['workflow']),
    ideaCard('8', '@factory-administration', 'Administration Factory', 'Réunir les guides', 'adoption', ['documentation']),
    ideaCard('9', 'cc-ops', 'Opérations', 'Piste historique sur les archives', 'adoption', ['documentation']),
  ];
  let workbenchOutcome = null;
  const portfolioId = n => n.toString(16).padStart(32, '0');
  const activityDay = Math.floor(Date.now() / 86400000) * 86400000;
  const activityTime = (age, hour = 0) => new Date(activityDay - age * 86400000 + hour * 3600000).toISOString();
  const activityEntry = (id, actor_type, actor_id, user_id, started_at, finished_at, duration_ms, tokens) => ({
    id: id.repeat(32), actor_type, actor_id, user_id, unit: 'delivery', action: `Recorded action ${actor_id}`,
    started_at, finished_at, duration_ms, status: 'completed', tokens, supports: [], source: 'reported',
  });
  const activityOwn = [
    { ...activityEntry('a', 'agent', 'agent-alpha', 'operator-a', activityTime(2, 22), activityTime(1, 2), 14400000, { input: 100, output: 25, total: 125 }),
      supports: [{ kind: 'file', label: '<img data-activity-injected>', ref: 'opaque-12' }] },
    activityEntry('b', 'user', 'operator-a', 'operator-a', activityTime(1, .5), activityTime(1, 3), 9000000, { input: 0, output: 0, total: 0 }),
    { ...activityEntry('c', 'user', 'operator-a', 'operator-a', null, activityTime(3, 8), null, null), source: 'workbench' },
    activityEntry('d', 'user', 'operator-a', 'operator-a', activityTime(4, 8), activityTime(4, 8), 0, { input: 0, output: 0, total: 0 }),
  ];
  const activityExtra = { ...activityEntry('a', 'agent', 'agent-bravo', 'operator-b', activityTime(1, 1), activityTime(1, 2), 3600000, { input: 15, output: 5, total: 20 }), unit: 'platform' };
  let activityAllAllowed = true, activityEmpty = false, holdActivity = null, activityViewer = 'e'.repeat(64);
  let holdFrench = null;
  const snapshotCalls = [];
  const assessmentCalls = [];
  const reviewTarget = fixtures.en.registry.find(u => u.risk.reevaluation_required);
  const nextReviewRevision = 'f'.repeat(64);
  const tierMarkup = 'high" data-xss-probe="tier"><em data-xss-probe="tier">probe</em>';
  const codeMarkup = '<em data-xss-probe="code">UC-04</em>';
  const metricMarkup = '<em data-xss-probe="metric">metric</em>';
  await ctx.route('http://factory.test/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/') {
      return route.fulfill({ contentType: 'text/html; charset=utf-8', body: url.searchParams.has('static') ? html.replace('<html ', '<html data-static-demo="1" ') : html });
    }
    if (url.pathname === '/api/v1/snapshot') {
      const lang = url.searchParams.get('lang');
      snapshotCalls.push(lang);
      if (!apiReady || !fixtures[lang]) return route.fulfill({ status: 503, json: {} });
      const data = structuredClone(fixtures[lang]);
      if (buMode) {
        if (!buAuthorized) return route.fulfill({ status: 403, json: {} });
        if (unscopedFirstRead) { unscopedFirstRead = false; return route.fulfill({ json: data }); }
        data.meta.business_scope = true;
        data.meta.business_view = 'fictional-bu-view';
        data.meta.security = { ...data.meta.security, auth_mode: 'proxy' };
        data.profiles.people = data.profiles.people.filter(person => person.id === 'p02');
        data.registry = data.registry.filter(usecase => usecase.id === 'uc-01');
        return route.fulfill({ json: data });
      }
      data.meta.telemetry_collected_at = new Date().toISOString();
      if (assessmentCalls.length) {
        data.registry.find(u => u.id === reviewTarget.id).review_revision = nextReviewRevision;
      }
      const useCase = data.registry.find(u => u.id === 'uc-04');
      useCase.name += ' (API)';
      useCase.code = codeMarkup;
      useCase.risk.tier = tierMarkup;
      data.catalog.models[0].eval = { [metricMarkup]: 0.5 };
      if (lang === 'fr' && holdFrench) return holdFrench.then(() => route.fulfill({ json: data }));
      return route.fulfill({ json: data });
    }
    if (buMode && url.pathname === '/api/v1/spaces/state') {
      if (route.request().method() === 'PUT') buWrites++;
      return route.fulfill({ json: { version: 0, view_token: 'f'.repeat(64), state: { v: 2, bookings: [], dayStatus: {}, meta: { autoDemo: false } },
        scope: { write: true, employee: 'p02', resources: ['D1'], resource_usecases: { D1: ['uc-01'] }, owned: [] } } });
    }
    if (url.pathname === '/api/v1/workbench/encounters') {
      workbenchRequests.push({ method: route.request().method(), caseId: url.searchParams.get('case_id') });
      return route.fulfill({ status: workbenchStatus,
        json: workbenchStatus === 200 ? { encounters: [{ case_id: workbenchCase, revision: workbenchRevision, status: workbenchDecision }] } : {} });
    }
    if (url.pathname === '/api/v1/activity') {
      const days = Number(url.searchParams.get('days')), view = url.searchParams.get('view');
      activityRequests.push({ method: route.request().method(), days, view });
      if (view === 'all' && !activityAllAllowed) return route.fulfill({ status: 403, json: { detail: 'activity_scope_forbidden' } });
      const reply = { json: { events: activityEmpty ? [] : view === 'all' ? [...activityOwn, activityExtra] : activityOwn,
        truncated: view === 'all', since: activityTime(days - 1), until: activityTime(0, 12), view: activityViewer, can_view_all: activityAllAllowed } };
      return days === 30 && holdActivity ? holdActivity.then(() => route.fulfill(reply)) : route.fulfill(reply);
    }
    if (url.pathname === '/api/v1/ideas') {
      const method = route.request().method();
      const body = method === 'POST' ? route.request().postDataJSON() : null;
      const { q, category, bu, cursor } = Object.fromEntries(url.searchParams);
      ideaRequests.push({ method, body, language: url.searchParams.get('lang'), q, category, bu, cursor });
      if (ideasStatus !== 200) return route.fulfill({ status: ideasStatus, json: {} });
      if (method === 'POST') {
        const created = { id: '3'.repeat(32), ...body, bu_label: 'Delivery', category: 'automation',
          tags: ['workflow'], visibility: 'factory', created_at: '2026-11-07T12:31:00+00:00' };
        ideaItems = [created, ...ideaItems];
        return route.fulfill({ status: 201, json: created });
      }
      const matching = ideaItems.filter(row => (!bu || row.bu === bu) && (!q || row.title.toLowerCase().includes(q.toLowerCase())));
      const category_totals = {};
      for (const row of matching) category_totals[row.category] = (category_totals[row.category] || 0) + 1;
      const filtered = matching.filter(row => !category || row.category === category);
      const next = !cursor && filtered.length > 5 ? pageCursor : null;
      const reply = { json: { items: cursor ? filtered.slice(5) : filtered.slice(0, 5), view: ideasViewer,
        truncated: !!next, next_cursor: next, category_totals, total: filtered.length,
        units: canPostIdeas ? [{ id: 'cc.platform:ops', label: 'Delivery' }] : [], permissions: { create: canPostIdeas, trace: canTraceIdeas } } };
      return q === 'alertes' && holdIdeaSearch ? holdIdeaSearch.then(() => route.fulfill(reply)) : route.fulfill(reply);
    }
    const traceId = url.pathname.match(/^\/api\/v1\/ideas\/([a-f0-9]{32})\/trace$/)?.[1];
    if (traceId) {
      traceRequests.push({ id: traceId, method: route.request().method(), body: route.request().postDataJSON(), language: url.searchParams.get('lang') });
      if (!canTraceIdeas) return route.fulfill({ status: 403, json: {} });
      const row = ideaItems.find(item => item.id === traceId);
      if (traceStatus !== 200 || !row) return route.fulfill({ status: 404, json: { detail: 'idea_origin_unavailable' } });
      return route.fulfill({ json: { id: row.id, bu: row.bu, kind: 'employee', subject: traceMarkup, created_at: row.created_at, view: ideasViewer } });
    }
    if (url.pathname === '/api/v1/assist') {
      const method = route.request().method();
      assistRequests.push({ method, body: method === 'POST' ? route.request().postDataJSON() : null, lang: url.searchParams.get('lang') });
      if (method === 'GET') return route.fulfill({ status: assistGetStatus,
        json: assistGetStatus === 200 ? { units: [{ id: 'delivery', label: 'Delivery' }], view: portfolioFixture.view } : {} });
      const reply = { status: assistStatus, json: assistStatus === 200
        ? { text: assistMarkup, usage: { input: 16, output: 8, total: 24, source: 'provider_reported' }, view: portfolioFixture.view }
        : { detail: 'assist_unavailable' } };
      return holdAssist ? holdAssist.then(() => route.fulfill(reply)) : route.fulfill(reply);
    }
    if (url.pathname.startsWith('/api/v1/portfolio')) {
      const method = route.request().method();
      const cursor = url.searchParams.get('cursor'), limit = url.searchParams.get('limit');
      portfolioRequests.push({ method, path: url.pathname, body: method === 'POST' ? route.request().postDataJSON() : null, cursor, limit });
      if (portfolioStatus !== 200) return route.fulfill({ status: portfolioStatus, json: {} });
      if (method !== 'GET') return route.fulfill({ status: portfolioConflict ? 409 : 200, json: {} });
      const data = structuredClone(portfolioFixture);
      data.tasks = data.tasks.filter(row => row.unit === 'delivery');
      if (workbenchOutcome) {
        const handoff = data.tasks.find(row => row.id === portfolioId(23));
        handoff.status = workbenchOutcome === 'waiting' ? 'workbench' : 'done';
        if (workbenchOutcome === 'approve') {
          handoff.revision += 1;
          handoff.workbench_decision = { outcome: 'approve', at: '2026-11-07T12:30:00+00:00' };
        }
      }
      data.projects = data.projects.filter(project => data.tasks.some(task => task.project_id === project.id));
      data.goals = data.goals.filter(goal => data.projects.some(project => project.goal_id === goal.id));
      data.projects.push({ title: 'Projet d’une autre BU', state: 'active' });
      data.goals.push({ title: 'Objectif stratégique partagé', status: 'met' });
      if (portfolioPaging) {
        data.tasks = data.tasks.filter(task => (task.id === portfolioId(23)) === !!cursor);
        data.truncated = !cursor;
        data.next_cursor = cursor ? null : pageCursor;
      }
      data.capabilities = { ...data.capabilities, global_admin: false, roles_by_unit: { delivery: [portfolioRole] } };
      return route.fulfill({ json: data });
    }
    if (url.pathname === `/api/v1/registry/${reviewTarget.id}/assessment`) {
      assessmentCalls.push(route.request().postDataJSON());
      return route.fulfill({ status: 409, json: { code: 'stale_review_revision' } });
    }
    if (url.pathname === '/api/v1/shadow-ai') {
      shadowReads++;
      const reply = { status: shadowStatus, json: shadowStatus === 200 ? shadowFixture() : {} };
      return holdShadow ? holdShadow.then(() => route.fulfill(reply)) : route.fulfill(reply);
    }
    if (url.pathname === '/api/v1/shadow-ai/policies/proposals') {
      proposedPolicy = route.request().postDataJSON();
      return route.fulfill({ status: 201, json: {} });
    }
    if (url.pathname === `/api/v1/shadow-ai/policies/proposals/${shadowProposalId}/decision`) {
      policyDecision = route.request().postDataJSON();
      return route.fulfill({ status: 200, json: {} });
    }
    const local = path.join(root, 'dashboard', url.pathname.slice(1));
    return fs.existsSync(local) ? route.fulfill({ path: local }) : route.fulfill({ status: 404 });
  });
  await mock.addInitScript(() => {
    localStorage.setItem('aiFactoryRef.v2', JSON.stringify({ v: 2, bookings: [
      { id: 'legacy-private', resourceId: 'D1', employeeId: 'p02', ucId: 'uc-01', activity: 'delivery',
        date: '2026-11-06', start: 540, end: 600, note: 'legacy-private-note' },
    ], meta: { autoDemo: false } }));
  });
  await mock.goto('http://factory.test/');
  await mock.waitForFunction(() => window.__twin?.source === 'embedded');
  await sceneReady(mock);
  check('initial API failure falls back to the selected offline locale', snapshotCalls.join(',') === 'fr');
  check('a failed first API request never restores a legacy local planning cache', await mock.evaluate(() =>
    localStorage.getItem('aiFactoryRef.v2').includes('legacy-private-note') && !window.__twin.Store.d.bookings.some(b => b.id === 'legacy-private')));
  await mock.evaluate(() => window.__twin.openConsole('registry'));
  apiReady = true;
  await mock.clock.fastForward(30000);
  await mock.waitForFunction(() => window.__twin?.source === 'api');
  check('polling recovers the API and refreshes the source, telemetry age and open console',
    (await mock.locator('#srcBadge').innerText()).startsWith('API') && await mock.locator('#telemetryAge').isVisible() &&
    (await mock.locator('#cBody').textContent()).includes(`${frenchName} (API)`));
  check('risk badges keep their known class and render unknown tier markup as text',
    await mock.locator('#cBody .cchip.t-high').count() > 0 &&
    await mock.locator('#cBody [data-xss-probe]').count() === 0 &&
    (await mock.locator('#cBody').textContent()).includes(tierMarkup));
  await mock.selectOption('#languageSelect', 'en');
  await mock.waitForFunction(() => window.__twin?.SNAP.meta.lang === 'en');
  const englishName = fixtures.en.registry.find(u => u.id === 'uc-04').name;
  check('live EN switch requests and renders the selected API locale', snapshotCalls.at(-1) === 'en' &&
    (await mock.locator('#cBody').textContent()).includes(`${englishName} (API)`));
  const approve = mock.locator(`#cBody [data-cact="approve"][data-uc="${reviewTarget.id}"]`);
  await approve.click();
  await mock.waitForFunction(({ id, revision }) =>
    window.__twin.SNAP.registry.find(u => u.id === id).review_revision === revision,
  { id: reviewTarget.id, revision: nextReviewRevision });
  check('a stale approval submits the displayed revision once and refreshes the review',
    assessmentCalls.length === 1 && assessmentCalls[0].expected_review_revision === reviewTarget.review_revision &&
    await approve.getAttribute('data-review-revision') === nextReviewRevision);
  let releaseFrench;
  holdFrench = new Promise(resolve => { releaseFrench = resolve; });
  const frenchRequest = mock.waitForRequest(/\/api\/v1\/snapshot\?lang=fr$/);
  await mock.selectOption('#languageSelect', 'fr');
  await frenchRequest;
  const latestEnglish = mock.waitForResponse(/\/api\/v1\/snapshot\?lang=en$/);
  await mock.selectOption('#languageSelect', 'en');
  await latestEnglish;
  const staleFrench = mock.waitForResponse(/\/api\/v1\/snapshot\?lang=fr$/);
  releaseFrench();
  await (await staleFrench).finished();
  check('slow FR response cannot replace the newer EN selection',
    await mock.locator('html').getAttribute('lang') === 'en' && (await mock.locator('#cBody').textContent()).includes(`${englishName} (API)`));
  await mock.evaluate(() => window.__twin.openConsole('placement'));
  check('use-case codes render as text in related console sections',
    await mock.locator('#cBody [data-xss-probe]').count() === 0 &&
    (await mock.locator('#cBody').textContent()).includes(codeMarkup));
  await mock.evaluate(() => window.__twin.openConsole('catalog'));
  check('catalog evaluation keys render as text',
    await mock.locator('#cBody [data-xss-probe]').count() === 0 &&
    (await mock.locator('#cBody').textContent()).includes(metricMarkup));
  await mock.evaluate(() => window.__twin.closeConsole());
  await mock.locator('#tabSpaces').click();
  await mock.locator('#spacePanel button[data-res="X1"]').click();
  await mock.selectOption('#fUc', 'uc-04');
  await mock.selectOption('#languageSelect', 'fr');
  await mock.waitForFunction(() => window.__twin?.SNAP.meta.lang === 'fr');
  const useCaseSelect = mock.locator('#fUc');
  check('use-case options keep literal labels and selection after switching language',
    await useCaseSelect.inputValue() === 'uc-04' && await useCaseSelect.locator('option').first().getAttribute('value') === '' &&
    (await useCaseSelect.locator('option:checked').textContent()).includes(`Sensible · ${codeMarkup} · ${frenchName} (API)`) &&
    await useCaseSelect.locator('[data-xss-probe]').count() === 0);
  // The real scene was exercised above. The mocked console now needs no continuous software WebGL draws.
  await mock.evaluate(() => { window.__twin.renderer.render = () => {}; });
  const openShadow = () => mock.evaluate(() => window.__twin.openConsole('shadow'));
  const shadowReady = () => mock.locator('[data-shadow-state="ready"]').waitFor();
  const refreshShadow = () => mock.locator('[data-shadow-action="refresh"]').click();
  await openShadow();
  await mock.locator('[data-shadow-state="off"]').waitFor();
  check('live disabled Observatory never displays fictional rows', await mock.locator('[data-shadow-state="demo"]').count() === 0 && await mock.locator('.shadow-metrics').count() === 0);
  shadowStatus = 503;
  await refreshShadow();
  await mock.locator('[data-shadow-state="error"]').waitFor();
  check('live outage displays a retryable error without sample data', await mock.locator('[data-shadow-state="demo"]').count() === 0 && await mock.locator('[data-shadow-action="refresh"]').isEnabled());
  shadowStatus = 200;
  await refreshShadow();
  await shadowReady();
  check('protected aggregates are rendered as text and remain outside the snapshot',
    (await mock.locator('#cBody').textContent()).includes(shadowMarkup) && await mock.locator('#cBody [data-xss-probe]').count() === 0 &&
    await mock.evaluate(() => !JSON.stringify(window.__twin.SNAP).includes('external-ai')));
  check('viewers see protected aggregates without write controls', await mock.locator('#shadowProposal').count() === 0 && await mock.locator('[data-shadow-action^="approve-"]').count() === 0);
  shadowRole = 'editor';
  await refreshShadow();
  await mock.locator('#shadowProposal [name="tool"]').fill('external-ai');
  await mock.locator('#shadowProposal [name="status"]').selectOption('restricted');
  await mock.locator('#shadowProposal [name="alternative"]').selectOption('internal-ai');
  await mock.locator('#shadowProposal button').click();
  await mock.getByRole('table', { name: 'Décisions en attente' }).waitFor();
  check('editor can propose a restricted tool and approved alternative but cannot decide',
    proposedPolicy?.tool === 'external-ai' && proposedPolicy?.expected_revision === 0 && proposedPolicy?.status === 'restricted' &&
    proposedPolicy?.alternative === 'internal-ai' && await mock.locator('[data-shadow-action^="approve-"]').count() === 0);
  shadowRole = 'approver';
  const [approverRefresh] = await Promise.all([
    mock.waitForResponse(r => r.url().endsWith('/api/v1/shadow-ai') && r.request().method() === 'GET'),
    refreshShadow(),
  ]);
  const approverData = await approverRefresh.json();
  if (!approverRefresh.ok() || approverData.capabilities?.decide !== true || !approverData.proposals?.some(p => p.id === shadowProposalId)) {
    throw new Error('Observatory approver refresh did not return the pending proposal with decision access');
  }
  await mock.locator(`[data-shadow-action="approve-${shadowProposalId}"]`).click();
  await mock.getByRole('table', { name: 'Gouvernance des outils' }).getByText('external-ai').waitFor();
  check('the dedicated approver refreshes the policy instead of assuming local approval',
    policyDecision?.decision === 'approve' && await mock.locator('[data-shadow-action^="approve-"]').count() === 0);
  shadowStatus = 403;
  await refreshShadow();
  await mock.locator('[data-shadow-state="blocked"]').waitFor();
  check('loss of access removes previously displayed aggregates and governance actions',
    await mock.locator('.shadow-metrics').count() === 0 && await mock.locator('#shadowProposal').count() === 0);
  await mock.evaluate(() => window.__twin.closeConsole());
  shadowStatus = 200;
  let releaseShadow;
  holdShadow = new Promise(resolve => { releaseShadow = resolve; });
  const shadowRequest = mock.waitForRequest(/\/api\/v1\/shadow-ai$/);
  await openShadow();
  await shadowRequest;
  await mock.evaluate(() => window.__twin.openConsole('catalog'));
  const shadowResponse = mock.waitForResponse(/\/api\/v1\/shadow-ai$/);
  releaseShadow();
  await (await shadowResponse).finished();
  check('a delayed Observatory response cannot repaint another console', await mock.locator('[data-shadow-state]').count() === 0 && (await mock.locator('#cTitle').textContent()).includes('Catalogue'));
  await mock.close();

  buMode = true;
  unscopedFirstRead = true; // rolling transition: the first snapshot predates the scoped booking API
  const scoped = await ctx.newPage();
  await scoped.emulateMedia({ reducedMotion: 'reduce' });
  await scoped.clock.install();
  await scoped.addInitScript(() => {
    localStorage.setItem('aiFactoryRef.v2', JSON.stringify({ v: 2, bookings: [], meta: { autoDemo: true } }));
    localStorage.setItem('aiFactoryRef.v2.backup', 'legacy-booking-private');
  });
  await scoped.goto('http://factory.test/?lang=fr', { waitUntil: 'commit' });
  await scoped.waitForFunction(() => window.__appReady && window.__twin?.source === 'api', null, { timeout: 30000 });
  await sceneReady(scoped);
  await scoped.clock.fastForward(1500);
  check('scoped booking response revalidates the snapshot, purges browser caches and does not reseed', buWrites === 0 &&
    await scoped.evaluate(() => window.__twin.SNAP.meta.business_scope === true) &&
    await scoped.evaluate(() => localStorage.getItem('aiFactoryRef.v2') === null && localStorage.getItem('aiFactoryRef.v2.backup') === null));
  check('protected boot stays live and hides the public shortcuts', await scoped.evaluate(() => window.__twin.S.live) &&
    !(await scoped.locator('#demoEntry').isVisible()));
  await scoped.evaluate(() => { window.__twin.renderer.render = () => {}; });
  await scoped.locator('#encounterOpen').click();
  check('Workbench projection requires explicit activation and exposes no demo control',
    workbenchRequests.length === 0 && !(await scoped.locator('#encounterScenarioField').isVisible()));
  await scoped.locator('#encounterFollow').click();
  const waiting = scoped.locator('#encounterPanel[data-stage="waiting"]');
  await waiting.waitFor();
  check('authorized Workbench case uses symbolic participants and a link to the decision companion',
    await scoped.locator('#encounterCompanion').isVisible() && !(await scoped.locator('#encounterForm').isVisible()) &&
    await scoped.locator('#encounterCompanion').getAttribute('target') === '_blank' &&
    !(await scoped.locator('#encounterPanel').textContent()).includes(workbenchCase));
  workbenchRevision++;
  workbenchDecision = 'approved';
  await scoped.clock.fastForward(5000);
  await scoped.locator('#encounterStatus').getByText('La révision suivie est indisponible.', { exact: false }).waitFor();
  check('a decision at another revision clears the exchange',
    !(await scoped.locator('#encounterQuestion').isVisible()) && !(await scoped.locator('#encounterReply').isVisible()));
  workbenchDecision = 'waiting';
  await scoped.clock.fastForward(5000);
  await waiting.waitFor();
  workbenchDecision = 'approved';
  await scoped.clock.fastForward(5000);
  await scoped.locator('#encounterReply').getByText('Décision consignée : approuvée.').waitFor();
  check('the exact Workbench decision advances the animation with targeted GET requests only',
    workbenchRequests.every(request => request.method === 'GET') && workbenchRequests[0]?.caseId === null &&
    workbenchRequests.some(request => request.caseId === workbenchCase));
  workbenchStatus = 403;
  await scoped.clock.fastForward(5000);
  await scoped.locator('#encounterStatus').getByText('Vous n’avez pas accès au suivi du Workbench.').waitFor();
  check('Workbench authorization loss clears the panel decision and stops following',
    !(await scoped.locator('#encounterReply').isVisible()) && await scoped.locator('#encounterFollow').isVisible());
  await scoped.keyboard.press('Escape');
  await scoped.locator('#tabPilot').click();
  await scoped.locator('#pilotPanel [data-activity-open]').click();
  const ledger = scoped.locator('.activity');
  const activityState = state => scoped.locator(`.activity[data-activity-state="${state}"]`);
  const filter = key => ledger.locator(`[data-activity-filter="${key}"]`);
  const total = key => ledger.locator(`[data-activity-total="${key}"]`).textContent();
  await activityState('ready').waitFor();
  check('protected history defaults to own, unions overlapping traces per user and does not render markup',
    activityRequests[0]?.view === 'own' && await total('duration') === '5 h' && await total('tokens') === '125' &&
    !(await ledger.textContent()).includes('operator-b') && (await ledger.locator('.activity-supports').textContent()).includes('<img data-activity-injected>') &&
    await ledger.locator('[data-activity-injected]').count() === 0);
  check('UTC tracks split intervals at midnight, credit tokens at finish and distinguish zero from unknown',
    await ledger.locator('[data-track="duration"] .activity-tick').count() === 7 &&
    JSON.stringify(await ledger.locator('.activity-daily tbody tr').evaluateAll(rows => rows.slice(-5, -1).map(row => [...row.cells].slice(1).map(td => td.textContent)))) ===
      JSON.stringify([['0 min', '0'], ['Non renseigné', 'Non renseigné'], ['2 h', 'Non renseigné'], ['3 h', '125']]) &&
    await ledger.locator('[data-track="tokens"] .zero').count() === 1 && await ledger.locator('[data-track="tokens"] .unknown').count() === 1);
  await filter('kind').selectOption('agent');
  await filter('subject').selectOption('agent-alpha');
  check('agent history exact filter excludes human records and keeps token attribution',
    await total('duration') === '4 h' && await total('events') === '1' && await total('tokens') === '125' && activityRequests.length === 1);
  await filter('kind').selectOption('user');
  await filter('scope').selectOption('all');
  await activityState('ready').waitFor();
  check('explicit authorized all scope shows another owner with the same scoped event ID and flags a partial result',
    await total('duration') === '6 h' && await total('tokens') === '145' && await total('events') === '5' &&
    (await ledger.textContent()).includes('operator-b') && await ledger.locator('.activity-partial').isVisible());
  activityViewer = 'f'.repeat(64);
  const revalidatedActivity = scoped.waitForResponse(res => res.url().includes('/api/v1/activity?days=7&view=own'));
  await scoped.clock.fastForward(20000);
  await revalidatedActivity;
  await activityState('ready').waitFor();
  check('changed authorized identity resets all to own even when the booking scope did not change',
    await filter('scope').inputValue() === 'own' && !(await ledger.textContent()).includes('operator-b'));
  await filter('scope').selectOption('all');
  await activityState('ready').waitFor();
  activityAllAllowed = false;
  await scoped.clock.fastForward(20000);
  await activityState('blocked').waitFor();
  check('administrator downgrade clears all history and the scope control on revalidation',
    await filter('scope').count() === 0 && await ledger.locator('.activity-event').count() === 0 &&
    !(await ledger.textContent()).includes('operator-b') && activityRequests.at(-1)?.view === 'all');
  activityEmpty = true;
  await ledger.locator('[data-activity-retry]').click();
  await ledger.getByText('Aucune activité enregistrée sur cette période.').waitFor();
  check('retry after lost access returns to own scope and exposes a true empty state', activityRequests.at(-1)?.view === 'own');
  activityEmpty = false;
  let releaseActivity;
  holdActivity = new Promise(resolve => { releaseActivity = resolve; });
  const previousActivity = scoped.waitForResponse(res => new URL(res.url()).searchParams.get('days') === '30');
  await filter('days').selectOption('30');
  await filter('days').selectOption('90');
  await activityState('ready').waitFor();
  releaseActivity(); holdActivity = null;
  await (await previousActivity).finished();
  check('late activity responses cannot overwrite the new period and the journal only reads the protected route',
    await ledger.locator('[data-track="duration"] .activity-tick').count() === 90 && activityRequests.every(req => req.method === 'GET'));
  await scoped.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })));
  check('pagehide removes protected records before a document can enter browser history', await ledger.count() === 0);
  await scoped.locator('#pilotPanel [data-portfolio-open]').click();
  const protectedPortfolio = scoped.locator('.portfolio[data-portfolio-state="ready"]');
  await protectedPortfolio.waitFor();
  const planTask = (screen, number) => screen.locator(`[data-portfolio-choice="task:${portfolioId(number)}"]`);
  const moreTasks = protectedPortfolio.getByRole('button', { name: 'Charger plus de tâches' });
  check('strategic summaries render as plain cards with status and no links',
    await protectedPortfolio.locator('article.portfolio-card').count() === 2 &&
    await protectedPortfolio.getByText('Objectif stratégique partagé').isVisible() &&
    await protectedPortfolio.getByText('Cible atteinte', { exact: true }).isVisible() &&
    await planTask(protectedPortfolio, 23).count() === 0);
  await moreTasks.click();
  await planTask(protectedPortfolio, 23).waitFor();
  check('portfolio load more appends authorized tasks with the server cursor',
    await planTask(protectedPortfolio, 22).isVisible() && portfolioRequests.at(-1).cursor === pageCursor &&
    portfolioRequests.at(-1).limit === '50' && await moreTasks.count() === 0);
  const portfolioRevalidation = scoped.waitForResponse(res => res.url().includes('/api/v1/portfolio?') &&
    new URL(res.url()).searchParams.get('cursor') === pageCursor);
  await scoped.clock.fastForward(20000);
  await (await portfolioRevalidation).finished();
  check('portfolio revalidation retains the loaded page prefix', await planTask(protectedPortfolio, 23).isVisible());
  portfolioPaging = false;
  await planTask(protectedPortfolio, 22).click();
  check('protected portfolio renders only scoped work and a BU manager sees assignment without planning or execution',
    await protectedPortfolio.getByRole('button', { name: 'Attribuer', exact: true }).isVisible() &&
    await protectedPortfolio.getByRole('button', { name: 'Nouvelle tâche', exact: true }).count() === 0 &&
    await protectedPortfolio.getByRole('button', { name: 'Démarrer', exact: true }).count() === 0 &&
    !((await protectedPortfolio.textContent()).includes('Configurer l’indexation')));
  portfolioConflict = true;
  await protectedPortfolio.getByRole('button', { name: 'Attribuer', exact: true }).click();
  await protectedPortfolio.locator('[data-portfolio-field="assignee"]').selectOption('employee:camille');
  await protectedPortfolio.getByRole('button', { name: 'Confirmer', exact: true }).click();
  await protectedPortfolio.getByText('Cette révision a changé.', { exact: false }).waitFor();
  const portfolioWrite = portfolioRequests.find(request => request.method === 'POST');
  check('portfolio conflicts use the exact revision and reload the authorized projection',
    portfolioWrite?.path === `/api/v1/portfolio/tasks/${portfolioId(22)}/commands` &&
    portfolioWrite.body?.revision === 1 && portfolioWrite.body?.assignee?.id === 'camille' &&
    portfolioRequests.at(-1)?.method === 'GET' && await protectedPortfolio.locator('[data-portfolio-form]').count() === 0);
  portfolioConflict = false;
  await planTask(protectedPortfolio, 21).click();
  await protectedPortfolio.locator(`[data-portfolio-action="command:delegate:${portfolioId(21)}"]`).click();
  await protectedPortfolio.locator('[data-portfolio-field="title"]').fill('Sous-tâche à identifiant serveur');
  await protectedPortfolio.locator('[data-portfolio-field="mode"]').selectOption('automatic');
  await protectedPortfolio.locator('[data-portfolio-form] button[type="submit"]').click();
  await protectedPortfolio.getByText('Modification consignée.', { exact: true }).waitFor();
  const delegateWrite = portfolioRequests.find(request => request.path.endsWith('/subagents'));
  portfolioRole = 'project_lead';
  await scoped.clock.fastForward(20000);
  await protectedPortfolio.getByRole('button', { name: 'Nouvelle tâche', exact: true }).click();
  await protectedPortfolio.locator('[data-portfolio-field="title"]').fill('Tâche à identifiant serveur');
  const protectedDescription = protectedPortfolio.locator('[data-portfolio-field="description"]');
  await protectedDescription.fill('Ma description initiale.');
  check('a regular BU user has no decision preparation entry', await protectedPortfolio.locator('[data-assist-open="decision_outline"]').count() === 0);
  const protectedAssist = scoped.getByRole('region', { name: 'Rédiger une tâche' });
  const portfolioWritesBeforeAssist = portfolioRequests.filter(request => request.method === 'POST').length;
  check('opening the task form alone does not contact the local model', assistRequests.length === 0);
  await protectedPortfolio.locator('[data-assist-open="draft_task"]').click();
  const generate = protectedAssist.getByRole('button', { name: 'Générer une suggestion' });
  assistStatus = 503;
  await generate.click();
  await protectedAssist.getByText('La suggestion locale n’a pas pu être vérifiée.').waitFor();
  check('private assistance failure leaves the draft unchanged and gives no demo suggestion',
    await protectedDescription.inputValue() === 'Ma description initiale.' &&
    await protectedAssist.getByRole('button', { name: 'Utiliser', exact: true }).count() === 0);
  assistStatus = 200;
  let releaseAssist;
  holdAssist = new Promise(resolve => { releaseAssist = resolve; });
  const lateSuggestion = scoped.waitForRequest(request => request.url().includes('/api/v1/assist?') && request.method() === 'POST');
  await generate.click();
  await lateSuggestion;
  assistGetStatus = 403;
  releaseAssist(); holdAssist = null;
  await protectedAssist.getByText('L’assistance locale n’est pas autorisée dans ce contexte.').waitFor();
  check('a late local result is hidden if fresh access verification is revoked',
    await protectedAssist.getByText(assistMarkup, { exact: true }).count() === 0 &&
    await protectedAssist.getByRole('button', { name: 'Utiliser', exact: true }).count() === 0 &&
    assistRequests.at(-1).method === 'GET');
  await protectedAssist.getByRole('button', { name: 'Fermer l’assistance' }).click();
  assistGetStatus = 200;
  await protectedPortfolio.locator('[data-assist-open="draft_task"]').click();
  await generate.click();
  await protectedAssist.getByText(assistMarkup, { exact: true }).waitFor();
  check('local draft uses explicit, bounded same-origin input and renders the result as text',
    await protectedAssist.locator('[data-assist-injected]').count() === 0 &&
    await protectedAssist.getByText('24 tokens mesurés', { exact: false }).isVisible() &&
    JSON.stringify(assistRequests.filter(request => request.method === 'POST').at(-1)) === JSON.stringify({ method: 'POST',
      body: { kind: 'draft_task', bu: 'delivery', title: 'Tâche à identifiant serveur', text: 'Ma description initiale.', source: 'internal' }, lang: 'fr' }));
  await protectedDescription.fill('Mon brouillon modifié.');
  await protectedAssist.getByText('La tâche a changé. Générez une nouvelle suggestion.').waitFor();
  check('an edit invalidates the stale suggestion', await protectedAssist.getByRole('button', { name: 'Utiliser', exact: true }).count() === 0);
  await generate.click();
  await protectedAssist.getByText(assistMarkup, { exact: true }).waitFor();
  assistGetStatus = 403;
  await scoped.clock.fastForward(20000);
  await protectedAssist.getByText('L’assistance locale n’est pas autorisée dans ce contexte.').waitFor();
  check('an open editor does not stop private assistance from clearing revoked suggestions',
    await protectedAssist.getByText(assistMarkup, { exact: true }).count() === 0);
  await protectedAssist.getByRole('button', { name: 'Fermer l’assistance' }).click();
  assistGetStatus = 200;
  await protectedPortfolio.locator('[data-assist-open="draft_task"]').click();
  await generate.click();
  await protectedAssist.getByRole('button', { name: 'Utiliser', exact: true }).click();
  await scoped.locator('#assistHost').waitFor({ state: 'hidden' });
  check('explicit use appends to the draft without submitting or persisting prompts',
    await protectedDescription.inputValue() === `Mon brouillon modifié.\n\n${assistMarkup}` &&
    portfolioRequests.filter(request => request.method === 'POST').length === portfolioWritesBeforeAssist &&
    await scoped.evaluate(() => !JSON.stringify(localStorage).includes('Mon brouillon modifié.')) &&
    await scoped.locator('#assistHost').isHidden());
  await protectedPortfolio.locator('[data-portfolio-form] button[type="submit"]').click();
  await protectedPortfolio.getByText('Modification consignée.', { exact: true }).waitFor();
  const taskWrite = portfolioRequests.find(request => request.path === '/api/v1/portfolio/tasks');
  check('protected task and subagent creation leave identifiers to the server',
    delegateWrite?.body?.revision === 1 && delegateWrite.body.mode === 'automatic' && delegateWrite.body.description === '' && !Object.hasOwn(delegateWrite.body, 'id') &&
    taskWrite?.body?.unit === 'delivery' && !Object.hasOwn(taskWrite.body, 'id'));
  workbenchOutcome = 'waiting';
  await scoped.clock.fastForward(20000);
  await planTask(protectedPortfolio, 23).click();
  const decisionDetail = protectedPortfolio.locator('[data-portfolio-detail]');
  await decisionDetail.getByRole('link', { name: 'Ouvrir Companion' }).waitFor();
  workbenchOutcome = 'approve';
  await scoped.clock.fastForward(20000);
  await decisionDetail.getByText('Dernière décision Workbench', { exact: true }).waitFor();
  check('exact Workbench outcome refreshes the selected task and retains drawer focus',
    (await decisionDetail.textContent()).includes('Approuvée') &&
    await decisionDetail.getByRole('link', { name: 'Ouvrir Companion' }).count() === 0 &&
    await decisionDetail.evaluate(node => node === document.activeElement));
  portfolioStatus = 403;
  await scoped.clock.fastForward(20000);
  await scoped.locator('.portfolio[data-portfolio-state="blocked"]').waitFor();
  check('portfolio downgrade clears all previously rendered tasks and controls',
    await scoped.locator('.portfolio-task').count() === 0 && await scoped.locator('[data-portfolio-form]').count() === 0);
  await scoped.keyboard.press('Escape');
  await scoped.locator('#pilotPanel [data-ideas-open]').click();
  const protectedIdeas = scoped.locator('.ideas[data-ideas-state="ready"]');
  await protectedIdeas.getByRole('heading', { name: ideaMarkup }).waitFor();
  check('protected idea wall renders a shared note from another BU as text only',
    await protectedIdeas.getByRole('button', { name: 'BU · Relation client', exact: true }).isVisible() &&
    await protectedIdeas.locator('[data-idea-injected]').count() === 0 &&
    await protectedIdeas.locator('[data-idea-trace]').count() === 0 && traceRequests.length === 0);
  const ideaNote = id => protectedIdeas.locator(`[data-idea-id="${id.repeat(32)}"]`);
  const ideaSearch = protectedIdeas.locator('#ideas-search');
  const olderIdea = ideaNote('9');
  check('older ideas start outside the first page', await olderIdea.count() === 0);
  await protectedIdeas.getByRole('button', { name: 'Charger plus d’idées' }).click();
  await olderIdea.waitFor();
  check('the idea wall loads older entries with a cursor and preserves the first page',
    ideaRequests.at(-1).cursor === pageCursor && await ideaNote('1').isVisible());
  const ideaRevalidation = scoped.waitForResponse(res => res.url().includes('/api/v1/ideas?') &&
    new URL(res.url()).searchParams.get('cursor') === pageCursor);
  await scoped.clock.fastForward(20000);
  await (await ideaRevalidation).finished();
  check('idea wall revalidation retains the loaded page prefix', await olderIdea.isVisible());
  let releaseIdeaSearch;
  holdIdeaSearch = new Promise(resolve => { releaseIdeaSearch = resolve; });
  const slowIdeaRequest = scoped.waitForRequest(req => new URL(req.url()).searchParams.get('q') === 'alertes');
  const slowIdeaResponse = scoped.waitForResponse(res => new URL(res.url()).searchParams.get('q') === 'alertes');
  await ideaSearch.fill('alertes');
  await slowIdeaRequest;
  await ideaSearch.fill('piste historique');
  await olderIdea.waitFor();
  releaseIdeaSearch(); holdIdeaSearch = null;
  await (await slowIdeaResponse).finished();
  check('protected idea search reaches old notes and ignores a stale response',
    ideaRequests.at(-1).q === 'piste historique' && await olderIdea.isVisible() && await ideaNote('6').count() === 0);
  await ideaSearch.fill('');
  await ideaNote('1').waitFor();
  const paper = id => ideaNote(id).evaluate(el => getComputedStyle(el).backgroundColor);
  check('BU colors follow provenance, including transversal administration, independently of topic',
    await ideaNote('5').getByRole('button', { name: 'BU · Opérations' }).isVisible() &&
    await ideaNote('5').locator('.idea-category').textContent() === 'Finance' &&
    await ideaNote('8').getByRole('button', { name: 'Administration Factory', exact: true }).isVisible() &&
    await paper('5') === await paper('6') && await paper('6') !== await paper('7'));
  await ideaNote('5').getByRole('button', { name: 'BU · Opérations' }).click();
  const clearIdeaUnit = protectedIdeas.getByRole('button', { name: 'Effacer le filtre BU · Opérations' });
  const ideaTheme = protectedIdeas.locator('[data-idea-filter="finance"]');
  await ideaTheme.click();
  await ideaSearch.fill('budget des interventions');
  await ideaNote('5').waitFor();
  check('the BU pill composes with search and topic and can be cleared independently',
    await clearIdeaUnit.isVisible() && await protectedIdeas.locator('.idea-note').count() === 1 &&
    ideaRequests.at(-1).bu === 'cc-ops' && ideaRequests.at(-1).category === 'finance');
  await clearIdeaUnit.click();
  await ideaNote('5').waitFor();
  check('clearing BU preserves the other idea filters',
    await ideaTheme.getAttribute('aria-pressed') === 'true' && await protectedIdeas.locator('#ideas-search').inputValue() === 'budget des interventions');
  await protectedIdeas.getByRole('button', { name: '+ Ajouter une idée' }).click();
  check('idea composer states the Factory-wide visibility before posting',
    await protectedIdeas.getByText('Visible par toute la Factory dès l’envoi.').isVisible());
  await protectedIdeas.locator('#idea-title').fill('Automatisation des passations');
  await protectedIdeas.locator('#idea-text').fill('Proposer un rappel partagé pour chaque handover.');
  await protectedIdeas.getByRole('button', { name: 'Déposer mon idée' }).click();
  await protectedIdeas.getByText('Idée déposée · Automatisation').waitFor();
  const ideaWrite = ideaRequests.find(request => request.method === 'POST');
  check('the posted idea is classified by the API and remains off browser storage',
    ideaWrite?.body?.bu === 'cc.platform:ops' && ideaWrite?.language === 'fr' && !Object.hasOwn(ideaWrite.body, 'category') &&
    await protectedIdeas.getByRole('heading', { name: 'Automatisation des passations' }).isVisible() &&
    await scoped.evaluate(() => localStorage.getItem(window.FactoryIdeas.storeKey) === null));
  canTraceIdeas = true; ideasViewer = '6'.repeat(64);
  await scoped.clock.fastForward(20000);
  await ideaNote('1').locator('[data-idea-trace]').click();
  const ideaTrace = protectedIdeas.getByRole('region', { name: 'Traçabilité du dépôt' });
  await ideaTrace.getByText(traceMarkup, { exact: true }).waitFor();
  check('admin trace uses one explicit exact POST and keeps the identity outside the wall and storage',
    traceRequests.length === 1 && traceRequests[0].id === '1'.repeat(32) && traceRequests[0].method === 'POST' &&
    traceRequests[0].language === 'fr' && JSON.stringify(traceRequests[0].body) === '{}' &&
    await ideaTrace.locator('[data-trace-injected]').count() === 0 && !((await protectedIdeas.locator('.ideas-wall').textContent()).includes(traceMarkup)) &&
    await scoped.evaluate(() => !JSON.stringify(localStorage).includes('verified-subject')));
  await ideaTrace.getByRole('button', { name: 'Fermer la traçabilité' }).click();
  check('closing idea trace removes the author from the document and restores focus',
    await ideaTrace.count() === 0 && await protectedIdeas.getByText(traceMarkup, { exact: true }).count() === 0 &&
    await ideaNote('1').locator('[data-idea-trace]').evaluate(el => el === document.activeElement));
  traceStatus = 404;
  await ideaNote('5').locator('[data-idea-trace]').click();
  await ideaTrace.getByText('Aucune origine n’a été enregistrée pour ce dépôt.').waitFor();
  check('legacy idea trace shows an unavailable state with no author', await ideaTrace.locator('dd').count() === 0);
  traceStatus = 200;
  await ideaNote('1').locator('[data-idea-trace]').click();
  await ideaTrace.getByText(traceMarkup, { exact: true }).waitFor();
  await protectedIdeas.getByRole('button', { name: '+ Ajouter une idée' }).click();
  await protectedIdeas.locator('#idea-title').fill('Do not retain this draft');
  ideasViewer = '4'.repeat(64); canPostIdeas = false; canTraceIdeas = false; ideaItems = [];
  await scoped.clock.fastForward(20000);
  await protectedIdeas.getByText('Le mur est encore vide').waitFor();
  check('idea wall replaces the previous viewer projection and draft on revalidation',
    await protectedIdeas.locator('.idea-note').count() === 0 && !await protectedIdeas.locator('#ideas-compose').isVisible() &&
    await protectedIdeas.locator('#idea-title').inputValue() === '' &&
    await protectedIdeas.getByRole('button', { name: '+ Ajouter une idée' }).count() === 0 &&
    await protectedIdeas.getByText(traceMarkup, { exact: true }).count() === 0 && await ideaTrace.count() === 0);
  ideasStatus = 403;
  await scoped.clock.fastForward(20000);
  await scoped.locator('.ideas[data-ideas-state="denied"]').waitFor();
  check('idea wall fails closed on permission loss without the demo fixture',
    await scoped.locator('.idea-note').count() === 0 && await scoped.locator('.ideas-mode').textContent() === 'Mur de la Factory');
  await scoped.keyboard.press('Escape');
  await scoped.locator('#tabSpaces').click();
  await scoped.locator('#spacePanel button[data-res="D1"]').click();
  check('protected scheduling presents only its authorized case and verified booking identity',
    await scoped.locator('#fSubmit').isEnabled() && await scoped.locator('#fEmp').inputValue() === 'p02' &&
    await scoped.locator('#fUc').inputValue() === 'uc-01');
  await scoped.keyboard.press('Escape');
  await scoped.locator('#spacePanel button[data-res="B1"]').click();
  check('protected scheduling disables booking an out-of-scope resource', await scoped.locator('#fSubmit').isDisabled());
  buAuthorized = false;
  await scoped.clock.fastForward(30000);
  await scoped.locator('#businessOffline').waitFor();
  check('loss of access conceals the protected dashboard without switching to the fictional schedule',
    await scoped.locator('#app').evaluate(el => el.inert) && await scoped.evaluate(() => window.__twin?.source === 'none') && buWrites === 0);
  await scoped.close();
  buMode = false;

  // Test a fresh static boot after releasing the live renderer and its clock.
  const callsBeforeStatic = snapshotCalls.length;
  const shadowCallsBeforeStatic = shadowReads;
  const ideaCallsBeforeStatic = ideaRequests.length;
  const workbenchCallsBeforeStatic = workbenchRequests.length;
  const activityCallsBeforeStatic = activityRequests.length;
  const portfolioCallsBeforeStatic = portfolioRequests.length;
  const assistCallsBeforeStatic = assistRequests.length;
  const staticPage = await ctx.newPage();
  const portraitScriptReads = [];
  staticPage.on('request', request => {
    const name = new URL(request.url()).pathname.split('/').at(-1);
    if (name === 'portrait-atlases.js' || name === 'camille-model.js') portraitScriptReads.push(name);
  });
  await staticPage.emulateMedia({ reducedMotion: 'reduce' });
  await staticPage.clock.install();
  await staticPage.goto('http://factory.test/?static', { waitUntil: 'commit', timeout: 15000 });
  await staticPage.waitForFunction(() => window.__appReady === true && window.__twin?.source === 'embedded', null, { timeout: 30000 });
  await staticPage.evaluate(() => window.__twin.openConsole('shadow'));
  await staticPage.locator('[data-shadow-state="demo"]').waitFor();
  await staticPage.keyboard.press('Escape');
  await sceneReady(staticPage);
  await staticPage.evaluate(() => { window.__twin.renderer.render = () => {}; });
  await seatDemoRoster(staticPage);
  await staticPage.waitForFunction(() => Object.values(window.__twin.VIS).flatMap(space => space.avatars)
    .filter(avatar => avatar.portrait).length === 12, null, { timeout: 15000 });
  const rosterPortraits = await mountedSeatPortraits(staticPage);
  check('all twelve fictional identities mount distinct 3D portraits at their desks', new Set(rosterPortraits).size === 12);
  check('all twelve portraits use calibrated neck heights and keep the garment collar', await staticPage.evaluate(() => {
    const avatars = Object.values(window.__twin.VIS).flatMap(space => space.avatars).filter(avatar => avatar.portrait);
    const drops = new Map(avatars.map(avatar => [avatar.emp, Math.round(-avatar.portrait.userData.headOffsetY * 1000)]));
    return drops.get('p10') === 90 && drops.get('p06') === 63 && avatars.every(avatar => {
      const neck = avatar.body.getObjectByName('neck'), drop = drops.get(avatar.emp);
      return drop >= 63 && drop <= 97 && neck?.visible && neck.scale.y > 0 && neck.scale.y < 1 &&
        neck.material !== avatar.skinMat && avatar.body.getObjectByName('collar')?.visible &&
        typeof avatar.releasePortraitNeck === 'function';
    });
  }));
  check('all twelve garments keep one closed indexed surface when the actor raises an arm', await staticPage.evaluate(() => {
    const avatars = Object.values(window.__twin.VIS).flatMap(space => space.avatars).filter(avatar => avatar.portrait);
    return avatars.every(avatar => {
      const geometry = avatar.body.getObjectByName('garment')?.geometry;
      const index = geometry?.index, position = geometry?.getAttribute('position');
      if (!index || !position) return false;
      const parent = Int32Array.from({ length: position.count }, (_, i) => i), edges = new Map();
      const root = index => { while (index !== parent[index]) index = parent[index]; return index; };
      for (let i = 0; i < index.count; i += 3) {
        const a = index.getX(i), b = index.getX(i + 1), c = index.getX(i + 2);
        if (a === b || b === c || c === a) return false;
        for (const [u, v] of [[a, b], [b, c], [c, a]]) {
          const edge = Math.min(u, v) * position.count + Math.max(u, v);
          edges.set(edge, (edges.get(edge) || 0) + 1);
          parent[root(u)] = root(v);
        }
      }
      if (new Set(parent.map((_, i) => root(i))).size !== 1 || [...edges.values()].some(uses => uses !== 2)) return false;
      const seated = position.array.slice();
      avatar.rig.pose('request', 1.35);
      const bent = geometry.index === index && position.array.some((coordinate, i) => Math.abs(coordinate - seated[i]) > .001);
      avatar.rig.pose('seated');
      return bent;
    });
  }));
  check('the shared engine and atlas manifest each load once', portraitScriptReads.length === 2 && new Set(portraitScriptReads).size === 2);
  const secondDesk = await staticPage.evaluate(() => window.__twin.Store.emp('p02').desk);
  await staticPage.locator('#tabSpaces').click();
  await staticPage.locator(`#spacePanel button[data-res="${secondDesk}"]`).click();
  await staticPage.locator('#dFaceView').click();
  await staticPage.locator('#dFaceAngles [data-face-angle="profile"]').click();
  check('another fictional character can be inspected from the profile angle',
    await staticPage.locator('#dFaceAngles [data-face-angle="profile"]').getAttribute('aria-pressed') === 'true');
  await staticPage.locator('#dFaceAngles [data-face-angle="back"]').click();
  check('rear close-up positions the camera behind the same rendered shoulder mesh', await staticPage.evaluate(id => {
    const t = window.__twin, avatar = t.VIS[id].avatars[0];
    const p = avatar.group.worldToLocal(t.camera.position.clone());
    const target = avatar.group.worldToLocal(t.controls.target.clone());
    return !!avatar.portrait && p.z > .9 && Math.abs(p.x) < .001 &&
      Math.abs(target.y - 1.425 - avatar.portrait.userData.headOffsetY) < .001;
  }, secondDesk));
  await staticPage.keyboard.press('Escape');
  check('leaving close-up keeps the ordinary seated portrait', (await mountedSeatPortraits(staticPage)).includes('factory-portrait-p02'));
  await staticPage.locator('#encounterOpen').click();
  await staticPage.locator('#encounterStart').click();
  await staticPage.locator('#encounterPanel[data-stage="waiting"]').waitFor();
  check('both inter-BU participants use their matching 3D portraits',
    JSON.stringify(await encounterPortraits(staticPage)) === JSON.stringify(['factory-portrait-p01', 'factory-portrait-p10']));
  await staticPage.locator('#encounterInput').fill('<b>Réponse locale</b>');
  await staticPage.locator('#encounterSend').click();
  check('the demo accepts a simulated human reply as literal text',
    (await staticPage.locator('#encounterReply').textContent()) === '<b>Réponse locale</b>' &&
    await staticPage.locator('#encounterReply b').count() === 0);
  await staticPage.locator('#encounterCancel').click();
  check('cancel removes both transient portrait instances', (await encounterPortraits(staticPage)).every(id => id === null));
  await staticPage.locator('#encounterScenario').selectOption('agent-agent');
  await staticPage.locator('#encounterStart').click();
  check('synthetic agents retain the generic head', (await encounterPortraits(staticPage)).every(id => id === null));
  await staticPage.locator('#encounterPanel[data-stage="complete"]').waitFor();
  check('agents exchange a scripted answer without a human input',
    await staticPage.locator('#encounterReply').isVisible() && !(await staticPage.locator('#encounterForm').isVisible()));
  await staticPage.locator('#encounterScenario').selectOption('agent-human');
  await staticPage.locator('#encounterStart').click();
  await staticPage.locator('#encounterPanel[data-stage="waiting"]').waitFor();
  check('agent-to-human scenario renders only the assigned human portrait',
    JSON.stringify(await encounterPortraits(staticPage)) === JSON.stringify([null, 'factory-portrait-p01']));
  await staticPage.keyboard.press('Escape');
  check('agent-to-human demo can be closed from the waiting state', !(await staticPage.locator('#encounterPanel').isVisible()));
  await staticPage.locator('#tabPilot').click();
  await staticPage.locator('#pilotPanel [data-activity-open]').click();
  const demoActivity = staticPage.locator('.activity[data-activity-state="demo"]');
  await demoActivity.waitFor();
  await demoActivity.locator('[data-activity-filter="days"]').selectOption('30');
  check('static work history is labelled fictional and filters a deterministic local timeline',
    /Démo fictive|Fictional demo/.test(await demoActivity.textContent()) &&
    await demoActivity.locator('[data-track="duration"] .activity-tick').count() === 30 &&
    await demoActivity.locator('[data-activity-filter="subject"] option').count() === 3 &&
    await demoActivity.locator('[data-activity-filter="scope"]').count() === 0);
  await staticPage.keyboard.press('Escape');
  await staticPage.locator('#pilotPanel [data-portfolio-open]').click();
  const demoPortfolio = staticPage.locator('.portfolio[data-portfolio-state="demo"]');
  await demoPortfolio.waitFor();
  const group = demoPortfolio.locator(`[data-portfolio-parent="${portfolioId(21)}"]`);
  check('static portfolio relates objectives, projects and BU tasks with collapsed delegated work',
    await demoPortfolio.locator('.portfolio-card').count() === 4 && await group.locator('summary').isVisible() &&
    await group.getAttribute('open') === null && (await group.locator('summary').textContent()).includes('720'));
  await group.locator('summary').click();
  await planTask(demoPortfolio, 21).click();
  for (const mode of ['manual', 'automatic']) {
    await demoPortfolio.locator(`[data-portfolio-action="command:delegate:${portfolioId(21)}"]`).click();
    await demoPortfolio.locator('[data-portfolio-field="title"]').fill(`Demo subagent ${mode}`);
    await demoPortfolio.locator('[data-portfolio-field="mode"]').selectOption(mode);
    await demoPortfolio.locator('[data-portfolio-form] button[type="submit"]').click();
    await group.getByText(`Demo subagent ${mode}`, { exact: true }).waitFor();
  }
  check('demo supports both delegation modes, preserves the folded hierarchy and enforces four children',
    await group.locator('.portfolio-task').count() === 4 && await group.getAttribute('open') !== null &&
    await demoPortfolio.locator(`[data-portfolio-action="command:delegate:${portfolioId(21)}"]`).count() === 0);
  await planTask(demoPortfolio, 26).click();
  const demoCommand = action => demoPortfolio.locator(`[data-portfolio-action="command:${action}:${portfolioId(26)}"]`);
  await demoCommand('assign').click();
  await demoPortfolio.locator('[data-portfolio-field="assignee"]').selectOption('agent:platform-agent');
  await demoPortfolio.locator('[data-portfolio-form] button[type="submit"]').click();
  check('a human-review BU requires the manager to authorize an assigned agent before it can start',
    await demoCommand('start').count() === 0 && await demoCommand('authorize_start').isVisible());
  await demoCommand('authorize_start').click();
  await demoCommand('start').click();
  await demoCommand('report_usage').click();
  await demoPortfolio.locator('[data-portfolio-field="input"]').fill('0');
  await demoPortfolio.locator('[data-portfolio-field="output"]').fill('0');
  await demoPortfolio.locator('[data-portfolio-form] button[type="submit"]').click();
  check('an active agent can report a known zero token counter without closing its task',
    await demoCommand('report_usage').isVisible() && /(?:0 reported tokens|0 tokens déclarés)/.test(await demoPortfolio.locator('[data-portfolio-detail]').textContent()));
  await demoCommand('report_usage').click();
  await demoPortfolio.locator('[data-portfolio-field="input"]').fill('12');
  await demoPortfolio.locator('[data-portfolio-field="output"]').fill('3');
  await demoPortfolio.locator('[data-portfolio-form] button[type="submit"]').click();
  await demoCommand('submit').click();
  await demoPortfolio.locator('[data-portfolio-form] button[type="submit"]').click();
  check('a technical task reaches separate review and preserves its latest live cumulative tokens',
    await demoCommand('approve').isVisible() && /(?:15 reported tokens|15 tokens déclarés)/.test(await demoPortfolio.locator('[data-portfolio-detail]').textContent()));
  await demoCommand('approve').click();
  check('architect approval closes the preparation task without an external action',
    (await planTask(demoPortfolio, 26).locator('[data-status]').getAttribute('data-status')) === 'done');
  const demoField = key => demoPortfolio.locator(`[data-portfolio-field="${key}"]`);
  const demoConfirm = () => demoPortfolio.locator('[data-portfolio-form] button[type="submit"]').click();
  await demoPortfolio.locator('[data-portfolio-action="edit:goals"]').click();
  await demoField('title').fill('Demo central objective');
  await demoField('metric').fill('Score');
  await demoField('target').fill('99');
  await demoConfirm();
  await demoPortfolio.locator('.portfolio-card').filter({ hasText: 'Demo central objective' }).click();
  await demoPortfolio.locator('[data-portfolio-action="edit:projects"]').click();
  await demoField('title').fill('Demo linked project');
  await demoConfirm();
  const demoProject = demoPortfolio.locator('.portfolio-card').filter({ hasText: 'Demo linked project' });
  await demoProject.click();
  const demoProjectId = (await demoProject.getAttribute('data-portfolio-choice')).slice('project:'.length);
  await demoPortfolio.locator(`[data-portfolio-action="edit:projects:${demoProjectId}"]`).click();
  await demoField('state').selectOption('active');
  await demoConfirm();
  check('central planning creates a linked project and edits mutable fields only',
    /(?:Active|Actif)/.test(await demoProject.textContent()) && (await demoProject.textContent()).includes('Demo central objective'));
  await demoPortfolio.locator('[data-assist-open="explain_process"]').click();
  const processAssist = staticPage.locator('#assistHost');
  check('contextual help shows the four workflow steps offline', await processAssist.locator('.assist-steps li').count() === 4 &&
    await processAssist.getByText('Trame structurée hors ligne').isVisible());
  await staticPage.keyboard.press('Escape');
  await demoPortfolio.locator('[data-assist-open="decision_outline"]').click();
  const decisionAssist = staticPage.locator('#assistHost');
  await decisionAssist.getByRole('combobox', { name: 'BU', exact: true }).selectOption('delivery');
  await decisionAssist.getByLabel('Décision à préparer').fill('Prioriser une intégration');
  await decisionAssist.getByLabel('Contexte et éléments sourcés').fill('Comparatif déjà fourni par l’équipe, 2026.');
  const decisionSubmit = decisionAssist.getByRole('button', { name: 'Préparer le brief' });
  await decisionSubmit.click();
  check('decision brief requires an explicit source before each analysis', await decisionAssist.locator('.assist-result').isHidden());
  await decisionAssist.getByRole('radio', { name: 'Web public' }).check();
  await decisionSubmit.click();
  check('public mode prepares an offline research brief without claiming retrieval',
    await decisionAssist.getByText('Sources à recueillir', { exact: false }).isVisible() &&
    await decisionAssist.getByText('Cette interface ne consulte ni ne vérifie de sources en direct.', { exact: false }).isVisible() &&
    await decisionAssist.locator('input[name="source"]:checked').count() === 0);
  await decisionAssist.getByRole('radio', { name: 'Sources internes' }).check();
  await decisionSubmit.click();
  check('internal mode changes the collection plan and consumes the source selection',
    await decisionAssist.getByText('procédures autorisées', { exact: false }).isVisible() &&
    await decisionAssist.locator('input[name="source"]:checked').count() === 0);
  await staticPage.keyboard.press('Escape');
  await staticPage.keyboard.press('Escape');
  await staticPage.locator('#pilotPanel [data-ideas-open]').click();
  const demoIdeas = staticPage.locator('.ideas[data-ideas-state="ready"]');
  if (visualDir) await staticPage.locator('#console').screenshot({ path: path.join(visualDir, 'ai-factory-coffee-board.png'), animations: 'disabled' });
  await demoIdeas.getByRole('button', { name: '+ Ajouter une idée' }).click();
  await demoIdeas.locator('#idea-title').fill('Automatisation du relais 42');
  await demoIdeas.locator('#idea-text').fill('Rappel unique E2E. Un workflow pour transmettre les dossiers.');
  await demoIdeas.getByRole('button', { name: 'Déposer mon idée' }).click();
  await demoIdeas.getByText('Idée déposée · Automatisation').waitFor();
  await demoIdeas.locator('#ideas-search').fill('Rappel unique E2E');
  check('the local idea wall classifies a new note and filters it by search and category',
    await demoIdeas.locator('.idea-note-automation').count() === 1 &&
    await demoIdeas.getByRole('heading', { name: 'Automatisation du relais 42' }).isVisible());
  await demoIdeas.locator('[data-idea-filter="security"]').click();
  check('unmatched idea filters offer a clear empty state', await demoIdeas.getByText('Aucun post-it correspondant').isVisible());
  await demoIdeas.locator('#ideas-search').fill('');
  await demoIdeas.locator('[data-idea-filter="finance"]').click();
  check('demo illustrates a Finance topic with Operations provenance and never exposes trace',
    await demoIdeas.getByRole('heading', { name: 'Mesurer l’impact financier des incidents' }).isVisible() &&
    await demoIdeas.getByRole('button', { name: 'BU · Opérations' }).isVisible() &&
    await demoIdeas.locator('[data-idea-trace]').count() === 0);
  await staticPage.keyboard.press('Escape');
  await staticPage.locator('#pilotPanel [data-ideas-open]').click();
  check('the posted demo idea survives reopening the wall', await demoIdeas.getByRole('heading', { name: 'Automatisation du relais 42' }).isVisible());
  await staticPage.keyboard.press('Escape');
  await staticPage.clock.fastForward(30000);
  check('published static mode never requests snapshot, Observatory, Workbench, activity, portfolio, idea or assistance APIs',
    snapshotCalls.length === callsBeforeStatic && shadowReads === shadowCallsBeforeStatic && workbenchRequests.length === workbenchCallsBeforeStatic &&
    activityRequests.length === activityCallsBeforeStatic && portfolioRequests.length === portfolioCallsBeforeStatic && ideaRequests.length === ideaCallsBeforeStatic &&
    assistRequests.length === assistCallsBeforeStatic);
  await staticPage.evaluate(() => { document.documentElement.removeAttribute('data-static-demo'); });
  const nextLanguage = await staticPage.locator('#languageSelect').inputValue() === 'fr' ? 'en' : 'fr';
  await staticPage.locator('#languageSelect').selectOption(nextLanguage);
  await staticPage.waitForFunction(() => window.__twin?.source === 'api');
  check('switching the same document to API data clears demo portraits even for reused IDs',
    (await mountedSeatPortraits(staticPage)).length === 0 && await staticPage.evaluate(() =>
      Object.values(window.__twin.VIS).some(space => space.avatars.some(avatar => avatar.emp === 'p02' &&
        avatar.head.visible && avatar.body.getObjectByName('neck')?.visible && !avatar.releasePortraitNeck &&
        avatar.body.getObjectByName('neck').material === avatar.skinMat && avatar.body.getObjectByName('neck').scale.y === 1 &&
        '#' + avatar.skinMat.color.getHexString() === (window.__twin.Store.emp('p02').skin || '#e8c4a0').toLowerCase()))));
  await staticPage.locator('#tabFactory').click();
  await staticPage.locator('#factoryPanel [data-csec="skills"]').click();
  await staticPage.getByRole('link', { name: /Open the Flow Lab|Ouvrir le laboratoire de flux/ }).click();
  await staticPage.waitForURL(url => url.pathname.endsWith('/docs/flow-lab.html'));
  check('the R&D Lab opens the Flow Lab from the dashboard', /Laboratoire de flux IA|AI Flow Lab/i.test(await staticPage.title()));
  await staticPage.close();

  const mobile = await ctx.newPage();
  await mobile.setViewportSize({ width: 390, height: 844 });
  const atlasManifest = fs.readFileSync(path.join(root, 'dashboard/assets/portrait-atlases.js'), 'utf8');
  await mobile.route('**/assets/portrait-atlases.js', route => route.fulfill({ contentType: 'application/javascript',
    body: `${atlasManifest}\nwindow.FactoryPortraitAtlases = {...window.FactoryPortraitAtlases,p02:{...window.FactoryPortraitAtlases.p02,src:'data:image/webp;base64,AAAA'}};` }));
  await mobile.goto('http://factory.test/?static&lang=fr');
  await mobile.waitForFunction(() => window.__appReady === true, null, { timeout: 30000 });
  await seatDemoRoster(mobile);
  await mobile.waitForFunction(() => Object.values(window.__twin.VIS).flatMap(space => space.avatars)
    .filter(avatar => avatar.portrait).length === 11, null, { timeout: 15000 });
  check('one corrupt atlas falls back on mobile while the other eleven remain available', await mobile.evaluate(() =>
    Object.values(window.__twin.VIS).some(space => space.avatars.some(avatar => avatar.emp === 'p02' && avatar.head.visible && !avatar.portrait))));
  await mobile.locator('#encounterOpen').click();
  check('mobile exchange controls are visible without horizontal overflow', await mobile.locator('#encounterScenario').isVisible() &&
    await mobile.locator('#encounterPanel').evaluate(el => el.scrollWidth <= el.clientWidth));
  await mobile.keyboard.press('Escape');
  await mobile.locator('#tabFactory').click();
  await mobile.locator('#factoryPanel [data-csec="shadow"]').click();
  const demoVisible = await mobile.locator('[data-shadow-state="demo"]').isVisible();
  const mobileLayout = await mobile.locator('#cBody').evaluate(el => ({
    consoleHeight: el.clientHeight,
    consoleScrollHeight: el.scrollHeight,
    documentWidth: document.documentElement.clientWidth,
    documentScrollWidth: document.documentElement.scrollWidth,
    viewportWidth: window.innerWidth,
  }));
  check('mobile Factory route shows the demo with a scrollable console and no page overflow',
    demoVisible && mobileLayout.consoleScrollHeight > mobileLayout.consoleHeight && mobileLayout.documentScrollWidth <= mobileLayout.documentWidth,
    JSON.stringify({ demoVisible, ...mobileLayout }));
  const mobileUsage = mobile.getByRole('table', { name: 'Signaux par équipe et outil' });
  check('mobile usage rows keep their labels and fit without horizontal scrolling', await mobileUsage.evaluate(el => {
    const row = el.querySelector('tbody tr'), label = row.querySelector('.shadow-cell-label');
    return getComputedStyle(row).display === 'grid' && getComputedStyle(label).display === 'block' && el.parentElement.scrollWidth <= el.parentElement.clientWidth;
  }));
  if (visualDir) {
    await mobile.getByRole('heading', { name: 'Signaux par équipe et outil' }).evaluate(el => el.scrollIntoView({ block: 'start' }));
    await mobile.screenshot({ path: path.join(visualDir, 'ai-factory-observatoire-mobile.png'), animations: 'disabled' });
  }
  await mobile.keyboard.press('Escape');
  await mobile.locator('#tabPilot').click();
  await mobile.locator('#pilotPanel [data-activity-open]').click();
  const mobileActivity = mobile.locator('.activity[data-activity-state="demo"]');
  check('mobile history fits the console and gives the chart a daily table alternative', await mobileActivity.isVisible() &&
    await mobileActivity.evaluate(el => el.scrollWidth <= el.clientWidth && el.parentElement.scrollWidth <= el.parentElement.clientWidth) &&
    await mobileActivity.locator('.activity-daily summary').isVisible());
  await mobile.keyboard.press('Escape');
  await mobile.locator('#pilotPanel [data-portfolio-open]').click();
  const mobilePortfolio = mobile.locator('.portfolio[data-portfolio-state="demo"]');
  check('mobile portfolio keeps its task board and delegation controls within the viewport', await mobilePortfolio.isVisible() &&
    await mobilePortfolio.locator('.portfolio-board').isVisible() &&
    await mobilePortfolio.evaluate(el => el.scrollWidth <= el.clientWidth && el.parentElement.scrollWidth <= el.parentElement.clientWidth));
  await mobile.keyboard.press('Escape');
  await mobile.locator('#pilotPanel [data-ideas-open]').click();
  const mobileIdeas = mobile.locator('.ideas[data-ideas-state="ready"]');
  check('mobile idea wall retains browser-local notes and has no horizontal overflow', await mobileIdeas.isVisible() &&
    await mobileIdeas.getByRole('heading', { name: 'Automatisation du relais 42' }).isVisible() &&
    await mobileIdeas.evaluate(el => el.scrollWidth <= el.clientWidth && el.parentElement.scrollWidth <= el.parentElement.clientWidth));
  if (visualDir) await mobile.screenshot({ path: path.join(visualDir, 'ai-factory-coffee-board-mobile.png'), animations: 'disabled' });
  await mobile.close();

  if (vendor) {
    const threeVersion = JSON.parse(fs.readFileSync(path.join(vendor, 'package.json'), 'utf8')).version;
    const pagesRoot = 'http://factory.test/project-demo/';
    const staticHtml = html.replace('<html ', '<html data-static-demo="1" ')
      .replaceAll(`https://cdn.jsdelivr.net/npm/three@${threeVersion}/`, './vendor/three/');
    for (const mode of ['import-failure', 'webgl-failure', 'same-origin']) {
      const isMobile = mode === 'import-failure';
      const demoContext = await browser.newContext({
        viewport: isMobile ? { width: 390, height: 844 } : { width: 1440, height: 900 },
        isMobile, hasTouch: isMobile, locale: 'fr-FR', timezoneId: 'Europe/Paris', reducedMotion: 'reduce',
      });
      const demo = await demoContext.newPage();
      const requests = [], failures = [];
      demo.on('request', request => requests.push(request.url()));
      demo.on('pageerror', error => failures.push(error.message));
      await demo.clock.setFixedTime(new Date('2026-11-08T23:00:00+01:00'));
      if (mode === 'webgl-failure') {
        await demo.addInitScript(() => {
          const getContext = HTMLCanvasElement.prototype.getContext;
          HTMLCanvasElement.prototype.getContext = function (kind, ...args) {
            return kind.includes('webgl') ? null : getContext.call(this, kind, ...args);
          };
        });
      }
      let releaseModule;
      const holdModule = new Promise(resolve => { releaseModule = resolve; });
      await demoContext.route(pagesRoot + '**', async route => {
        const rel = new URL(route.request().url()).pathname.slice('/project-demo/'.length);
        if (!rel || rel === 'index.html') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: staticHtml });
        if (rel === 'companion/index.html') return route.fulfill({ contentType: 'text/html; charset=utf-8',
          body: fs.readFileSync(path.join(root, 'dashboard', rel), 'utf8').replace('<html ', '<html data-static-demo="1" ') });
        if (mode === 'import-failure' && rel === 'vendor/three/build/three.module.js') {
          await holdModule;
          return route.abort();
        }
        const file = rel.startsWith('vendor/three/') ? path.join(vendor, rel.slice('vendor/three/'.length)) : path.join(root, 'dashboard', rel);
        return fs.existsSync(file) ? route.fulfill({ path: file }) : route.fulfill({ status: 404 });
      });
      try {
        await demo.goto(pagesRoot + (isMobile ? '?lang=en' : '?lang=fr'), { waitUntil: 'commit' });
        const entry = demo.locator('#demoEntry');
        const projects = demo.locator('#demoProjects');
        const roadmapLink = demo.locator('#demoRoadmap');
        await projects.waitFor();
        const sample = await demo.evaluate(() => ({
          day: window.__twin.S.date, time: window.__twin.S.t, live: window.__twin.S.live,
          busy: Object.values(window.__twin.snap.res).some(resource => resource.state === 'busy'),
          overflow: document.documentElement.scrollWidth > innerWidth,
        }));
        const targetsInView = await entry.locator('.btn').evaluateAll(buttons => buttons.length === 3 && buttons.every(button => {
          const rect = button.getBoundingClientRect();
          return rect.height >= 44 && rect.top >= 0 && rect.bottom <= innerHeight;
        }));
        check(`${mode}: public landing has a populated Monday at 10:00 and three visible 44px shortcuts`,
          sample.day === '2026-11-09' && sample.time === 600 && !sample.live && sample.busy && !sample.overflow && targetsInView,
          JSON.stringify(sample));
        const phaseLabels = isMobile ? ['Identity & testing', 'Skills & ERP', 'Cost & browser agents']
          : ['Identités & tests', 'Compétences & ERP', 'Coûts & navigateur'];
        const roadmapInView = () => roadmapLink.evaluate((link, labels) => {
          const rect = link.getBoundingClientRect();
          const phases = [...link.querySelectorAll('.demo-roadmap-phases span span')];
          return rect.height >= 44 && rect.top >= 0 && rect.bottom <= innerHeight &&
            document.documentElement.scrollWidth <= innerWidth && phases.length === labels.length &&
            phases.every((phase, index) => phase.textContent === labels[index]);
        }, phaseLabels);
        check(`${mode}: the public homepage shows three roadmap horizons and a 44px link`,
          await roadmapInView());
        if (isMobile) {
          await demo.setViewportSize({ width: 320, height: 740 });
          const compactFit = await roadmapLink.evaluate(link => {
            const rect = link.getBoundingClientRect();
            return innerWidth === 320 && document.documentElement.scrollWidth <= innerWidth &&
              rect.left >= 0 && rect.right <= innerWidth && rect.height >= 44;
          });
          await roadmapLink.scrollIntoViewIfNeeded();
          check('the 320px mobile homepage roadmap fits horizontally and scrolls into view', compactFit && await roadmapInView());
          await demo.setViewportSize({ width: 390, height: 844 });
          await demo.evaluate(() => window.scrollTo(0, 0));
          await projects.tap();
          await demo.locator('.portfolio[data-portfolio-state="demo"]').waitFor();
          check('mobile project shortcut opens the portfolio while the Three.js request is held',
            await demo.locator('#loading').getAttribute('data-scene') === 'loading',
            `${await demo.evaluate(() => Math.round(performance.now()))} ms from navigation to the portfolio on the test machine`);
          releaseModule();
        } else {
          await projects.click();
          await demo.locator('.portfolio[data-portfolio-state="demo"]').waitFor();
        }
        await demo.keyboard.press('Tab');
        const consoleCloseFocused = await demo.locator('#cClose').evaluate(el => el === document.activeElement);
        await demo.keyboard.press('Shift+Tab');
        const consoleWrap = await demo.locator('#console').evaluate(el => el.contains(document.activeElement));
        await demo.keyboard.press('?');
        await demo.locator('#help').waitFor();
        await demo.keyboard.press('Tab');
        const helpTrap = await demo.locator('#helpClose').evaluate(el => el === document.activeElement);
        await demo.keyboard.press('Escape');
        const restoredPanel = await demo.locator('#console').evaluate(el => el.contains(document.activeElement));
        await demo.keyboard.press('Escape');
        check(`${mode}: focus wraps in the console and Help and restores to their launchers in order`,
          consoleCloseFocused && consoleWrap && helpTrap && restoredPanel && await projects.evaluate(el => el === document.activeElement));

        if (mode === 'same-origin') {
          await sceneReady(demo);
          check('Pages project subpath loads Three.js from the same origin', requests.some(url => url.endsWith('/project-demo/vendor/three/build/three.module.js')) &&
            await demo.locator('#canvasHost canvas').isVisible() && requests.every(url => url.startsWith(pagesRoot)));
        } else {
          await demo.locator('#loading[data-scene="unavailable"]').waitFor();
          await demo.locator('#fallbackSpaces').click();
          await demo.locator('#spacePanel button[data-res="X1"]').click();
          await demo.keyboard.press('Tab');
          const drawerStarts = await demo.locator('#dClose').evaluate(el => el === document.activeElement);
          await demo.keyboard.press('Shift+Tab');
          const drawerWrap = await demo.locator('#drawer').evaluate(el => el.contains(document.activeElement));
          await demo.keyboard.press('Escape');
          const drawerReturns = await demo.locator('#spacePanel button[data-res="X1"]').evaluate(el => el === document.activeElement);
          await demo.locator('#tabPilot').click();
          const planning = await demo.locator('#pilotPanel [data-portfolio-open]').isVisible();
          await demo.locator('#tabFactory').click();
          await demo.locator('#factoryPanel [data-csec="registry"]').click();
          const registry = await demo.locator('#cBody .ctable tbody tr').count();
          await demo.keyboard.press('Escape');
          await demo.locator('#tabTeam').click();
          check(`${mode}: Spaces, drawer focus, Planning, Factory and Team survive the 3D failure`,
            drawerStarts && drawerWrap && drawerReturns && planning && registry >= 6 && await demo.locator('#teamPanel .prow').count() > 0 &&
            await demo.locator('#canvasHost canvas').count() === 0);
        }
        await demo.locator('#demoDecision').click();
        await demo.waitForURL(url => url.pathname.endsWith('/companion/index.html'));
        await demo.locator('#queue-count').waitFor();
        const targetLocale = isMobile ? 'en' : 'fr';
        check(`${mode}: Decision Companion opens in ${targetLocale}`, await demo.locator('html').getAttribute('lang') === targetLocale);
        await demo.goBack();
        if (isMobile) {
          await projects.tap();
          const portfolio = demo.locator('.portfolio[data-portfolio-state="demo"]');
          const handoff = portfolio.locator(`[data-portfolio-parent="${portfolioId(23)}"]`);
          await handoff.locator('summary').tap();
          const child = handoff.getByRole('button', { name: /Done.*Verify the Aster CRM event/ });
          await child.tap();
          const detail = portfolio.locator('[data-portfolio-detail]');
          const decisionLink = detail.getByRole('link', { name: 'Open the simulated decision' });
          const link = './companion/index.html?lang=en&demo_case=DEMO-104&demo_revision=1';
          check('the fictional Portfolio shows completed delegated CRM evidence linked to the exact decision revision',
            await portfolio.getByText('Fictional demo', { exact: true }).isVisible() &&
            await detail.getByText('CRM event · opportunity won · OPP-104', { exact: true }).isVisible() &&
            await detail.getByText(/DEMO-104 · Revision 1/).isVisible() && await decisionLink.getAttribute('href') === link);
          await decisionLink.tap();
          await demo.waitForURL(new URL(link, pagesRoot).href);
          await demo.getByRole('heading', { name: 'Aster opportunity · prepare an order' }).waitFor();
          check('the Pages mobile handoff opens DEMO-104 revision 1 with the same evidence in local simulation',
            await demo.locator('#connection').getAttribute('data-mode') === 'static' &&
            await demo.locator('#queue-view').getAttribute('data-screen') === 'detail' &&
            await demo.locator('#case-label').textContent() === 'DEMO-104 · Business action' &&
            (await demo.locator('#case-revision').textContent()).startsWith('Revision 1 ·') &&
            await demo.locator('#action-fields').getByText('OPP-104', { exact: true }).isVisible() &&
            await demo.locator('#evidence-items').getByText('CRM event · opportunity won · OPP-104', { exact: true }).isVisible() &&
            requests.filter(url => url === `${pagesRoot}assets/factory-demo-decision.js`).length >= 2);
          const rationale = 'The fictional CRM event matches the draft order.';
          await demo.getByLabel('Reason for the decision').fill(rationale);
          await demo.getByRole('button', { name: 'Approve', exact: true }).tap();
          await demo.locator('#receipt-id').getByText('ERP-DRAFT-104', { exact: true }).waitFor();
          await demo.getByRole('button', { name: 'Show history' }).tap();
          check('the guided local decision shows its simulated ERP receipt and exact revision in decision history',
            await demo.locator('#history-items').getByText(rationale, { exact: true }).isVisible() &&
            await demo.locator('#history-items').getByText(/Approved · Revision 1|Approve · Revision 1/).isVisible());
          await demo.goBack();
        }
        await demo.locator('#demoFlows').click();
        await demo.waitForURL(url => url.pathname.endsWith('/docs/flow-lab.html'));
        await demo.locator('#outcome').waitFor();
        check(`${mode}: Flow Lab opens in ${targetLocale} and renders the example`, await demo.locator('html').getAttribute('lang') === targetLocale &&
          await demo.locator('#outcome').getAttribute('data-outcome') === 'delivered');
        await demo.goBack();
        await roadmapLink.waitFor();
        if (isMobile) await demo.setViewportSize({ width: 320, height: 740 });
        await roadmapLink.focus();
        await demo.keyboard.press('Enter');
        await demo.waitForURL(`${pagesRoot}docs/roadmap.html?lang=${targetLocale}`);
        const roadmapHeading = targetLocale === 'en' ? 'Roadmap' : 'Feuille de route';
        await demo.getByRole('heading', { name: roadmapHeading, exact: true }).waitFor();
        check(`${mode}: the keyboard opens the six-workstream roadmap in ${targetLocale} at the Pages subpath`,
          await demo.locator('html').getAttribute('lang') === targetLocale && await demo.locator('.workstreams article').count() === 6 &&
          await demo.locator('#roadmap-home').getAttribute('href') === `../index.html?lang=${targetLocale}` &&
          await demo.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        check(`${mode}: public shortcuts need no API and raise no JavaScript error`,
          !requests.some(url => new URL(url).pathname.includes('/api/')) && failures.length === 0, failures.join(' | '));
      } finally {
        releaseModule();
        await demoContext.close();
      }
    }
  }
}

const roadmap = await ctx.newPage();
await roadmap.setViewportSize({ width: 320, height: 740 });
await roadmap.goto(new URL('docs/roadmap.html?lang=fr#crm-erp', base).href);
await roadmap.getByRole('heading', { name: 'Transactions CRM–ERP supervisées' }).waitFor();
const roadmapNoOverflow = await roadmap.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
const englishRoadmap = roadmap.getByRole('link', { name: 'English' });
const languageTarget = new URL(await englishRoadmap.getAttribute('href'), roadmap.url());
await englishRoadmap.click();
await roadmap.getByRole('heading', { name: 'Supervised CRM–ERP transactions' }).waitFor();
const sourceLinks = await roadmap.locator('.signal a').evaluateAll(links => links.length > 0 && links.every(link => {
  const source = document.getElementById(link.hash.slice(1));
  return source?.querySelector('a')?.href.startsWith('https://');
}));
check('roadmap keeps a workstream deep link across languages and fits a 320px viewport',
  roadmapNoOverflow && await roadmap.evaluate(() => document.documentElement.scrollWidth <= innerWidth) &&
  languageTarget.searchParams.get('lang') === 'en' && languageTarget.hash === '#crm-erp' && roadmap.url() === languageTarget.href && sourceLinks &&
  await roadmap.locator('#roadmap-guide').getAttribute('href') === './index.en.html');
await roadmap.close();

const staticRoadmapContext = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 320, height: 740 } });
const staticRoadmap = await staticRoadmapContext.newPage();
await staticRoadmap.goto(new URL('docs/roadmap.html#agent-identity', base).href);
check('roadmap anchors and both translations remain readable with JavaScript disabled',
  await staticRoadmap.locator('#agent-identity-title [lang="fr"]').isVisible() &&
  await staticRoadmap.locator('#agent-identity-title [lang="en"]').isVisible() &&
  await staticRoadmap.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
await staticRoadmapContext.close();

const guide = await ctx.newPage();
await guide.setViewportSize({ width: 390, height: 844 });
await guide.goto(new URL('docs/index.html#sous-agents', base).href);
const guideNav = guide.getByRole('navigation', { name: 'Sommaire' });
const anchorsMatch = await guideNav.getByRole('link').evaluateAll(links => links.length > 0 && links.every(link =>
  document.getElementById(link.hash.slice(1))));
const engineCurrent = await guideNav.getByRole('link', { name: 'Moteur des sous-agents' }).getAttribute('aria-current');
await guide.getByRole('searchbox', { name: 'Rechercher dans le guide' }).fill('execution se bloque');
await guide.locator('#guide-results').getByRole('link', { name: 'Supervision et incidents' }).click();
check('operations guide deep links and accent-insensitive search open the relevant runbook',
  anchorsMatch && engineCurrent === 'location' && new URL(guide.url()).hash === '#supervision' &&
  await guide.locator('#supervision details[open]').getByText('Une exécution se bloque ou expire').isVisible());

const guideSectionIds = await guideNav.getByRole('link').evaluateAll(links => links.map(link => link.hash));
await guide.getByRole('link', { name: 'Read in English' }).click();
await guide.waitForURL('**/index.en.html#supervision');
const englishNav = guide.getByRole('navigation', { name: 'Contents' });
const englishIds = await englishNav.getByRole('link').evaluateAll(links => links.map(link => link.hash));
const englishSearch = guide.getByRole('searchbox', { name: 'Search the guide' });
await englishSearch.fill('unmatched-search-term');
const localizedEmpty = await guide.getByRole('status').textContent();
await guide.getByRole('button', { name: 'Clear' }).click();
const resetFocused = await englishSearch.evaluate(input => input === document.activeElement && !input.value);
await englishSearch.fill('execution stalls');
await englishSearch.press('Enter');
const result = guide.locator('#guide-results').getByRole('link', { name: 'Monitoring and incidents' });
const keyboardResult = await result.evaluate(link => link === document.activeElement);
await guide.keyboard.press('Enter');
check('English operations guide preserves French anchors and supports keyboard search',
  (await guide.locator('html').getAttribute('lang')) === 'en' &&
  JSON.stringify(guideSectionIds) === JSON.stringify(englishIds) &&
  localizedEmpty === 'No sections found. Try another term.' && resetFocused && keyboardResult &&
  await guide.locator('#supervision details[open]').getByText('An execution stalls or times out').isVisible() &&
  await guide.evaluate(() => document.documentElement.scrollWidth === document.documentElement.clientWidth));

await guide.goto(new URL('docs/architecture.en.html#zone-observatoire', base).href);
const englishRoomOpen = await guide.locator('#zone-observatoire').evaluate(room => room.open);
await guide.getByRole('link', { name: 'Lire en français' }).click();
await guide.waitForURL('**/architecture.html#zone-observatoire');
check('architecture atlas switches language while keeping the room open on mobile',
  englishRoomOpen &&
  await guide.locator('#zone-observatoire details, #zone-observatoire').evaluate(room => room.open) &&
  await guide.evaluate(() => document.documentElement.scrollWidth === document.documentElement.clientWidth));
await guide.close();

const staticGuideContext = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 390, height: 844 } });
try {
  const staticGuide = await staticGuideContext.newPage();
  await staticGuide.goto(new URL('docs/index.en.html', base).href);
  await staticGuide.getByRole('navigation', { name: 'Contents' }).getByRole('link', { name: 'Kubernetes pilot' }).click();
  check('operations guide keeps native navigation and content without JavaScript',
    new URL(staticGuide.url()).hash === '#kubernetes' &&
    await staticGuide.getByRole('heading', { name: 'Kubernetes pilot' }).isVisible());
} finally {
  await staticGuideContext.close();
}

await browser.close();
const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
