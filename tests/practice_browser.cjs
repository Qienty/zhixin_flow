/* Run only against an isolated TETHER_DATA_DIR server. Needs Playwright + Edge. */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const base = process.env.TETHER_TEST_URL || 'http://127.0.0.1:8765';
const screenshots = path.resolve('.cache/ui-preview');

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.EDGE_EXECUTABLE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' });
  let page;
  try {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1, isMobile: true, hasTouch: true, serviceWorkers: 'block', reducedMotion: 'reduce' });
    const registered = await context.request.post(base + '/api/auth/register', { data: { username: 'practice_' + Date.now(), password: 'Practice.test.2026!' } });
    assert(registered.ok(), await registered.text());
    page = await context.newPage();
    const errors = []; page.on('pageerror', err => errors.push(err.message));
    const act = name => page.locator(`[data-act="p-${name}"]`);
    const sessions = async () => (await (await context.request.get(base + '/api/practice-sessions')).json()).items;
    const detail = async id => (await context.request.get(base + '/api/practice-sessions/' + id)).json();
    const closed = () => page.locator('#overlay').waitFor({ state: 'hidden' });
    const open = async id => { await page.locator(`[data-act="p-open"][data-id="${id}"]`).click(); await page.locator('.practice-focus h2').waitFor(); };
    const next = async expected => { await act('next').click(); await page.getByRole('heading', { name: expected, exact: true }).waitFor(); };
    await fs.mkdir(screenshots, { recursive: true });
    await page.goto(base + '/#practice');
    await page.locator('.practice-card').first().waitFor();
    assert.equal(await page.locator('.practice-card').count(), 4);
    await page.screenshot({ path: path.join(screenshots, 'practice-library-light.png'), fullPage: true });
    console.log('PASS Library has four practices and five mobile tabs');

    await page.locator('[data-act="p-intro"][data-id="grounding"]').click();
    await page.locator('[data-act="p-score"][data-value="4"]').click();
    await act('start').click();
    await page.getByRole('heading', { name: '找一个舒服的位置' }).waitFor();
    const grounding = (await sessions())[0];
    assert.equal(grounding.before_score, 4);
    await act('timer').click();
    await page.waitForFunction(() => Number(document.querySelector('[data-practice-time]').textContent) < 30);
    await act('timer').click();
    const paused = await page.locator('[data-practice-time]').textContent();
    await page.waitForTimeout(1100);
    assert.equal(await page.locator('[data-practice-time]').textContent(), paused);
    await page.screenshot({ path: path.join(screenshots, 'practice-step-light.png'), fullPage: true });
    await next('看看身边');
    await act('close').last().click(); await closed();
    await page.reload(); await page.locator('.practice-card').first().waitFor();
    await open(grounding.id);
    assert.equal(await page.locator('.practice-focus h2').textContent(), '看看身边');
    await next('把注意力带回来'); await next('现在，感觉怎样？');
    await page.locator('[data-act="p-score"][data-value="3"]').click();
    await page.locator('[data-act="p-relief"][data-value="worse"]').click();
    await page.locator('[data-practice-input="note"]').fill('记录真实感受 <script>no code</script>');
    await page.screenshot({ path: path.join(screenshots, 'practice-feedback-light.png'), fullPage: true });
    await act('finish').click(); await page.getByRole('heading', { name: '这一次，已记录' }).waitFor();
    assert(await act('support').isVisible());
    const completed = await detail(grounding.id);
    assert.equal(completed.status, 'completed'); assert.equal(completed.after_score, 3); assert.equal(completed.relief, 'worse');
    assert.equal(await page.locator('.practice-answer script').count(), 0);
    await act('support').click(); await page.locator('#overlay .crisis-card').waitFor();
    await page.locator('#overlay [data-act="close"]').first().click(); await closed();
    console.log('PASS Start, timer pause, checkpoint, refresh, feedback, safe text and support');

    // Preserve an optional response when the server is unreachable; retry the exact PATCH.
    await page.locator('[data-act="p-intro"][data-id="notice-thoughts"]').click();
    await act('start').click(); await page.locator('[data-practice-input="thought"]').waitFor();
    const thoughtId = (await sessions())[0].id;
    await page.locator('[data-practice-input="thought"]').fill('暂时还没想清楚，但可以慢慢来。');
    const attempts = []; let failing = true;
    await page.route('**/api/practice-sessions/' + thoughtId + '?*', async route => {
      if (route.request().method() === 'PATCH') { attempts.push(route.request().postDataJSON()); if (failing) return route.abort('failed'); }
      return route.continue();
    });
    await act('next').click(); await act('retry').waitFor();
    assert.equal(await page.locator('[data-practice-input="thought"]').inputValue(), '暂时还没想清楚，但可以慢慢来。');
    assert.equal((await detail(thoughtId)).step_index, 0);
    failing = false; await act('retry').click(); await page.getByRole('heading', { name: '试着加上一个开头' }).waitFor();
    assert.deepEqual(attempts[0], attempts[1]);
    await page.unroute('**/api/practice-sessions/' + thoughtId + '?*');
    await page.goBack(); await closed();
    await open(thoughtId); await next('留意眼前'); await next('现在，感觉怎样？');
    await page.locator('[data-practice-input="note"]').fill('稍后补充');
    await act('close').last().click(); await closed();
    await open(thoughtId);
    assert.equal(await page.locator('[data-practice-input="note"]').inputValue(), '稍后补充');
    await act('finish').click(); await page.getByRole('heading', { name: '这一次，已记录' }).waitFor();
    const unscored = await detail(thoughtId);
    assert.equal(unscored.before_score, null); assert.equal(unscored.after_score, null);
    const stats = await (await context.request.get(base + '/api/practice-summary?tz_offset=480')).json();
    assert.equal(stats.completed, 2); assert.equal(stats.paired, 1);
    await act('close').last().click(); await closed();
    console.log('PASS Failed save retains text; retry is stable; browser back and optional ratings work');

    // Concurrent tab updates must never silently overwrite newer saved data.
    await page.locator('[data-act="p-intro"][data-id="small-action"]').click();
    await act('start').click(); await page.locator('[data-practice-input="action"]').waitFor();
    const actionId = (await sessions())[0].id;
    await context.request.patch(base + '/api/practice-sessions/' + actionId, { data: { request_id: 'other-tab-update', revision: 0, responses: { action: 'another tab saved this' } } });
    await page.locator('[data-practice-input="action"]').fill('local change');
    await act('next').click(); await act('reopen').waitFor();
    assert.equal(await page.locator('[data-practice-input="action"]').inputValue(), 'local change');
    await act('reopen').click(); await page.locator('button[data-act="confirm-ok"]').click();
    await page.waitForFunction(() => document.querySelector('[data-practice-input="action"]')?.value === 'another tab saved this');
    await act('stop').click(); await page.locator('button[data-act="confirm-ok"]').click();
    await page.getByRole('heading', { name: '这一次，已记录' }).waitFor();
    assert.equal((await detail(actionId)).status, 'stopped');
    await act('delete').click(); await page.locator('button[data-act="confirm-ok"]').click(); await closed();
    assert.equal((await context.request.get(base + '/api/practice-sessions/' + actionId)).status(), 404);
    console.log('PASS Conflict preserves local input; explicit reopen, early stop and deletion work');

    // A committed start whose response is lost must retry without creating a duplicate.
    const starts = []; let loseStartResponse = true;
    const countBeforeStart = (await sessions()).length;
    await page.route('**/api/practice-sessions?*', async route => {
      if (route.request().method() === 'POST') {
        starts.push(route.request().postDataJSON());
        if (loseStartResponse) {
          loseStartResponse = false;
          const response = await route.fetch();
          assert(response.ok());
          return route.abort('failed');
        }
      }
      return route.continue();
    });
    await page.locator('[data-act="p-intro"][data-id="notice-thoughts"]').click();
    await act('start').click(); await act('retry').waitFor();
    assert.equal((await sessions()).length, countBeforeStart + 1);
    await act('retry').click(); await page.locator('[data-practice-input="thought"]').waitFor();
    assert.deepEqual(starts[0], starts[1]);
    assert.equal(starts.length, 2);
    assert.equal((await sessions()).length, countBeforeStart + 1);
    await page.unroute('**/api/practice-sessions?*');

    // A permanently failed save can be left explicitly, without trapping the user.
    const removedId = (await sessions())[0].id;
    await page.locator('[data-practice-input="thought"]').fill('未确认的本页文字');
    await context.request.delete(base + '/api/practice-sessions/' + removedId);
    await act('next').click(); await act('retry').waitFor();
    await act('discard').click();
    await page.locator('button[data-act="confirm-cancel"]').click();
    assert.equal(await page.locator('[data-practice-input="thought"]').inputValue(), '未确认的本页文字');
    assert(await act('retry').isEnabled());
    await act('discard').click();
    assert((await page.locator('.confirm-body').textContent()).includes('不会撤销或删除记录'));
    await page.locator('button[data-act="confirm-ok"]').click(); await closed();
    await page.locator('.practice-card').first().waitFor();
    assert.equal((await sessions()).length, countBeforeStart);
    console.log('PASS Lost start response retries without duplicates; permanent save failure has confirmed exit');

    // Link from an existing emotion record, preserving edits first.
    const emotion = await (await context.request.post(base + '/api/entries', { data: { emotion: 'anxiety', severity: 3, cause: 'link fixture', tz_offset: 480 } })).json();
    await page.locator('.tab[data-view="log"]').click();
    await page.locator(`[data-act="edit-entry"][data-id="${emotion.entry.id}"]`).click();
    await page.locator('[data-input="cause"]').fill('edited before practice');
    await page.locator('[data-act="save-note-practice"]').click();
    await page.locator('.practice-linked').waitFor();
    assert.equal((await (await context.request.get(base + '/api/entries/' + emotion.entry.id)).json()).cause, 'edited before practice');
    await page.locator('[data-act="p-intro"][data-id="make-room"]').click(); await act('start').click();
    await page.locator('.practice-progress').waitFor();
    assert.equal((await sessions())[0].entry_id, emotion.entry.id);
    await act('close').last().click(); await closed();
    const exported = await context.request.get(base + '/api/practice-export');
    assert.equal((await exported.json()).count, 3);
    assert(exported.headers()['content-disposition'].includes('attachment'));
    console.log('PASS Linked emotion edits are saved and independent practice JSON exports');

    for (const lang of ['zh', 'en']) {
      for (const theme of ['light', 'dark']) {
        await page.evaluate(({ lang, theme }) => { localStorage.setItem('tether.lang', lang); localStorage.setItem('tether.theme', theme); }, { lang, theme });
        await page.reload(); await page.locator('.practice-card').first().waitFor();
        for (const width of [320, 390, 430, 1280]) {
          await page.setViewportSize({ width, height: 844 });
          assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${lang} ${theme} ${width}: page overflow`);
          assert.equal(await page.locator('.tab').count(), 5);
          assert((await page.locator('.practice-card').first().boundingBox()).width <= 560);
          await page.locator('[data-act="p-intro"][data-id="grounding"]').click();
          assert(await page.locator('.practice-focus').evaluate(el => el.scrollWidth <= el.clientWidth), `${lang} ${theme} ${width}: intro overflow`);
          await act('close').click(); await closed();
          if (width === 390 && lang === 'zh') await page.screenshot({ path: path.join(screenshots, `practice-library-${theme}.png`), fullPage: true });
        }
        console.log(`PASS ${lang} ${theme} layout at 320 / 390 / 430 / 1280 px`);
      }
    }
    const stranger = await browser.newContext();
    await stranger.request.post(base + '/api/auth/register', { data: { username: 'isolation_' + Date.now(), password: 'Practice.test.2026!' } });
    assert.equal((await stranger.request.get(base + '/api/practice-sessions/' + grounding.id)).status(), 404);
    assert.equal((await (await stranger.request.get(base + '/api/practice-export')).json()).count, 0);
    await stranger.close();
    assert.deepEqual(errors, []);
    console.log('PASS Account isolation and no uncaught browser errors');
  } catch (error) {
    if (page) await page.screenshot({ path: path.join(screenshots, 'practice-failure.png'), fullPage: true }).catch(() => {});
    throw error;
  } finally { await browser.close(); }
})().catch(err => { console.error(err); process.exitCode = 1; });
