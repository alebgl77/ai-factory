// End-to-end checks of the 3D dashboard (MIT). Usage:
//   node tests/e2e/run.mjs                                  -> file:// mode with the embedded demo snapshot
//   E2E_BASE_URL=http://localhost:8080 node tests/e2e/run.mjs -> deployed stack (API mode)
// THREE_VENDOR_DIR=/path/to/three/package serves Three.js locally instead of the CDN (offline CI).
import { createRequire } from 'node:module';
// ESM imports ignore NODE_PATH: fall back to require() so a global Playwright install also works
let chromium;
try { ({ chromium } = await import('playwright')); } catch { ({ chromium } = createRequire(import.meta.url)('playwright')); }
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const base = process.env.E2E_BASE_URL || 'file://' + path.join(root, 'dashboard/index.html');
const vendor = process.env.THREE_VENDOR_DIR;
const results = [];
const check = (name, ok, info = '') => {
  results.push({ name, ok: !!ok, info });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${info ? '  — ' + info : ''}`);
};

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
await page.waitForTimeout(1200);

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
check('Usine tab lists 9 console sections', (await page.$$('#factoryPanel [data-csec]')).length === 9);
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
  await page.click('#view3d'); await page.waitForTimeout(1300);  // overview, so the drop target is on screen
  const bb = await (await page.$('#pegBtn')).boundingBox();
  const drop = await page.evaluate(() => {
    const T = window.__twin, V = T.camera.position.constructor, host = document.getElementById('canvasHost').getBoundingClientRect();
    const p = new V(-4.0, 0, 4.6).project(T.camera);
    return { x: host.left + (p.x + 1) / 2 * host.width, y: host.top + (1 - p.y) / 2 * host.height };
  });
  await page.mouse.move(bb.x + bb.width / 2, bb.y + bb.height / 2); await page.mouse.down();
  for (let i = 1; i <= 10; i++) await page.mouse.move(bb.x + (drop.x - bb.x) * i / 10, bb.y + (drop.y - bb.y) * i / 10);
  await page.mouse.up();
  await page.waitForFunction(() => window.__twin.pov.active && !window.__twin.pov.animating, null, { timeout: 8000 }).catch(() => {});
  let s = await pov();
  check('visit mode: dropping the character on the floor enters first-person view there', s.active && Math.hypot(s.x + 4.0, s.z - 4.6) < 0.8, `${s.zone} (${s.x.toFixed(1)}, ${s.z.toFixed(1)})`);
  await page.evaluate(() => window.__twin.pov.enter(-4.0, 4.6, -Math.PI / 2));
  await page.waitForFunction(() => !window.__twin.pov.animating, null, { timeout: 8000 });
  const s0 = await pov();
  await page.keyboard.down('KeyW'); await page.waitForTimeout(1200); await page.keyboard.up('KeyW');
  s = await pov();
  check('visit mode: W walks forward', Math.hypot(s.x - s0.x, s.z - s0.z) > 0.5, `${Math.hypot(s.x - s0.x, s.z - s0.z).toFixed(2)} m`);
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
}

for (const lens of ['uc', 'heat', 'status']) { await page.click(`[data-lens="${lens}"]`); await page.waitForTimeout(150); }
check('lenses switch without errors', errors.length === 0, errors.slice(0, 2).join(' | '));
const leak = await page.evaluate(() => /glsa_|Bearer\s+[A-Za-z0-9]/.test(document.documentElement.outerHTML));
check('no Grafana token or bearer in the page', !leak);

await browser.close();
const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
