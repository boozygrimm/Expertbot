const {test}=require('node:test'), assert=require('node:assert/strict'),path=require('node:path');
const {pathToFileURL}=require('node:url');let puppeteer;try{puppeteer=require('puppeteer-core');require.resolve('@sparticuz/chromium');}catch(_){}
test('discovery design fits screen, explores, pages and supports touch drag', {skip:!puppeteer},async()=>{
 const chromium=(await import('@sparticuz/chromium')).default;
 const browser=await puppeteer.launch({args:chromium.args,executablePath:await chromium.executablePath(),headless:true});
 try{for(const [width,height] of [[320,480],[320,568],[360,640],[390,844],[640,360],[1024,768]]){
  const p=await browser.newPage();await p.setViewport({width,height,isMobile:width<700,hasTouch:true});await p.emulateMediaFeatures([{name:'prefers-reduced-motion',value:'reduce'}]);
  const errors=[];p.on('pageerror',e=>errors.push(e.message));await p.goto(pathToFileURL(path.join(__dirname,'..','lunapark-discovery.html')).href);
  const click=s=>p.$eval(s,e=>e.click());
  const fit=async label=>{const r=await p.evaluate(()=>{const shell=document.querySelector('.shell');const buttons=[...document.querySelectorAll('button,input,select')].filter(e=>e.getClientRects().length);return {body:document.documentElement.scrollHeight,client:innerHeight,shell:shell.scrollHeight,shellClient:shell.clientHeight,clipped:buttons.filter(e=>{const r=e.getBoundingClientRect();return r.bottom>innerHeight+1||r.top< -1||r.right>innerWidth+1||r.left< -1}).map(e=>e.outerHTML.slice(0,100))}});assert.ok(r.body<=r.client+1 && r.shell<=r.shellClient+1 && !r.clipped.length,`${width}x${height} ${label}: ${JSON.stringify(r)}`);};
  await fit('initial');
  for(const [a,b] of [['w','f'],['w','w'],['w','e'],['f','a'],['f','e'],['e','e'],['e','a'],['a','a'],['steam','e']]){if(await p.$eval('#page-label',e=>e.textContent.startsWith('2')))await click('#prev');await click('[data-slot="0"]');await click(`[data-id="${a}"]`);await click('[data-slot="1"]');await click(`[data-id="${b}"]`);await click('#mix');}
  assert.ok((await p.$eval('#open-count',e=>Number(e.textContent)))>=10);
  await click('#search-toggle');await fit('search');await click('#options-toggle');await fit('options');
  const overlap=await p.evaluate(()=>{const menus=[...document.querySelectorAll('.hero-action')].map(e=>e.getBoundingClientRect());return [...document.querySelectorAll('.slots button')].some(el=>{const a=el.getBoundingClientRect();return menus.some(b=>a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top);});});
  assert.equal(overlap,false,`${width}x${height}: cauldron controls overlap navigation`);await click('#options-toggle');
  await click('#prev');assert.equal(await p.$$eval('#elements .element',e=>e.length),12);await click('#next');assert.equal(await p.$eval('#page-label',e=>e.textContent),'2 / 2');await click('#prev');
  await click('[data-view="album"]');await fit('album');await click('.recipe');await fit('recipe detail');await click('.detail-card button');await fit('back to cauldron');
  await click('[data-view="history"]');await fit('history');await click('#back');
  if(width===320&&height===480){const center=s=>p.$eval(s,e=>{const r=e.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2};});const from=await center('[data-id="w"]'),to=await center('[data-slot="0"]');const c=await p.createCDPSession();await c.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{...from,id:1}]});await c.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{...to,id:1}]});await c.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});assert.match(await p.$eval('[data-slot="0"]',e=>e.title),/Вода/);}
  assert.deepEqual(errors,[]);await p.close();
 }}finally{await browser.close();}
});

test('new element flies from cauldron into its actual collection slot', {skip:!puppeteer},async()=>{
 const chromium=(await import('@sparticuz/chromium')).default;
 const browser=await puppeteer.launch({args:chromium.args,executablePath:await chromium.executablePath(),headless:true});
 try{const p=await browser.newPage();await p.setViewport({width:360,height:640,isMobile:true,hasTouch:true});await p.goto(pathToFileURL(path.join(__dirname,'..','lunapark-discovery.html')).href);
 const click=s=>p.$eval(s,e=>e.click());await click('[data-id="w"]');await click('[data-id="f"]');await click('#mix');
 assert.equal(await p.$eval('#mix',e=>e.dataset.state),'reacting');await p.waitForSelector('.flight-clone');
 const frames=await p.evaluate(()=>{const ghost=document.querySelector('.flight-clone'),target=document.querySelector('[data-id="steam"]');const animation=ghost.getAnimations()[0];animation.pause();
   const rect=t=>{animation.currentTime=t;const r=ghost.getBoundingClientRect();return{x:r.x,y:r.y,w:r.width}};
   const start=rect(0),mid=rect(550),end=rect(1100),destination=target.getBoundingClientRect();
   return {start,mid,end,destination:{x:destination.x,y:destination.y,w:destination.width},targetHidden:target.classList.contains('in-flight'),icon:!!ghost.querySelector('.symbol') && /[\p{Extended_Pictographic}]/u.test(ghost.querySelector('.symbol').textContent)};});
 assert.equal(frames.icon,true);assert.equal(frames.targetHidden,true);
 assert.ok(Math.abs(frames.end.x-frames.destination.x)<4&&Math.abs(frames.end.y-frames.destination.y)<4,JSON.stringify(frames));
 assert.ok(frames.mid.y<frames.end.y && frames.start.y<frames.end.y,JSON.stringify(frames));
 await p.evaluate(()=>document.querySelector('.flight-clone').getAnimations()[0].finish());
 await p.waitForFunction(()=>!document.querySelector('.flight-clone'));assert.equal(await p.$eval('[data-id="steam"]',e=>e.classList.contains('in-flight')),false);
 await p.close();}finally{await browser.close();}
});
