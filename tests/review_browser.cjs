/* Run through run_browser.py: temporary data, real HTTP, no external AI. */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const base = process.env.TETHER_TEST_URL;
if (!base) throw new Error('Use tests/run_browser.py to isolate this test');

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.EDGE_EXECUTABLE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' });
  let page;
  try {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: 'block', reducedMotion: 'reduce', colorScheme: 'light', acceptDownloads: true });
    const registration = await context.request.post(base + '/api/auth/register', { data: { username: 'review_' + Date.now(), password: 'Review.test.2026!' } });
    assert(registration.ok(), await registration.text());
    page = await context.newPage(); page.setDefaultTimeout(10000);
    page.on('dialog', dialog => dialog.accept());
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    const act = name => page.locator(`[data-act="${name}"]`);
    const closed = () => page.locator('#overlay').waitFor({ state: 'hidden' });
    await fs.mkdir('.cache/ui-preview', { recursive: true });
    await page.goto(base); await page.locator('#expression-home-text').waitFor();

    // System theme changes preserve the editing control and selection.
    await page.locator('#expression-home-text').fill('主题切换时保留这一段表达');
    await page.locator('#expression-home-text').evaluate(el => { el.focus(); el.setSelectionRange(2, 5); });
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
    assert.deepEqual(await page.locator('#expression-home-text').evaluate(el => [document.activeElement === el, el.selectionStart, el.selectionEnd]), [true, 2, 5]);
    assert.equal(await page.locator('#expression-home-text').inputValue(), '主题切换时保留这一段表达');
    // A refresh can start before the user begins typing.
    await page.locator('#expression-home-text').blur();
    let releaseHome, homeEntered;
    const homeReady = new Promise(resolve => { homeEntered = resolve; });
    const homeGate = new Promise(resolve => { releaseHome = resolve; });
    await page.route('**/api/home?*', async route => { homeEntered(); await homeGate; await route.continue(); });
    await page.evaluate(() => window.dispatchEvent(new Event('online'))); await homeReady;
    await page.locator('#expression-home-text').focus();
    await page.locator('#expression-home-text').evaluate(el => { window.reviewInput = el; });
    const loaded = page.waitForResponse(res => res.url().includes('/api/home?'));
    releaseHome(); await loaded; await page.waitForTimeout(100);
    assert(await page.locator('#expression-home-text').evaluate(el => window.reviewInput === el && document.activeElement === el));
    await page.unroute('**/api/home?*');
    console.log('PASS Theme changes retain focus and selection; in-flight refresh preserves active input');

    await act('wheel').click();
    const start = Date.now(); await act('wheel-spin').click(); await page.locator('.wheel-pick').waitFor();
    assert(Date.now() - start < 1500, 'reduced motion still waits for animation');
    await page.keyboard.press('Escape'); await closed();
    console.log('PASS Reduced-motion wheel returns a result without the 4.2 second wait');

    await page.locator('[data-act="pick"][data-emotion="despair"]').click();
    await act('guide-start').click(); await act('iv-next').waitFor();
    const guideHeading = await page.locator('#overlay h2').textContent();
    await page.locator('#overlay').evaluate(el => { window.reviewGuide = el.firstElementChild; });
    await page.locator('#overlay [data-act="open-crisis"]').first().click();
    await page.locator('.crisis-card').waitFor();
    await page.screenshot({ path: '.cache/ui-preview/review-crisis-dark.png' });
    await page.locator('#overlay [data-act="close"]').first().click();
    await act('iv-next').waitFor();
    assert(await page.locator('#overlay').evaluate(el => el.firstElementChild === window.reviewGuide));
    assert.equal(await page.locator('#overlay h2').textContent(), guideHeading);
    await page.waitForTimeout(80);
    await page.locator('#overlay [data-act="open-crisis"]').first().click();
    await page.locator('.crisis-card').waitFor(); await page.goBack(); await act('iv-next').waitFor();
    assert(await page.locator('#overlay').evaluate(el => el.firstElementChild === window.reviewGuide));
    // Repeated keyboard events on the non-control surface may not skip steps.
    await page.locator('#overlay').evaluate(el => {
      el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', repeat: true, bubbles: true }));
      el.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', repeat: true, bubbles: true }));
    });
    assert.equal(await page.locator('#overlay h2').textContent(), guideHeading);
    // A slow response must not replace the restored parent after back.
    let releaseCrisis, enteredCrisis;
    const crisisReady = new Promise(resolve => { enteredCrisis = resolve; });
    const crisisGate = new Promise(resolve => { releaseCrisis = resolve; });
    await page.route('**/api/crisis?*', async route => { enteredCrisis(); await crisisGate; await route.continue(); });
    await page.locator('#overlay [data-act="open-crisis"]').first().click(); await crisisReady;
    await page.goBack(); await act('iv-next').waitFor();
    const crisisLoaded = page.waitForResponse(res => res.url().includes('/api/crisis?'));
    releaseCrisis(); await crisisLoaded; await page.waitForTimeout(80);
    assert(await page.locator('#overlay').evaluate(el => el.firstElementChild === window.reviewGuide));
    await page.unroute('**/api/crisis?*');
    await act('record-direct').click(); await page.locator('[data-input="cause"]').fill('资源返回后仍能保存原来的草稿');
    await act('save-note').click(); await closed();
    const entries = (await (await context.request.get(base + '/api/entries')).json()).items;
    assert.equal(entries.length, 1); assert.equal(entries[0].emotion, 'despair');
    await page.locator('[data-act="pick"][data-emotion="anxiety"]').click();
    await act('guide-start').click(); await act('iv-next').focus();
    await page.keyboard.down('Enter');
    const afterFirstKey = await page.locator('#overlay h2').textContent();
    await page.keyboard.down('Enter'); await page.keyboard.up('Enter');
    assert.equal(await page.locator('#overlay h2').textContent(), afterFirstKey);
    await page.keyboard.press('Escape'); await closed();
    console.log('PASS Crisis close/back restores the same guide; late responses and repeated keys are harmless');

    // Initial records failure offers an in-page retry.
    let failLog = true;
    await page.route('**/api/entries?*', route => failLog && route.request().method() === 'GET' ? route.abort('failed') : route.continue());
    await page.goto(base + '/#log'); await act('retry-log').waitFor();
    assert.equal(await page.locator('#log-body .skeleton').count(), 0);
    failLog = false; await act('retry-log').click();
    await page.locator(`[data-act="edit-entry"][data-id="${entries[0].id}"]`).waitFor();
    await page.unroute('**/api/entries?*');
    console.log('PASS Initial record load failure exposes a working page retry');

    // Association is consumed once, but a lost start response retries its frozen link.
    await page.locator(`[data-act="edit-entry"][data-id="${entries[0].id}"]`).click();
    await act('save-note-practice').click(); await page.locator('.practice-linked').waitFor();
    const starts = []; let lose = true;
    await page.route('**/api/practice-sessions?*', async route => {
      if (route.request().method() === 'POST') {
        starts.push(route.request().postDataJSON());
        if (lose) { lose = false; const res = await route.fetch(); assert(res.ok()); return route.abort('failed'); }
      }
      return route.continue();
    });
    await page.locator('[data-act="p-intro"][data-id="grounding"]').click();
    await page.locator('[data-act="p-score"][data-value="4"]').click();
    assert(await page.locator('[data-act="p-score"][data-value="4"]').evaluate(el => el === document.activeElement));
    await act('p-start').click(); await act('p-retry').waitFor(); await act('p-retry').click();
    await page.locator('.practice-progress').waitFor();
    assert.deepEqual(starts[0], starts[1]); assert.equal(starts[0].entry_id, entries[0].id);
    await act('p-close').last().click(); await closed();
    await page.locator('[data-act="p-intro"][data-id="grounding"]').click();
    await act('p-start').click(); await page.locator('.practice-progress').waitFor();
    assert.equal(starts[2].entry_id, null);
    await act('p-close').last().click(); await closed(); await page.unroute('**/api/practice-sessions?*');
    console.log('PASS Practice score focus, frozen retry association and one-practice link scope');

    // Expire a real session, then trigger 401 from the expression save itself.
    await page.locator('.tab[data-view="now"]').click();
    await page.locator('#expression-home-text').fill('会话过期也要保留的表达');
    await act('e-open').click(); await page.locator('.expression-save summary').click();
    await page.locator('#expression-emotion').selectOption('anxiety');
    const loggedOut = await context.request.post(base + '/api/auth/logout'); assert(loggedOut.ok());
    await act('e-save').click(); await page.locator('button[data-act="confirm-ok"]').click();
    await page.locator('[data-auth-form]').waitFor(); await closed();
    assert.equal(await page.locator('#view').evaluate(el => el.inert), false);
    const downloadEvent = page.waitForEvent('download'); await act('e-download').click();
    const download = await downloadEvent;
    assert((await fs.readFile(await download.path(), 'utf8')).includes('会话过期也要保留的表达'));
    await page.locator('[name="username"]').fill('still-editable');
    await page.locator('[name="password"]').fill('Private.test.2026!');
    await page.emulateMedia({ colorScheme: 'light' });
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'light');
    assert.equal(await page.locator('[name="username"]').inputValue(), 'still-editable');
    assert.equal(await page.locator('[name="password"]').inputValue(), 'Private.test.2026!');
    await page.screenshot({ path: '.cache/ui-preview/review-session-expired.png', fullPage: true });
    assert.deepEqual(errors, []);
    console.log('PASS Real save 401 unlocks login and retains a downloadable draft; no browser errors');
  } catch (error) {
    if (page) await page.screenshot({ path: '.cache/ui-preview/review-failure.png', fullPage: true }).catch(() => {});
    throw error;
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
