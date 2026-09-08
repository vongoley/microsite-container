const { test } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

test('mobile notes survive keyboard resize; per-row units persist and respect locking', async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || "msedge"});
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(process.env.TRAINING_LOG_URL || 'http://127.0.0.1:8001/?month=2026-09');
    await page.locator('[data-date="2026-09-08"]').click();
    await page.locator('#trainingOptions [data-training="legs"]').click();
    const weight = page.locator('[data-field="weight"]').first();
    await weight.fill('60.25');
    const unit = page.locator('[data-weight-unit]').first();
    assert.equal(await unit.textContent(), 'Kg');
    await unit.click();
    assert.equal(await unit.textContent(), 'Lbs');
    assert.equal(await weight.inputValue(), '60.25');
    const box = await unit.boundingBox();
    assert.ok(box.width >= 44 && box.height >= 44);
    await page.locator('[data-note-training]').first().click();
    const note = page.locator('.note-popover textarea').first();
    await note.fill('膝盖稳定，保留两次余力');
    await page.setViewportSize({width: 390, height: 500});
    await page.evaluate(() => window.dispatchEvent(new Event('resize')));
    assert.ok(await note.isVisible());
    assert.equal(await note.evaluate(el => el === document.activeElement), true);
    await page.setViewportSize({width: 390, height: 844});
    assert.ok(await note.isVisible());
    await note.press('Escape');
    assert.equal(await note.isVisible(), false);
    await page.locator('#saveDay').click();
    await page.reload();
    await page.locator('[data-date="2026-09-08"]').click();
    assert.equal(await unit.textContent(), 'Lbs');
    await page.locator('[data-note-training]').first().click();
    assert.equal(await note.inputValue(), '膝盖稳定，保留两次余力');
    await note.press('Escape');
    await page.locator('#toggleLock').click();
    assert.equal(await page.locator('[data-weight-unit]').count(), 0);
    assert.match(await page.locator('.locked-summary').first().innerText(), /60.25\s*Lbs/);
    await page.locator('#toggleLock').click();
    await unit.click();
    assert.equal(await unit.textContent(), 'Kg');
    await page.locator('#saveDay').click();
    const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('training-log-local-plan')));
    assert.equal(saved.value['2026-09-08'].details.legs[0].weightUnit, 'kg');
    assert.equal(saved.value['2026-09-08'].details.legs[0].weight, 60.25);
    await page.setViewportSize({width: 1280, height: 900});
    await page.locator('[data-date="2026-09-08"]').click();
    assert.ok(await page.locator('.detail-field-note input').first().isVisible());
    assert.equal(await unit.textContent(), 'Kg');
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

