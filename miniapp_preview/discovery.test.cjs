const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require('jsdom');
function open(reduced=true) {
  const html=fs.readFileSync(path.join(__dirname,'..','lunapark-discovery.html'),'utf8');
  const errors=[],network=[];const v=new VirtualConsole();v.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM(html,{runScripts:'dangerously',url:'file:///lunapark-discovery.html',pretendToBeVisual:true,virtualConsole:v,beforeParse(w){w.fetch=(...x)=>{network.push(x);throw Error('network forbidden');};w.matchMedia=()=>({matches:reduced});}});
  return {dom,errors,network};
}
test('situation window, failed attempts, clearing, known pairs and album',()=>{
 const {dom,errors,network}=open(),d=dom.window.document;
 const click=s=>{const e=d.querySelector(s);assert.ok(e,s);e.click();};
 const state=()=>d.querySelector('#mix').dataset.state,slot=i=>d.querySelector(`[data-slot="${i}"]`).title;
 try{
  assert.equal(d.querySelectorAll('.element').length,4);assert.equal(state(),'empty');assert.equal(d.querySelector('#mix').textContent,'');
  click('[data-view="album"]');assert.match(d.querySelector('#overlay-body').textContent,/Пока пусто/);click('#back');
  click('[data-id="w"]');assert.equal(state(),'waiting');assert.match(d.querySelector('#mix').textContent,/ВТОРОЙ/);assert.match(slot(0),/Вода/);
  click('[data-id="a"]');assert.equal(state(),'mix');click('#mix');
  assert.equal(state(),'clear');assert.match(d.querySelector('#mix').textContent,/ОЧИСТИТЬ/);assert.match(slot(0),/Вода/);assert.match(slot(1),/Воздух/);
  click('[data-slot="1"]');click('[data-id="f"]');assert.equal(state(),'mix');click('#mix');
  assert.equal(state(),'known');assert.match(d.querySelector('#mix').textContent,/БЫЛОПар/);assert.ok(d.querySelector('[data-id="steam"]'));
  click('[data-view="album"]');assert.equal(d.querySelectorAll('.recipe').length,1);click('.recipe');assert.match(d.querySelector('.detail-card').textContent,/Вода.*Огонь|Огонь.*Вода/);
  [...d.querySelectorAll('.detail-card button')].find(x=>x.textContent.includes('В котёл')).click();assert.equal(state(),'known');
  click('[data-slot="0"]');click('[data-id="a"]');click('[data-slot="1"]');click('[data-id="w"]');assert.equal(state(),'known-fail');assert.match(d.querySelector('#mix').textContent,/БЫЛО.*НЕТ.*РЕАКЦИИ/);
  click('#mix');assert.equal(state(),'empty');assert.match(slot(0),/Пустой/);assert.match(slot(1),/Пустой/);assert.equal(d.querySelectorAll('.element').length,5);
  click('[data-id="f"]');click('[data-id="w"]');assert.equal(state(),'known');assert.match(d.querySelector('#mix').textContent,/Пар/);assert.equal(d.querySelector('#mix').disabled,true);
  click('[data-view="history"]');assert.equal(d.querySelectorAll('.history-item').length,2);
  assert.deepEqual(network,[]);assert.deepEqual(errors,[]);
 }finally{dom.window.close();}
});
test('search, favorites, category and recent filters operate on opened elements only',()=>{
 const {dom,errors}=open(),d=dom.window.document;const click=s=>d.querySelector(s).click();
 try{
  click('.token-wrap .favorite');click('[data-filter="favorites"]');assert.equal(d.querySelectorAll('.element').length,1);assert.equal(d.querySelector('.element').dataset.id,'w');
  click('[data-filter="all"]');click('#search-toggle');const input=d.querySelector('#search');input.value='огонь';input.dispatchEvent(new dom.window.Event('input',{bubbles:true}));assert.equal(d.querySelector('.element').dataset.id,'f');assert.equal(d.querySelectorAll('.element').length,1);
  click('#search-close');click('[data-id="e"]');click('[data-filter="recent"]');assert.equal(d.querySelector('.element').dataset.id,'e');
  click('[data-filter="all"]');click('#options-toggle');const select=d.querySelector('#category');select.value='nature';select.dispatchEvent(new dom.window.Event('change',{bubbles:true}));assert.equal(d.querySelectorAll('.element').length,0);
  assert.deepEqual(errors,[]);
 }finally{dom.window.close();}
});
test('after success choose first then second, while explicit slot choice and clearing win',()=>{
 const {dom}=open(),d=dom.window.document,click=s=>d.querySelector(s).click(),slot=i=>d.querySelector(`[data-slot="${i}"]`).title;
 try{
  click('[data-id="w"]');click('[data-id="f"]');click('#mix');
  click('[data-id="e"]');assert.match(slot(0),/Земля/);assert.match(slot(1),/Огонь/);
  click('[data-id="a"]');assert.match(slot(0),/Земля/);assert.match(slot(1),/Воздух/);
  click('#mix');click('[data-id="w"]');assert.match(slot(0),/Вода/);assert.match(slot(1),/Воздух/);
  click('#mix');assert.equal(d.querySelector('#mix').dataset.state,'clear');assert.match(slot(0),/Вода/);assert.match(slot(1),/Воздух/);
  click('#mix');assert.match(slot(0),/Пустой/);assert.match(slot(1),/Пустой/);
  click('[data-id="f"]');click('[data-id="a"]');click('#mix');click('[data-slot="1"]');click('[data-id="w"]');assert.match(slot(0),/Огонь/);assert.match(slot(1),/Вода/);assert.equal(d.querySelector('#mix').dataset.state,'known');
 }finally{dom.window.close();}
});
test('history pages show ten entries and element emoji replace initials',()=>{
 const {dom,errors}=open(),d=dom.window.document,click=s=>d.querySelector(s).click();
 try{
  const pairs=[['w','f'],['w','w'],['w','e'],['f','a'],['f','e'],['e','e'],['e','a'],['a','a'],['w','a'],['f','f']];
  for(const [a,b] of pairs){click('[data-slot="0"]');click(`[data-id="${a}"]`);click('[data-slot="1"]');click(`[data-id="${b}"]`);click('#mix');}
  click('[data-slot="0"]');click('[data-id="steam"]');click('[data-slot="1"]');click('[data-id="w"]');click('#mix');
  click('[data-view="history"]');assert.equal(d.querySelectorAll('.history-item').length,10);assert.equal(d.querySelector('#overlay-page').textContent,'1 / 2');
  click('#overlay-next');assert.equal(d.querySelectorAll('.history-item').length,1);
  assert.ok([...d.querySelectorAll('.element .symbol')].every(x=>/[\p{Extended_Pictographic}]/u.test(x.textContent)));assert.equal(d.querySelectorAll('.symbol-letter').length,0);assert.deepEqual(errors,[]);
 }finally{dom.window.close();}
});

test('mix reaction protects the pair while it animates',async()=>{
 const {dom,errors}=open(false),d=dom.window.document,click=s=>d.querySelector(s).click();
 try{
  click('[data-id="w"]');click('[data-id="f"]');click('#mix');assert.equal(d.querySelector('#mix').dataset.state,'reacting');assert.equal(d.querySelector('.hero').classList.contains('reacting'),true);
  click('[data-id="e"]');assert.match(d.querySelector('[data-slot="0"]').title,/Вода/);
  await new Promise(resolve=>setTimeout(resolve,750));assert.equal(d.querySelector('#mix').dataset.state,'known');assert.ok(d.querySelector('[data-id="steam"]'));assert.deepEqual(errors,[]);
 }finally{dom.window.close();}
});
