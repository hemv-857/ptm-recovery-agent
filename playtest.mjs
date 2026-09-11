import { chromium } from 'playwright';

const BASE = 'http://127.0.0.1:8001';
const defects = [];
const seen = new Set();

async function note(label, predicate) {
  if (!predicate()) return;
  const key = label.replace(/\s+/g, '_');
  if (seen.has(key)) return;
  seen.add(key);
  defects.push({ label, when: new Date().toISOString() });
  console.log('DEFECT ::', label);
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const consoleErrors = [];
page.on('console', msg => {
  if (msg.type() === 'error') consoleErrors.push(msg.text());
});
page.on('pageerror', err => consoleErrors.push('PAGEERROR: ' + err.message));

// 1. Load the shell
const res = await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 15000 });
note('HTTP status not 200', () => res.status() !== 200);

// 2. Wait for React to mount
await page.waitForTimeout(3500);

// 3. Root mounted?
const rootHtml = await page.$eval('#root', el => el.innerHTML.length);
note('Root empty after 3.5s (React not mounted)', () => rootHtml === 0);

// 4. Console errors on load
const initErrors = consoleErrors.filter(e => !e.includes('Babel') && !e.includes('babel'));
note('Console errors on load: ' + initErrors.slice(0,3).join(' | '), () => initErrors.length > 0);

// 5. Welcome prompt or skeleton visible?
const welcomeText = await page.$eval('body', el => el.innerText);
note('No visible loading prompt (empty first-run state)', () =>
  !welcomeText.includes('Spinning up') && !welcomeText.includes('Loading') && rootHtml > 0 && welcomeText.trim().length < 20
);

// 6. Cmd+K palette
await page.keyboard.press('Meta+k');
await page.waitForTimeout(400);
const paletteVisible = await page.$eval('body', el => el.innerHTML.includes('palette') || el.innerHTML.includes('Type a command'));
note('Cmd+K palette did not open', () => !paletteVisible);

// 7. Rapid Run Batch clicks
const runBtn = page.locator('button:has-text("Run Batch")');
const runCountBefore = await runBtn.count();
if (runCountBefore > 0) {
  for (let i = 0; i < 8; i++) {
    await runBtn.click();
    await page.waitForTimeout(80);
  }
  await page.waitForTimeout(1200);
  const disabledStates = await runBtn.evaluateAll(buttons => buttons.map(b => b.disabled));
  note('Run Batch not disabled during run (rapid clicks: ' + disabledStates.join(','), () =>
    disabledStates.some(d => d === true) === false && disabledStates.length > 0
  );
}

// 8. Palette empty state
await page.keyboard.press('Escape');
await page.waitForTimeout(200);
await page.keyboard.press('Meta+k');
await page.waitForTimeout(300);
await page.fill('input.palette-input', 'xyznonexistent1234');
await page.waitForTimeout(250);
const noMatches = await page.$eval('.palette-list', el => el.innerText.includes('No matches'));
note('Palette empty state missing for unknown query', () => !noMatches);

// 9. Real palette command
await page.fill('input.palette-input', 'Run Batch');
await page.waitForTimeout(200);
await page.keyboard.press('ArrowDown');
await page.waitForTimeout(100);
await page.keyboard.press('Enter');
await page.waitForTimeout(600);
const runningBadge = await page.$eval('body', el => el.innerHTML.includes('LIVE') || el.innerText.includes('Running'));
note('Run Batch command from palette produced no visible run state', () => !runningBadge);

// 10. Case Ledger tab
await page.locator('.nav-item:has-text("Case Ledger")').click();
await page.waitForTimeout(400);
const ledgerContent = await page.$eval('body', el => el.innerHTML.includes('Case Ledger') || el.innerHTML.includes('case_') || el.innerText.includes('Case'));
note('Case Ledger tab did not render any content', () => !ledgerContent);

// 11. Return to hub
await page.locator('.nav-item:has-text("Dashboard")').click();
await page.waitForTimeout(300);
const heroVisible = await page.$eval('body', el => el.innerHTML.includes('hero') || el.innerText.includes('incremental'));
note('Return to Hub lost content', () => !heroVisible);

// 12. Notification panel
const notifBtn = page.locator('.notif-btn');
await notifBtn.click();
await page.waitForTimeout(250);
const notifPanel = await page.$eval('body', el => el.innerHTML.includes('notif-panel') || el.innerHTML.includes('Notifications'));
note('Notification panel did not open', () => !notifPanel);

// 13. Theme toggle
const themeBtn = page.locator('button[aria-label*="Theme"]');
await themeBtn.click();
await page.waitForTimeout(350);
const dataTheme = await page.$eval('html', el => el.getAttribute('data-theme'));
note('Theme toggle did not change data-theme', () => dataTheme !== 'dark' && dataTheme !== 'light');

// 14. Tenant selector
const tenantSel = page.locator('.tenant-select');
await tenantSel.selectOption('demo');
await page.waitForTimeout(400);
const tenantVal = await tenantSel.inputValue();
note('Tenant select did not change value', () => tenantVal !== 'demo');

// 15. Reload mid-load
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForTimeout(3000);
const rootHtml2 = await page.$eval('#root', el => el.innerHTML.length);
note('Reload left root empty (app failed to remount)', () => rootHtml2 === 0);

// 16. Audit modal
await page.locator('.nav-item:has-text("Case Ledger")').click();
await page.waitForTimeout(500);
const caseRows = await page.$$('.case-row, table tbody tr, [class*="case"]');
if (caseRows.length > 0) {
  await caseRows[0].click();
  await page.waitForTimeout(450);
  const modalOpen = await page.$eval('body', el => el.innerHTML.includes('audit') || el.innerHTML.includes('Audit') || el.innerHTML.includes('modal'));
  note('Case click did not open audit modal', () => !modalOpen);
}

console.log('\n=== PLAYTEST SUMMARY ===');
console.log(' Defects found:', defects.length);
defects.forEach(d => console.log('  -', d.label));
console.log(' Console errors:', consoleErrors.length);
consoleErrors.slice(0,5).forEach(e => console.log('   err:', e.slice(0,160)));

await browser.close();
process.exit(defects.length > 0 ? 1 : 0);
