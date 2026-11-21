// Optional real-Chromium layout test: see README for installing the temporary
// browser dependencies. No network or real Telegram/Mongo are used.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');

let chromium, puppeteer;
try {
  puppeteer = require('puppeteer-core');
  require.resolve('@sparticuz/chromium');
} catch (_) { /* optional dependencies */ }

test('one-screen layouts, all bottom controls and touch dragging', {skip: !puppeteer}, async () => {
  chromium = (await import('@sparticuz/chromium')).default;
  const browser = await puppeteer.launch({args:chromium.args, executablePath:await chromium.executablePath(), headless:true});
  try {
    for (const [width, height] of [[320,480], [320,568], [360,640], [390,844], [640,360], [1024,768]]) {
      const page = await browser.newPage();
      await page.setViewport({width, height, isMobile:width < 700, hasTouch:true, deviceScaleFactor:1});
      await page.emulateMediaFeatures([{name:'prefers-reduced-motion', value:'reduce'}]);
      const errors = []; page.on('pageerror', error => errors.push(error.message));
      await page.goto(pathToFileURL(path.join(__dirname, '..', 'lunapark-offline.html')).href);
      const click = selector => page.$eval(selector, element => element.click());
      const checkPage = async () => {
        const dimensions = await page.evaluate(() => ({viewport:innerHeight, document:document.documentElement.scrollHeight,
          app:document.querySelector('.app').scrollHeight, appVisible:document.querySelector('.app').clientHeight}));
        assert.ok(dimensions.document <= dimensions.viewport + 1, JSON.stringify(dimensions));
        assert.ok(dimensions.app <= dimensions.appVisible + 1, JSON.stringify(dimensions));
      };
      await checkPage();
      await click('[data-action="enter"]');
      await checkPage();
      assert.equal(await page.$$eval('.scene-dock button', buttons => buttons.length), 6);
      for (const area of ['laboratory','production','buildings','expeditions','inventory','fighters']) {
        await click(`.scene-dock [data-area="${area}"]`);
        const result = await page.evaluate(() => {
          const dialog = document.querySelector('dialog'), rect = dialog.getBoundingClientRect();
          const content = dialog.querySelector('#dialog-content'), contentRect = content.getBoundingClientRect();
          const visible = [...dialog.querySelectorAll('button, select, summary')].filter(e => e.getClientRects().length && e.offsetParent !== null);
          const clipped = visible.filter(e => e.getBoundingClientRect().bottom > rect.bottom + 1).map(e => e.textContent);
          const clippedContent = [...content.querySelectorAll('button')].filter(e => e.getClientRects().length && e.getBoundingClientRect().bottom > contentRect.bottom + 1).map(e => e.textContent);
          const grids = [...dialog.querySelectorAll('.orb-grid, .base-art, .other-buildings')].filter(e => e.getClientRects().length).map(e => ({name:e.id || e.className, horizontal:e.scrollWidth-e.clientWidth, vertical:e.scrollHeight-e.clientHeight}));
          return {panel:dialog.dataset.panel, overflow:dialog.scrollHeight-dialog.clientHeight, clipped, clippedContent, grids};
        });
        assert.equal(result.panel, area);
        assert.ok(result.overflow <= 1 && result.clipped.length === 0 && result.clippedContent.length === 0 && result.grids.every(g => g.horizontal <= 1 && g.vertical <= 1),
                  `${width}x${height} ${area}: ${JSON.stringify(result)}`);
        await click('.dialog-close');
      }
      await page.evaluate(() => {
        const profile = window.eval('profile');
        profile.recipes = Array.from({length:6}, () => ({ingredients:['🚂 Паровой двигатель','⛵ Деревянный корабль'], result:'🐍 Электрический угорь'}));
      });
      await click('.scene-dock [data-area="laboratory"]');
      await click('[data-action="open-recipes"]');
      const recipeLayout = await page.evaluate(() => {
        const dialog = document.querySelector('dialog'), bottom = dialog.getBoundingClientRect().bottom;
        return {overflow:dialog.scrollHeight - dialog.clientHeight,
          clipped:[...dialog.querySelectorAll('button')].filter(e => e.getClientRects().length && e.getBoundingClientRect().bottom > bottom + 1).map(e => e.textContent)};
      });
      assert.ok(recipeLayout.overflow <= 1 && !recipeLayout.clipped.length, `${width}x${height} recipes: ${JSON.stringify(recipeLayout)}`);
      await click('.dialog-close');
      if (width === 320) {
        await click('.scene-dock [data-area="laboratory"]');
        const center = selector => page.$eval(selector, element => {const r=element.getBoundingClientRect();return {x:r.x+r.width/2, y:r.y+r.height/2};});
        const client = await page.createCDPSession();
        await click('[data-action="craft-filter"][data-filter="spirits"]');
        assert.ok(await page.$('[data-ingredient="spirit_w"]'), 'spirits group');
        await click('[data-action="craft-filter"][data-filter="all"]');
        assert.equal(await page.$eval('[data-action="craft-next"]', button => button.disabled), false);
        await click('[data-action="craft-next"]');
        assert.equal(await page.$eval('#craft-page-label', label => label.textContent), '2 / 2');
        await click('[data-action="craft-prev"]');
        const from = await center('[data-ingredient="w"]'), to = await center('#brew-stage');
        await client.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{...from,id:1}]});
        await client.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{...to,id:1}]});
        await client.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
        assert.equal(await page.$eval('#craft-first', select => select.value), 'w');
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
  } finally { await browser.close(); }
});
