(() => {
'use strict';
const data = window.DISCOVERY_DATA;
const $ = s => document.querySelector(s);
const basics = ['w','f','e','a'];
const PAGE_SIZE = 12;
const categories = {
  nature:new Set('steam stone moss energy lava rain cloud lake sea ocean river swamp forest tree grass flower sand dust wind pressure ice snow mountain desert soil earth fire water air'.split(' ')),
  animals:new Set('beast bird fish worm insect ant bee butterfly bat bear whale wolf snake spider crab squid octopus turtle penguin panda monkey pig cow horse'.split(' ')),
  machines:new Set('wheel cart car motor plane robot steam_boiler steam_engine steamboat bicycle motorcycle tractor train tool metal weapon'.split(' '))
};
const names = data.elements;
const recipes = data.recipes;
const pairKey = ids => JSON.stringify([...ids].sort());
const byPair = new Map(recipes.map(r => [pairKey(r.ingredients), r]));
const usedIngredients = new Set(recipes.flatMap(r => r.ingredients));
const label = id => (names[id] || id).replace(/^[\p{Extended_Pictographic}\p{Regional_Indicator}\p{Emoji_Modifier}\uFE0F\u200D]+\s+/u, '');
const HISTORY_SIZE = 10;
const GLYPH = {
water:'<path d="M16 4C16 4 6 15 6 21a10 10 0 0 0 20 0C26 15 16 4 16 4Z"/>',
fire:'<path d="M16 3c0 5-4 6-4 12a4 4 0 0 0 8 0c0-3-2-4-2-7 0 2-2 3-2 4 0-3 0-6 0-9Z"/>',
earth:'<path d="M4 23l7-9 5 6 4-7 8 10z"/>',
air:'<path d="M4 15a7 7 0 1 0 7 7h14a5 5 0 1 0-5-5z"/>',
plant:'<path d="M5 27C5 15 15 5 27 5C27 17 17 27 5 27Z"/>',
tree:'<path d="M16 28v-9M9 19a7 7 0 1 1 14 0 7 7 0 0 1-14 0z"/>',
flower:'<path d="M16 7a4 4 0 1 0 .1 0 4 4 0 0 0-.1 0zM16 11v6M10 13.5h5M22 13.5h-5"/>',
seed:'<path d="M16 6a5 5 0 1 0 .1 0 5 5 0 0 0-.1 0zM16 12v14M12 18h8"/>',
cloud:'<path d="M8 22a5 5 0 0 1 1-9 6 6 0 0 1 11-1 5 5 0 0 1 1 9z"/>',
rain:'<path d="M8 18a5 5 0 0 1 1-9 6 6 0 0 1 11-1 5 5 0 0 1 1 9z"/><path d="M12 24v3M18 24v4M23 24v3"/>',
snow:'<path d="M16 5v22M6 16h20M9 8l14 16M23 8L9 24"/>',
ice:'<path d="M16 4C16 4 7 14 7 20a9 9 0 0 0 18 0C25 14 16 4 16 4Z"/><path d="M16 10v12M11 14h10M12 20h8"/>',
mountain:'<path d="M3 27l9-15 6 9 4-7 7 13z"/>',
sand:'<path d="M5 21c2-3 5-3 7 0s5 3 7 0 5-3 7 0"/><path d="M8 27c2-2 4-2 6 0s4 2 6 0"/>',
dust:'<circle cx="10" cy="12" r="2"/><circle cx="20" cy="9" r="2.5"/><circle cx="15" cy="20" r="2"/><circle cx="24" cy="19" r="1.5"/>',
swamp:'<path d="M6 22c2-4 5-4 7 0s5 4 7 0 5-4 7 0"/><path d="M10 27v-3M16 28v-4M22 27v-3"/>',
sea:'<path d="M3 17c4-5 8-5 12 0s8 5 12 0"/><path d="M3 23c4-5 8-5 12 0s8 5 12 0"/>',
wave:'<path d="M3 16c4-5 8-5 12 0s8 5 12 0"/>',
steam:'<path d="M9 9c3-3 6-3 9 0s6 3 9 0M9 18c3-3 6-3 9 0s6 3 9 0"/>',
lava:'<path d="M16 4c4 5 8 8 8 13a8 8 0 0 1-16 0c0-5 4-8 8-13z"/>',
metal:'<path d="M16 4l10 6v12l-10 6-10-6V10z"/>',
mineral:'<path d="M16 4l10 8-10 16L6 12z"/>',
salt:'<path d="M9 9h14v14H9z"/><path d="M16 9v14M9 16h14"/>',
acid:'<path d="M16 5c5 6 8 9 8 13a8 8 0 0 1-16 0c0-4 3-7 8-13z"/><circle cx="12" cy="20" r="2"/>',
poison:'<path d="M16 5a6 6 0 0 0-6 6c0 4 3 5 3 8h6c0-3 3-4 3-8a6 6 0 0 0-6-6z"/><path d="M13 22h6"/>',
medicine:'<path d="M9 12h14a3 3 0 0 1 3 3v4a3 3 0 0 1-3 3H9a3 3 0 0 1-3-3v-4a3 3 0 0 1 3-3z"/><path d="M9 16h14"/>',
alcohol:'<path d="M9 10h14l-2 14H11z"/><path d="M12 10V6h8v4"/>',
food:'<path d="M9 4v18M9 12h14v8"/><path d="M23 8v8"/>',
milk:'<path d="M11 6h10l-1 20H12z"/><path d="M11 12h10"/>',
cheese:'<path d="M5 12h22l-3 12H8z"/><circle cx="12" cy="16" r="2"/><circle cx="20" cy="18" r="2"/>',
bread:'<path d="M5 16c0-4 4-7 11-7s11 3 11 7v8H5z"/><path d="M9 14c2-2 5-2 7 0M16 14c2-2 5-2 7 0"/>',
meat:'<path d="M8 12c2-4 8-4 10 0 2 3 2 7 0 9-2-2-4-3-4-4 0-3 2-4 4-5z"/>',
egg:'<path d="M16 5c5 5 9 10 9 16a9 9 0 0 1-18 0c0-6 4-11 9-16z"/>',
fruit:'<path d="M16 8c5 0 9 4 9 9 0 5-4 10-9 10s-9-5-9-10c0-5 4-9 9-9z"/><path d="M16 8V5"/>',
vegetable:'<path d="M16 6v18M12 12l4-4 4 4M11 18l5-4 5 4"/>',
bird:'<path d="M5 18c5-8 10-8 15 0l-3 7H8z"/><path d="M5 18l-2 2M20 18l2 2"/>',
fish:'<path d="M4 16c4-6 12-6 16 0-4 6-12 6-16 0z"/><path d="M20 16l6-4v8z"/>',
beast:'<circle cx="11" cy="18" r="3"/><circle cx="21" cy="18" r="3"/><circle cx="16" cy="13" r="4"/>',
insect:'<path d="M16 10c-3 0-5 2-5 5v6c0 2 2 4 5 4s5-2 5-4v-6c0-3-2-5-5-5z"/><path d="M11 12l-4-2M21 12l4-2M11 22l-4 2M21 22l4 2"/>',
beetle:'<path d="M16 8c-4 0-7 3-7 7v4c0 3 3 6 7 6s7-3 7-6v-4c0-4-3-7-7-7z"/><path d="M10 12l-3-1M22 12l3-1M10 21l-3 1M22 21l3 1"/>',
worm:'<path d="M5 16c3-4 6-4 9 0s6 4 9 0"/><path d="M5 20c3-4 6-4 9 0s6 4 9 0"/>',
snake:'<path d="M5 10c4 0 4 6 8 6s4-6 8-6 4 6 6 6"/>',
lizard:'<path d="M5 18c4-2 8-2 10 0l5-2v6l-5 2c-4 2-8 2-10 0z"/>',
turtle:'<path d="M8 14h16v10H8z"/><circle cx="16" cy="19" r="4"/><path d="M8 14l-3-4M24 14l3-4"/>',
frog:'<path d="M8 16c0-4 4-6 8-6s8 2 8 6c0 4-4 6-8 6s-8-2-8-6z"/><path d="M8 16l-3 3M24 16l3 3"/>',
bat:'<path d="M16 8c-2 0-4 2-5 5-3 1-6 4-6 8 0 2 2 3 4 2 1-2 3-2 5-2s4 0 5 2c2 1 4 0 4-2 0-4-3-7-6-8-1-3-3-5-5-5z"/>',
bear:'<circle cx="16" cy="18" r="8"/><circle cx="11" cy="12" r="3"/><circle cx="21" cy="12" r="3"/>',
wolf:'<path d="M16 6l-4 5v8c0 5 4 8 4 8s4-3 4-8v-8z"/>',
mouse:'<path d="M16 6c-5 0-8 4-8 9 0 4 3 6 5 6s3-1 3-3c0-3 2-5 5-5s5 2 5 5c0 2 1 3 3 3s5-2 5-6c0-5-3-9-8-9z"/>',
elephant:'<path d="M16 5c-5 0-8 4-8 9 0 4 2 6 3 8-1 3 1 5 5 5s6-2 5-5c1-2 3-4 3-8 0-5-3-9-8-9z"/>',
whale:'<path d="M4 16c4-5 10-5 15 0 3 3 5 5 9 4-3-1-5-3-5-7-3-4-5-6-9-6-4 0-7 2-9 7z"/>',
crab:'<path d="M8 16c2-4 6-4 8 0s6 4 8 0"/><path d="M8 16l-4-2M24 16l4-2M8 16l-3 4M24 16l3 4"/>',
squid:'<path d="M16 5c-4 0-7 3-7 7 0 3 2 5 3 7l4 8 4-8c1-2 3-4 3-7 0-4-3-7-7-7z"/>',
octopus:'<path d="M16 5c-4 0-7 3-7 7 0 2 1 4 2 5l2 8 3-7 3 7 2-8c1-1 2-3 2-5 0-4-3-7-7-7z"/>',
spider:'<circle cx="16" cy="16" r="5"/><path d="M11 12l-5-3M21 12l5-3M11 20l-5 3M21 20l5 3M12 9l-3-4M20 9l3-4M12 23l-3 4M20 23l3 4"/>',
scorpion:'<path d="M8 16c3-3 6-3 8 0s5 3 8 0"/><path d="M8 16l-4 2M24 16l4 2M16 16v8"/>',
bee:'<path d="M11 9c-2 0-3 2-3 5v3c0 2 1 3 3 3h10c2 0 3-1 3-3v-3c0-3-1-5-3-5z"/><path d="M10 12h12M10 17h12"/>',
butterfly:'<path d="M16 10c-3 0-6 2-7 5 1 1 3 1 4 0-2 1-3 3-3 5 2-1 4-1 6 0 2-2 2-4 2-6-1-1-3-1-4 0 1-3 2-4 2-5z"/>',
ant:'<path d="M16 6c-2 0-3 2-3 5v6c0 2 1 3 3 3s3-1 3-3v-6c0-3-1-5-3-5z"/><path d="M9 10l-3-2M23 10l3-2M9 22l-3 2M23 22l3 2"/>',
termite:'<path d="M16 5c-2 0-3 2-3 5v8c0 2 1 3 3 3s3-1 3-3v-8c0-3-1-5-3-5z"/><path d="M10 9l-3 1M22 9l3 1"/>',
tick:'<circle cx="16" cy="16" r="6"/><path d="M10 10l-3-3M22 10l3-3M10 22l-3 3M22 22l3 3"/>',
leech:'<path d="M5 16c3-4 6-4 9 0s6 4 9 0"/>',
slug:'<path d="M6 18c2-3 5-3 8 0s6 3 8 0 4-2 4-4c0-3-3-4-6-4-4 0-7 2-8 5z"/>',
snail:'<path d="M8 18c2-3 5-3 8 0s6 3 8 0"/><circle cx="20" cy="14" r="5"/>',
shell:'<path d="M6 18c2-6 8-10 14-10s12 4 14 10c-2 2-6 3-10 3s-8-1-10-3z"/><path d="M16 8v10M11 12l5 4M21 12l-5 4"/>',
clam:'<path d="M5 16c2-7 8-11 13-11s11 4 13 11c-2 2-6 3-10 3s-8-1-10-3z"/>',
pearl:'<circle cx="16" cy="16" r="9"/>',
wheel:'<circle cx="16" cy="16" r="9"/><path d="M16 7v18M7 16h18M10 10l12 12M22 10L10 22"/>',
cart:'<path d="M5 14h18l-3 8H8z"/><circle cx="10" cy="22" r="3"/><circle cx="22" cy="22" r="3"/>',
car:'<path d="M5 16l2-7h18l2 7v7H5z"/><circle cx="10" cy="22" r="2"/><circle cx="22" cy="22" r="2"/>',
motor:'<circle cx="16" cy="16" r="4"/><path d="M16 3v4M16 25v4M3 16h4M25 16h4M7 7l3 3M22 22l3 3M25 7l-3 3M10 22l-3 3"/>',
plane:'<path d="M16 4l3 10 10 2-8 5 2 10-9-6-9 6 2-10-8-5 10-2z"/>',
robot:'<rect x="9" y="7" width="14" height="12" rx="2"/><path d="M16 19v8M12 27h8"/><circle cx="13" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/>',
boiler:'<rect x="8" y="8" width="16" height="16" rx="3"/><path d="M12 8V5h8v3"/>',
engine:'<circle cx="16" cy="16" r="7"/><path d="M16 3v5M16 24v5M3 16h5M24 16h5M7 7l3 3M22 22l3 3M25 7l-3 3M10 22l-3 3"/>',
boat:'<path d="M5 18h22l-4 8H9z"/><path d="M16 18V8l8 4z"/>',
ship:'<path d="M5 20h22l-3 6H8z"/><path d="M16 20V6l10 4z"/>',
train:'<rect x="6" y="9" width="20" height="12" rx="2"/><path d="M6 21l-2 4h20l-2-4M11 9V5h10v4"/>',
bicycle:'<circle cx="9" cy="20" r="5"/><circle cx="23" cy="20" r="5"/><path d="M9 20l7-8h6l-3 8M16 12l-3-4h-3"/>',
motorcycle:'<path d="M5 18h18l-2 5H7z"/><circle cx="9" cy="21" r="3"/><circle cx="21" cy="21" r="3"/>',
tractor:'<path d="M5 18h12l-2 6H5z"/><circle cx="9" cy="22" r="3"/><circle cx="18" cy="22" r="3"/><path d="M17 18V9h5v4"/>',
tool:'<path d="M14 6l8 8-4 4-8-8z"/><path d="M10 10l-4 4 4 4 4-4z"/>',
weapon:'<path d="M16 4l4 8-4 2-4-2z"/><path d="M16 14v14M12 20h8"/>',
electricity:'<path d="M18 4L8 18h7l-3 10 11-16h-7z"/>',
magnet:'<path d="M10 6c-3 0-5 3-5 7s2 6 5 6c2 0 2-2 2-4 0-2-2-2-2-4s0-2 2-2c3 0 5 3 5 7s-2 7-5 7c-3 0-5-3-5-7s2-7 5-7z"/>',
bulb:'<path d="M12 6a6 6 0 0 1 8 0c0 3 2 4 2 7v4h-12v-4c0-3 2-4 2-7z"/><path d="M10 23h12"/>',
light:'<circle cx="16" cy="16" r="5"/><path d="M16 4v3M16 25v3M4 16h3M25 16h3M7 7l2 2M23 23l2 2M25 7l-2 2M9 23l-2 2"/>',
clock:'<circle cx="16" cy="16" r="10"/><path d="M16 8v8l6 4"/>',
watch:'<circle cx="16" cy="16" r="9"/><path d="M16 10v6l4 2"/><path d="M12 4h8M12 28h8"/>',
hourglass:'<path d="M9 5h14l-4 8v12l4-8H9l4-8V5z"/>',
time:'<circle cx="16" cy="16" r="10"/><path d="M16 8v8l6 4"/>',
book:'<path d="M8 5h11a3 3 0 0 1 3 3v18H8z"/><path d="M8 5v18"/>',
library:'<path d="M6 6h5v18H6zM12 6h5v18h-5zM18 6h5v18h-5z"/>',
pen:'<path d="M22 4l8 8-14 14H8v-8z"/>',
paper:'<path d="M9 4h14v24H9z"/><path d="M13 9h8M13 14h8M13 19h5"/>',
coin:'<circle cx="16" cy="16" r="10"/><circle cx="16" cy="16" r="5"/>',
money:'<rect x="5" y="8" width="22" height="16" rx="2"/><circle cx="16" cy="16" r="4"/>',
gold:'<circle cx="16" cy="16" r="10"/><path d="M16 9v14M9 16h14"/>',
silver:'<circle cx="16" cy="16" r="10"/><path d="M16 9v14M9 16h14"/>',
diamond:'<path d="M16 4l10 8-10 16L6 12z"/>',
gem:'<path d="M16 4l10 8-10 16L6 12z"/>',
crystal:'<path d="M16 4l8 8-8 16-8-16z"/>',
idea:'<path d="M12 6a6 6 0 0 1 8 0c0 3 2 4 2 7v4h-12v-4c0-3 2-4 2-7z"/><path d="M10 23h12"/>',
sound:'<path d="M6 12c4-3 8-3 12 0s8 3 12 0M6 18c4-3 8-3 12 0s8 3 12 0"/>',
music:'<path d="M11 22V8l12-2v12"/><circle cx="7" cy="22" r="3"/><circle cx="19" cy="20" r="3"/>',
love:'<path d="M16 24S5 16 5 10a5 5 0 0 1 11-2 5 5 0 0 1 11 2c0 6-11 14-11 14z"/>',
life:'<path d="M12 5c-3 3-3 8 0 12 3-4 3-9 0-12zM20 5c-3 3-3 8 0 12 3-4 3-9 0-12z"/><path d="M16 11v10"/>',
death:'<path d="M16 5a6 6 0 0 0-6 6c0 3 2 4 3 7h6c1-3 3-4 3-7a6 6 0 0 0-6-6z"/><path d="M13 21h6"/>',
ghost:'<path d="M9 5h14v16l-3 4-3-4-3 4-3-4-3 4z"/><circle cx="13" cy="13" r="1.5"/><circle cx="19" cy="13" r="1.5"/>',
space:'<path d="M16 4c3 3 5 7 5 11 0 4-2 7-5 9-3-2-5-5-5-9 0-4 2-8 5-11z"/><path d="M16 4v22"/>',
star:'<path d="M16 4l3 8 8 1-6 5 2 8-7-4-7 4 2-8-6-5 8-1z"/>',
moon:'<path d="M20 5a10 10 0 1 0 7 14 8 8 0 0 1-7-14z"/>',
sun:'<circle cx="16" cy="16" r="6"/><path d="M16 3v4M16 25v4M3 16h4M25 16h4M7 7l2 2M23 23l2 2M25 7l-2 2M9 23l-2 2"/>',
planet:'<circle cx="16" cy="16" r="8"/><path d="M5 10c4 2 18 2 22 0M4 18c5 1 19 1 24 0"/>',
city:'<path d="M6 24V12h5v12zM13 24V8h6v16zM21 24V14h5v10z"/>',
country:'<path d="M6 22c2-5 5-7 10-7s8 2 10 7c-2 2-5 3-10 3s-8-1-10-3z"/><path d="M10 22v-4h4v4M18 22v-5h4v5"/>',
house:'<path d="M5 14l11-8 11 8v10H5z"/><path d="M13 24v-7h6v7"/>',
hospital:'<path d="M8 4h16v24H8z"/><path d="M16 10v12M10 16h12"/>'
};
const SYMBOL_KEYS = {
w:'water', f:'fire', e:'earth', a:'air', steam:'steam', stone:'earth', moss:'plant', energy:'fire',
water:'water', fire:'fire', earth:'earth', air:'air', rain:'rain', snow:'snow', ice:'ice', cloud:'cloud',
sea:'sea', ocean:'sea', lake:'sea', river:'sea', wave:'wave', swamp:'swamp', mountain:'mountain', sand:'sand', dust:'dust',
tree:'tree', grass:'plant', flower:'flower', seed:'seed', plant:'plant', forest:'tree', bush:'plant', vine:'plant', reed:'plant', cane:'plant', moss:'plant', algae:'plant', mushroom:'plant', fern:'plant',
bird:'bird', fish:'fish', beast:'beast', insect:'insect', beetle:'beetle', worm:'worm', snake:'snake', lizard:'lizard', turtle:'turtle', frog:'frog', bat:'bat', bear:'bear', wolf:'wolf', mouse:'mouse', elephant:'elephant', whale:'whale', crab:'crab', squid:'squid', octopus:'octopus', spider:'spider', scorpion:'scorpion', bee:'bee', butterfly:'butterfly', ant:'ant', termite:'termite', tick:'tick', leech:'leech', slug:'slug', snail:'snail', shell:'shell', clam:'clam', pearl:'pearl',
wheel:'wheel', cart:'cart', car:'car', motor:'motor', plane:'plane', robot:'robot', steam_boiler:'boiler', steam_engine:'engine', steamboat:'ship', bicycle:'bicycle', motorcycle:'motorcycle', tractor:'tractor', tool:'tool', metal:'metal', weapon:'weapon', electricity:'electricity', magnet:'magnet', bulb:'bulb', light:'light', clock:'clock', watch:'watch', hourglass:'hourglass', time:'time',
book:'book', library:'library', paper:'paper', pen:'pen', coin:'coin', money:'money', gold:'gold', silver:'silver', diamond:'diamond', gem:'gem', crystal:'crystal', idea:'idea', sound:'sound', music:'music', love:'love', life:'life', death:'death', ghost:'ghost', space:'space', star:'star', moon:'moon', sun:'sun', planet:'planet', city:'city', country:'country', house:'house', hospital:'hospital',
alcohol:'alcohol', beer:'alcohol', wine:'alcohol', vodka:'alcohol', milk:'milk', cheese:'cheese', bread:'bread', meat:'meat', egg:'egg', fruit:'fruit', vegetable:'vegetable', medicine:'medicine', poison:'poison', acid:'acid', salt:'salt', mineral:'mineral', lava:'lava'
};
const state = {opened:[...basics], found:[], attempts:[], tried:new Map(), failedNow:null, reacting:false, favorites:new Set(), recent:[], slots:[null,null], target:null, nextAfterMix:null, filter:'all', category:'all', sort:'default', search:'', hideDead:false, page:0, view:'', viewPage:0, detail:null, sound:false, effect:true, lastNew:null};
function cat(id) { if (basics.includes(id)) return 'basic'; for (const c of ['nature','animals','machines']) if (categories[c].has(id)) return c; return 'other'; }
function hash(id) { let h=0; for(const c of id) h=(h*31+c.charCodeAt(0))>>>0; return h; }
function abstractGlyph(id) {
 const h=hash(id), n=3+h%7, pts=[];
 for(let i=0;i<n;i++){const a=i*2*Math.PI/n-Math.PI/2; pts.push(`${(16+9*Math.cos(a)).toFixed(1)},${(16+9*Math.sin(a)).toFixed(1)}`);}
 return `<path d="M${pts.join('L')}Z"/>`+(h%2?'<circle cx="16" cy="16" r="3"/>':'');
}
function glyphKey(id) {
 if(SYMBOL_KEYS[id]) return SYMBOL_KEYS[id];
 const text=label(id).toLowerCase();
 if(/вода|море|озеро|река|океан|дожд|пар|лед|лёд|снег|холод|волн/.test(text)) return 'water';
 if(/огонь|огонь|лава|солнц|свет|энерг|взрыв|огонь/.test(text)) return 'fire';
 if(/земл|камен|гора|песок|пыль|гряз|земля|угл|минера|метал|соль|селитра/.test(text)) return 'earth';
 if(/воздух|ветер|облак|небо|шторм|давление|газ|озон|кислород/.test(text)) return 'air';
 if(/трава|дерево|лист|цвет|семя|лес|куст|лоза|тростник|мох|водорос|гриб|папоротник/.test(text)) return 'plant';
 if(/птица|рыба|звер|насеком|жук|черв|зме|ящер|черепа|лягуш|летуч|медвед|волк|мыш|слон|кит|краб|осьминог|кальмар|паук|скорпи|пчел|бабоч|мурав|термит|клещ|пияв|слизень|улитк|ракуш|моллюск|жемчуг/.test(text)) return 'beast';
 if(/колесо|телег|машин|мотор|самолет|робот|котел|двигатель|пароход|велосипед|мотоцикл|трактор|инструмент|оруж|электриче|магнит|лампоч|часы|время|книг|бумаг|ручк|моне|деньг|золот|серебр|алмаз|драг|кристалл|иде|звук|музык|любов|жизн|смерт|призрак|косм|звез|лун|планет|город|страна|дом|больниц/.test(text)) return 'machine';
 if(/спирт|пив|вин|водк|молок|сыр|хлеб|мяс|яйц|фрукт|овощ|лекарств|яд|кислот|медицин/.test(text)) return 'food';
 return null;
}
function symbolMarkup(id) {
 const key=glyphKey(id);
 return key && GLYPH[key] ? GLYPH[key] : abstractGlyph(id);
}
function blendRecipe(recipes, seed) {
 const profiles=new Map(seed);
 const sum=new Map(), count=new Map();
 for(const r of recipes){
  const out=r.result;if(!profiles.has(out))continue;
  const ins=(r.ingredients||[]).filter(x=>profiles.has(x));if(!ins.length)continue;
  const avg=ins.reduce((a,x)=>{const p=profiles.get(x);return a.map((v,i)=>v+p[i]);},Array(seed.get(out).length).fill(0)).map(v=>v/ins.length);
  const prev=sum.get(out)||Array(avg.length).fill(0);
  sum.set(out,prev.map((v,i)=>v+avg[i]));
  count.set(out,(count.get(out)||0)+1);
 }
 for(const [out,s] of sum){const n=count.get(out)||1;sum.set(out,s.map(v=>v/n));}
 for(const [id,base] of profiles){
  const r=sum.get(id);
  if(r)profiles.set(id,base.map((v,i)=>v*.7+r[i]*.3));
 }
 for(const [id,p] of profiles){const total=p.reduce((a,b)=>a+b,0)||1;profiles.set(id,p.map(v=>v/total));}
 return profiles;
}
function elementComposition() {
 const direct={w:[1,0,0,0],f:[0,1,0,0],e:[0,0,1,0],a:[0,0,0,1]};
 function heuristic(id) {
  if(direct[id])return [...direct[id]];
  const key=glyphKey(id);
  if(key==='water'||key==='rain'||key==='snow'||key==='ice'||key==='sea'||key==='wave'||key==='cloud'||key==='steam'||key==='swamp')return [1,0,0,0];
  if(key==='fire'||key==='energy'||key==='lava'||key==='light'||key==='sun')return [0,1,0,0];
  if(key==='earth'||key==='stone'||key==='mountain'||key==='sand'||key==='dust'||key==='metal'||key==='mineral'||key==='salt')return [0,0,1,0];
  if(key==='air')return [0,0,0,1];
  if(key==='plant'||key==='tree'||key==='flower'||key==='seed'||key==='grass'||key==='moss'||key==='forest')return [.15,0,.6,.25];
  if(key==='bird'||key==='fish'||key==='beast'||key==='insect'||key==='beetle'||key==='worm'||key==='snake'||key==='lizard'||key==='turtle'||key==='frog'||key==='bat'||key==='bear'||key==='wolf'||key==='mouse'||key==='elephant'||key==='whale'||key==='crab'||key==='squid'||key==='octopus'||key==='spider'||key==='scorpion'||key==='bee'||key==='butterfly'||key==='ant'||key==='termite'||key==='tick'||key==='leech'||key==='slug'||key==='snail'||key==='shell'||key==='clam'||key==='pearl')return [.1,0,.6,.3];
  if(key==='wheel'||key==='cart'||key==='car'||key==='motor'||key==='plane'||key==='robot'||key==='boiler'||key==='engine'||key==='ship'||key==='bicycle'||key==='motorcycle'||key==='tractor'||key==='tool'||key==='weapon'||key==='electricity'||key==='magnet'||key==='bulb'||key==='clock'||key==='watch'||key==='hourglass'||key==='time'||key==='book'||key==='library'||key==='paper'||key==='pen'||key==='coin'||key==='money'||key==='gold'||key==='silver'||key==='diamond'||key==='gem'||key==='crystal'||key==='idea'||key==='sound'||key==='music'||key==='love'||key==='life'||key==='death'||key==='ghost'||key==='space'||key==='star'||key==='moon'||key==='planet'||key==='city'||key==='country'||key==='house'||key==='hospital')return [.1,.25,.55,.1];
  if(key==='alcohol'||key==='beer'||key==='wine'||key==='vodka'||key==='milk'||key==='cheese'||key==='bread'||key==='meat'||key==='egg'||key==='fruit'||key==='vegetable'||key==='medicine'||key==='poison'||key==='acid')return [.25,.05,.7,0];
  return [.25,.25,.25,.25];
 }
 return blendRecipe(recipes,new Map(Object.keys(names).map(id=>[id,heuristic(id)])));
}
function stateComposition() {
 const direct={w:[0,0,1,0,0,0],f:[1,0,0,0,0,0],e:[0,0,0,0,1,0],a:[0,1,0,0,0,0]};
 function heuristic(id) {
  if(direct[id])return [...direct[id]];
  const key=glyphKey(id);
  if(key==='fire'||key==='energy'||key==='lava'||key==='light'||key==='sun')return [1,0,0,0,0,0];
  if(key==='air'||key==='cloud'||key==='steam'||key==='wind'||key==='pressure')return [0,1,0,0,0,0];
  if(key==='water'||key==='rain'||key==='snow'||key==='ice'||key==='sea'||key==='wave'||key==='swamp')return [0,0,1,0,0,0];
  if(key==='sand'||key==='dust'||key==='soil')return [0,0,0,1,0,0];
  if(key==='earth'||key==='stone'||key==='mountain'||key==='metal'||key==='mineral'||key==='salt')return [0,0,0,0,1,0];
  if(key==='plant'||key==='tree'||key==='flower'||key==='seed'||key==='grass'||key==='moss'||key==='forest')return [0,0,0,0,0,1];
  if(key==='bird'||key==='fish'||key==='beast'||key==='insect'||key==='beetle'||key==='worm'||key==='snake'||key==='lizard'||key==='turtle'||key==='frog'||key==='bat'||key==='bear'||key==='wolf'||key==='mouse'||key==='elephant'||key==='whale'||key==='crab'||key==='squid'||key==='octopus'||key==='spider'||key==='scorpion'||key==='bee'||key==='butterfly'||key==='ant'||key==='termite'||key==='tick'||key==='leech'||key==='slug'||key==='snail'||key==='shell'||key==='clam'||key==='pearl')return [0,0,0,0,0,1];
  if(key==='wheel'||key==='cart'||key==='car'||key==='motor'||key==='plane'||key==='robot'||key==='boiler'||key==='engine'||key==='ship'||key==='bicycle'||key==='motorcycle'||key==='tractor'||key==='tool'||key==='weapon'||key==='electricity'||key==='magnet'||key==='bulb'||key==='clock'||key==='watch'||key==='hourglass'||key==='time'||key==='book'||key==='library'||key==='paper'||key==='pen'||key==='coin'||key==='money'||key==='gold'||key==='silver'||key==='diamond'||key==='gem'||key==='crystal'||key==='idea'||key==='sound'||key==='music'||key==='love'||key==='life'||key==='death'||key==='ghost'||key==='space'||key==='star'||key==='moon'||key==='planet'||key==='city'||key==='country'||key==='house'||key==='hospital')return [0,0,0,0,1,0];
  if(key==='alcohol'||key==='beer'||key==='wine'||key==='vodka'||key==='milk'||key==='cheese'||key==='bread'||key==='meat'||key==='egg'||key==='fruit'||key==='vegetable'||key==='medicine'||key==='poison'||key==='acid')return [0,0,0,0,0,1];
  return [.12,.12,.12,.12,.26,.26];
 }
 return blendRecipe(recipes,new Map(Object.keys(names).map(id=>[id,heuristic(id)])));
}
const ELEMENT_COMPOSITION=elementComposition();
const STATE_COMPOSITION=stateComposition();
function elemental(id){return ELEMENT_COMPOSITION.get(id)||[.25,.25,.25,.25];}
function stateProfile(id){return STATE_COMPOSITION.get(id)||[0,0,0,0,0,0];}
function rgb(hex){const n=parseInt(hex.slice(1),16);return [n>>16&255,n>>8&255,n&255];}
function blend(colors,weights){const out=[0,0,0];for(let i=0;i<3;i++){let s=0;for(let j=0;j<colors.length;j++)s+=colors[j][i]*weights[j];out[i]=Math.round(s);}return out;}
function hex(rgb){return '#'+rgb.map(v=>Math.max(0,Math.min(255,Math.round(v))).toString(16).padStart(2,'0')).join('');}
const ELEMENT_TINTS={water:'#4db8ff',fire:'#ff5252',earth:'#a9743f',air:'#d8f0ff'};
const STATE_ORDER=['fire','gas','liquid','sand','solid','organic'];
function orbColor(id){
 const w=elemental(id);
 const t=blend(Object.values(ELEMENT_TINTS).map(rgb),w);
 const pale=blend([t,[255,255,255]],[.68,.32]);
 const mid=blend([t,[41,35,60]],[.55,.45]);
 return `radial-gradient(circle at 36% 23%, ${hex(pale)} 0%, ${hex(mid)} 58%, #29233c 82%)`;
}
function dominantState(id){
 const w=stateProfile(id);
 let best=0;
 for(let i=1;i<w.length;i++)if(w[i]>w[best])best=i;
 return STATE_ORDER[best];
}
function clearOrb(el){
 if(!el)return;
 el.style.background='';
 el.style.backgroundImage='';
 el.style.backgroundColor='';
 el.style.backgroundBlendMode='';
 for(const k of STATE_ORDER)el.classList.remove('state-'+k);
 el.classList.remove('fx');
}
function applyOrb(el,id){
 if(!el)return;
 clearOrb(el);
 el.style.background=orbColor(id);
 if(state.effect){
  el.classList.add('fx','state-'+dominantState(id));
 }
}
function icon(id) {
 const span=document.createElement('span');
 span.className='symbol';
 const raw=names[id] || id;
 const emoji=raw.match(/^[\p{Extended_Pictographic}\p{Regional_Indicator}\p{Emoji_Modifier}\uFE0F\u200D]+/u);
 span.textContent=emoji ? emoji[0] : label(id).slice(0,1);
 return span;
}
function button(text, cls, handler) { const el=document.createElement('button'); el.type='button'; el.className=cls; el.textContent=text; el.addEventListener('click',handler); return el; }
let labelId=0;
function curvedLabel(id) {
 const ns='http://www.w3.org/2000/svg', svg=document.createElementNS(ns,'svg');
 svg.setAttribute('viewBox','0 0 100 100');svg.setAttribute('aria-hidden','true');svg.classList.add('orb-curve');
 const chars=Array.from(label(id).slice(0,14)), count=chars.length||1;
 const p0=[11,72], p1=[50,81], p2=[89,72];
 // Approximate each glyph width, then space letters along the arc so they cannot crowd.
 const widths=chars.map(ch=>{
  if(/\s/.test(ch))return 0.3;
  const code=ch.codePointAt(0), wide=/[А-ЯЁА-ЯЁ]/.test(ch)||code>=0x400&&code<=0x4FF;
  return wide?1.1:0.72;
 });
 const total=widths.reduce((a,b)=>a+b,0)||1;
 const maxWidth=66, fontSize=Math.max(7,Math.min(12,maxWidth/total));
 const arcLen=78.7, margin=3, usable=arcLen-margin*2;
 const scale=Math.min(1,usable/(total*fontSize));
 const stepFont=fontSize*scale; const start=Math.max(0,(usable-total*stepFont)/2); let pos=start; const positions=[];
 for(let i=0;i<count;i++){positions.push(pos+widths[i]*stepFont/2);pos+=widths[i]*stepFont;}
 const samples=80, cum=[0];
 for(let i=1;i<=samples;i++){
  const t=i/samples, x=(1-t)*(1-t)*p0[0]+2*(1-t)*t*p1[0]+t*t*p2[0], y=(1-t)*(1-t)*p0[1]+2*(1-t)*t*p1[1]+t*t*p2[1];
  const px=(1-(i-1)/samples)*(1-(i-1)/samples)*p0[0]+2*(1-(i-1)/samples)*((i-1)/samples)*p1[0]+((i-1)/samples)**2*p2[0];
  const py=(1-(i-1)/samples)*(1-(i-1)/samples)*p0[1]+2*(1-(i-1)/samples)*((i-1)/samples)*p1[1]+((i-1)/samples)**2*p2[1];
  cum.push(cum[i-1]+Math.hypot(x-px,y-py));
 }
 const atLength=len=>{
  for(let i=1;i<cum.length;i++)if(len<=cum[i]){
   const frac=i===0?0:(len-cum[i-1])/(cum[i]-cum[i-1]||1), t=(i-1+frac)/samples;
   return [(1-t)*(1-t)*p0[0]+2*(1-t)*t*p1[0]+t*t*p2[0],(1-t)*(1-t)*p0[1]+2*(1-t)*t*p1[1]+t*t*p2[1],
    2*((1-t)*(p1[0]-p0[0])+t*(p2[0]-p1[0])),2*((1-t)*(p1[1]-p0[1])+t*(p2[1]-p1[1]))];
  }
  return [p2[0],p2[1],p2[0]-p1[0],p2[1]-p1[1]];
 };
 for(let i=0;i<count;i++){
  const [x,y,dx,dy]=atLength(positions[i]*arcLen/usable);
  const text=document.createElementNS(ns,'text');
  text.setAttribute('x',x.toFixed(2));text.setAttribute('y',(y+stepFont*.32).toFixed(2));
  text.setAttribute('text-anchor','middle');text.setAttribute('font-size',stepFont.toFixed(2));
  text.setAttribute('dominant-baseline','central');
  text.setAttribute('transform',`rotate(${(Math.atan2(dy,dx)*180/Math.PI).toFixed(2)} ${x.toFixed(2)} ${y.toFixed(2)})`);
  text.textContent=chars[i];svg.append(text);
 }
 return svg;
}

function selectedItems() {
 let result=state.opened.filter(id => {
  if (state.filter==='favorites' && !state.favorites.has(id)) return false;
  if (state.filter==='recent' && !state.recent.includes(id)) return false;
  if (state.filter==='dead' && usedIngredients.has(id)) return false;
  if (state.hideDead && !usedIngredients.has(id)) return false;
  return (state.category==='all' || cat(id)===state.category) && label(id).toLocaleLowerCase('ru').includes(state.search.toLocaleLowerCase('ru'));
 });
 if (state.sort==='az') result.sort((a,b) => label(a).localeCompare(label(b),'ru'));
 else if (state.sort==='recent' || state.filter==='recent') result.sort((a,b) => {const ra=state.recent.indexOf(a), rb=state.recent.indexOf(b); return (ra<0?999:ra)-(rb<0?999:rb);});
 else result.sort((a,b) => (state.favorites.has(b)?1:0)-(state.favorites.has(a)?1:0));
 return result;
}
function renderElements() {
 stopFlight();
 const all=selectedItems(), pages=Math.max(1,Math.ceil(all.length/PAGE_SIZE)); state.page=Math.min(state.page,pages-1);
 $('#open-count').textContent=String(state.opened.length).padStart(2,'0');
 $('#page-label').textContent=`${state.page+1} / ${pages}`; $('#prev').disabled=state.page===0; $('#next').disabled=state.page===pages-1;
 document.querySelectorAll('#tabs button').forEach(b=>{b.classList.toggle('active',b.dataset.filter===state.filter);b.setAttribute('aria-pressed',String(b.dataset.filter===state.filter));});
 const grid=$('#elements');grid.replaceChildren();
 for(const id of all.slice(state.page*PAGE_SIZE,state.page*PAGE_SIZE+PAGE_SIZE)) {
  const wrap=document.createElement('div');wrap.className='token-wrap';
  const el=document.createElement('button');el.type='button';el.className='element'+(!usedIngredients.has(id)?' dead':'');el.dataset.id=id;el.title=label(id);el.setAttribute('aria-label',label(id)+(usedIngredients.has(id)?'':', тупиковый элемент')+', выбрать или перетащить');
  applyOrb(el,id);el.append(icon(id),curvedLabel(id));
  const fav=button(state.favorites.has(id)?'★':'☆','favorite'+(state.favorites.has(id)?' on':''),()=>{if(state.favorites.has(id))state.favorites.delete(id);else state.favorites.add(id);renderElements();});fav.setAttribute('aria-label',(state.favorites.has(id)?'Убрать из избранного: ':'В избранное: ')+label(id));
  wrap.append(el,fav);grid.append(wrap);
 }
 if(!all.length){const empty=document.createElement('div');empty.className='empty';empty.textContent='Здесь пока ничего нет. Попробуй другой фильтр.';grid.append(empty);}
}
function renderCenter(){
 const result=$('#mix');result.replaceChildren();
 const ids=state.slots, count=ids.filter(Boolean).length;
 let kind='empty', accessible='Выбери элементы', lines=[];
 if(state.reacting){kind='reacting';accessible='Идёт реакция';lines=['РЕАКЦИЯ','✦'];}
 else if(count===1){kind='waiting';accessible=ids[0]?'Добавь второй элемент':'Добавь первый элемент';lines=['ДОБАВЬ',ids[0]?'ВТОРОЙ':'ПЕРВЫЙ'];}
 else if(count===2){
  const key=pairKey(ids);
  if(state.tried.has(key)){
   const outcome=state.tried.get(key);
   if(outcome){kind='known';accessible='Было: '+label(outcome);lines=['БЫЛО',label(outcome)];}
   else {kind=state.failedNow===key?'clear':'known-fail';accessible='Нет реакции. Очистить выбранные элементы';lines=kind==='clear'?['НЕТ','РЕАКЦИИ','ОЧИСТИТЬ']:['БЫЛО','НЕТ РЕАКЦИИ','ОЧИСТИТЬ'];}
  } else {kind='mix';accessible='Смешать элементы';lines=['✦','СМЕШАТЬ'];}
 }
 result.dataset.state=kind;result.disabled=!['mix','clear','known-fail'].includes(kind);
 result.setAttribute('aria-label',accessible);result.title=accessible;
 for(const [i,line] of lines.entries()){const el=document.createElement(i===0?'span':'small');el.className=i===0?'result-primary':'result-caption';el.textContent=line;result.append(el);}
}
function renderSlots() {
 document.querySelectorAll('[data-slot]').forEach(el=>{
  const i=Number(el.dataset.slot),id=state.slots[i];el.replaceChildren();
  if(id){applyOrb(el,id);el.append(icon(id),curvedLabel(id));}else{clearOrb(el);el.textContent='+';}
  el.disabled=state.reacting;
  el.classList.toggle('active',state.target===i || (state.target===null && state.nextAfterMix===i));
  el.setAttribute('aria-label',id?`${label(id)}. Нажми для замены`:`Пустой слот ${i+1}`);el.title=id?label(id)+' — нажми для замены':'Пустой слот';
 });
 renderCenter();
}
function remember(id){state.recent=[id,...state.recent.filter(x=>x!==id)].slice(0,24);}
function choose(id, target=null){
 if(state.reacting || !state.opened.includes(id))return;
 state.failedNow=null;
 const explicit=target!==null || state.target!==null;
 let i=target ?? state.target ?? state.nextAfterMix;
 if(i===null)i=state.slots.indexOf(null);if(i<0)i=1;
 state.slots[i]=id;state.target=null;
 // After a mix replace slot 1, then slot 2; explicit slot selection overrides it.
 state.nextAfterMix=explicit?null:(state.nextAfterMix===0?1:null);
 remember(id);state.lastNew=null;
 $('#mix-message').textContent='Выбран: '+label(id)+(state.nextAfterMix===1?' · теперь второй':'');
 renderSlots();renderElements();
}
function tone(success){if(!state.sound)return;try{const C=window.AudioContext||window.webkitAudioContext;const c=new C(),o=c.createOscillator(),g=c.createGain();o.type='sine';o.frequency.setValueAtTime(success?530:240,c.currentTime);o.frequency.exponentialRampToValueAtTime(success?800:160,c.currentTime+.2);g.gain.setValueAtTime(.035,c.currentTime);g.gain.exponentialRampToValueAtTime(.001,c.currentTime+.25);o.connect(g).connect(c.destination);o.start();o.stop(c.currentTime+.26);o.onended=()=>c.close();}catch(_) {}}
let flying=null;
function resetResult(){
 const result=$('#mix');result.classList.remove('flying-origin');renderCenter();
}
function stopFlight(){
 if(!flying)return;
 flying.animation.cancel();flying.target?.classList.remove('in-flight');flying.target?.closest('.token-wrap')?.classList.remove('receiving');
 flying.orb.remove();resetResult();flying=null;
}
function flyToCollection(id){
 stopFlight();
 const reduced=window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
 const target=[...document.querySelectorAll('#elements .element')].find(el=>el.dataset.id===id);
 const grid=$('#elements'),result=$('#mix');
 if(reduced || typeof result.animate!=='function')return;
 const area=grid.getBoundingClientRect(),sample=grid.querySelector('.element')?.getBoundingClientRect();
 const size=sample?.width || 55;
 const dest=target ? target.getBoundingClientRect() : {left:area.left+area.width/2-size/2,top:area.top+area.height/2-size/2,width:size,height:size};
 const start=result.getBoundingClientRect();if(!dest.width || !start.width)return;
 const orb=document.createElement('div');orb.className='result-slot flying-result flight-clone';orb.append(icon(id),curvedLabel(id));
 orb.style.left=start.left+'px';orb.style.top=start.top+'px';orb.style.width=start.width+'px';orb.style.height=start.height+'px';document.body.append(orb);
 result.classList.add('flying-origin');target?.classList.add('in-flight');target?.closest('.token-wrap')?.classList.add('receiving');
 const dx=dest.left+dest.width/2-start.left-start.width/2,dy=dest.top+dest.height/2-start.top-start.height/2;
 const scale=dest.width/start.width;
 const anim=orb.animate([
  {transform:'translate(0,0) scale(1)',opacity:1,offset:0},
  {transform:'translate(0,0) scale(1.1)',opacity:1,offset:.18},
  {transform:`translate(${dx*.45}px,${Math.min(0,dy*.3)-25}px) scale(${(1+scale)/2})`,opacity:1,offset:.55},
  {transform:`translate(${dx}px,${dy}px) scale(${scale})`,opacity:target?1:0,offset:1}
 ],{duration:1150,easing:'ease-in-out',fill:'forwards'});
 flying={animation:anim,target,orb};
 anim.onfinish=()=>{
  target?.classList.remove('in-flight');target?.closest('.token-wrap')?.classList.remove('receiving');target?.classList.add('landed');
  if(flying?.animation===anim){anim.cancel();orb.remove();flying=null;resetResult();}
 };
}
function clearMix(){
 state.slots=[null,null];state.target=null;state.nextAfterMix=null;state.failedNow=null;
 $('#mix-message').textContent='Выбери или перетащи два элемента';renderSlots();
}
function mix(){
 if(state.reacting)return;
 const action=$('#mix').dataset.state;
 if(action==='clear'||action==='known-fail'){clearMix();return;}
 if(action!=='mix')return;
 const ids=[...state.slots], key=pairKey(ids), r=byPair.get(key);
 state.reacting=true;state.failedNow=null;
 const hero=$('#hero');hero.classList.remove('success','fail','reacting');void hero.offsetWidth;hero.classList.add('reacting');
 $('#mix-message').textContent='Котёл творит магию…';renderSlots();
 const resolve=()=>{
  state.reacting=false;hero.classList.remove('reacting');
  state.attempts.unshift({key,ids,result:r?.result||null});state.attempts=state.attempts.slice(0,100);state.tried.set(key,r?.result||null);
  if(r){
   const fresh=!state.found.some(x=>x.key===key),newElement=!state.opened.includes(r.result);
   if(fresh)state.found.push({key,ids,result:r.result});
   if(newElement){
    state.opened.push(r.result);remember(r.result);state.lastNew=r.result;state.filter='all';state.category='all';state.search='';
    $('#search').value='';$('#category').value='all';const index=selectedItems().indexOf(r.result);state.page=index<0?0:Math.floor(index/PAGE_SIZE);
   }
   state.target=null;state.nextAfterMix=0;
   $('#mix-message').textContent=newElement?'Открыт элемент: '+label(r.result):fresh?'Новый рецепт: '+label(r.result):'Уже открыто: '+label(r.result);
   hero.classList.add('success');tone(true);
  } else {
   state.failedNow=key;state.target=null;state.nextAfterMix=null;
   $('#mix-message').textContent='Нет реакции — очисти или замени элемент';hero.classList.add('fail');tone(false);
  }
  renderSlots();renderElements();
  if(r)flyToCollection(r.result);
 };
 if(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)resolve();else window.setTimeout(resolve,700);
}
function go(view){stopFlight();state.view=view;state.viewPage=0;state.detail=null;$('#workspace').hidden=true;$('#overlay').hidden=false;renderOverlay();}
function goBack(){state.view='';state.detail=null;$('#overlay').hidden=true;$('#workspace').hidden=false;}
function renderOverlay(){
 $('#overlay-kicker').textContent=state.view==='album'?'ТВОЯ КОЛЛЕКЦИЯ':'ЛАБОРАТОРНЫЙ ЖУРНАЛ';$('#overlay-title').textContent=state.view==='album'?'Альбом открытий':'История попыток';
 const body=$('#overlay-body');body.replaceChildren();const items=state.view==='album'?[...state.found].reverse():state.attempts;const size=state.view==='album'?6:HISTORY_SIZE,pages=Math.max(1,Math.ceil(items.length/size));state.viewPage=Math.min(state.viewPage,pages-1);
 $('#overlay-prev').disabled=state.viewPage===0;$('#overlay-next').disabled=state.viewPage===pages-1;$('#overlay-page').textContent=`${state.viewPage+1} / ${pages}`;$('.overlay-bottom').hidden=!!state.detail;
 if(state.detail){const r=state.detail,card=document.createElement('div');card.className='detail-card';const im=document.createElement('div');im.className='large';im.append(icon(r.result));const h=document.createElement('h3');h.textContent=label(r.result);const p=document.createElement('p');p.textContent=r.ids.map(label).join('  +  ');card.append(im,h,p,button('В котёл ✦','',()=>{state.slots=[...r.ids];state.target=null;state.nextAfterMix=null;goBack();renderSlots();$('#mix-message').textContent='Известное сочетание в котле';}),button('← В альбом','',()=>{state.detail=null;renderOverlay();}));body.append(card);return;}
 if(!items.length){const empty=document.createElement('div');empty.className='empty';empty.textContent=state.view==='album'?'Пока пусто. Найди первое сочетание у котла ✨':'Здесь появятся твои попытки смешивания.';body.append(empty);return;}
 const list=document.createElement('div');list.className=state.view==='album'?'album-grid':'history-list';
 for(const r of items.slice(state.viewPage*size,state.viewPage*size+size)){
  if(state.view==='album'){const el=button('','recipe',()=>{state.detail=r;renderOverlay();});const med=document.createElement('span');med.className='medallion';med.append(icon(r.result));const info=document.createElement('span');info.className='recipe-text';const name=document.createElement('strong');name.textContent=label(r.result);const pair=document.createElement('small');pair.textContent=r.ids.map(label).join(' + ');info.append(name,pair);el.append(med,info);el.setAttribute('aria-label',label(r.result)+': '+r.ids.map(label).join(' плюс '));list.append(el);}
  else {const row=document.createElement('div');row.className='history-item';const pair=document.createElement('span');pair.textContent=r.ids.map(label).join(' + ');const result=document.createElement('strong');result.textContent=r.result?'→ '+label(r.result):'нет реакции';row.append(pair,result);list.append(row);}
 }body.append(list);
}
$('#elements').addEventListener('click',e=>{const el=e.target.closest('.element');if(el&&!suppress)choose(el.dataset.id);});
let drag=null,suppress=false;
$('#elements').addEventListener('pointerdown',e=>{const el=e.target.closest('.element');if(!el||e.button!==0)return;drag={id:el.dataset.id,x:e.clientX,y:e.clientY,moved:false,ghost:null};el.setPointerCapture?.(e.pointerId);});
$('#elements').addEventListener('pointermove',e=>{if(!drag)return;if(!drag.moved&&Math.hypot(e.clientX-drag.x,e.clientY-drag.y)>8){drag.moved=true;drag.ghost=document.createElement('div');drag.ghost.className='drag-ghost';drag.ghost.append(icon(drag.id));document.body.append(drag.ghost);}if(drag.ghost){drag.ghost.style.left=(e.clientX-14)+'px';drag.ghost.style.top=(e.clientY-14)+'px';}});
function endDrag(e,cancel){if(!drag)return;const d=drag;drag=null;if(d.ghost)d.ghost.remove();if(!d.moved)return;suppress=true;setTimeout(()=>suppress=false,100);const under=document.elementFromPoint(e.clientX,e.clientY);if(!cancel&&under?.closest('#hero')){const slot=under.closest('[data-slot]');choose(d.id,slot?Number(slot.dataset.slot):null);}}
$('#elements').addEventListener('pointerup',e=>endDrag(e,false));$('#elements').addEventListener('pointercancel',e=>endDrag(e,true));
document.querySelectorAll('[data-slot]').forEach(el=>el.addEventListener('click',()=>{if(state.reacting)return;const i=Number(el.dataset.slot);state.nextAfterMix=null;state.target=i;$('#mix-message').textContent=state.slots[i]?'Выбери элемент для замены':'Выбери элемент для этого слота';renderSlots();}));
$('#mix').addEventListener('click',mix);$('#prev').onclick=()=>{state.page--;renderElements();};$('#next').onclick=()=>{state.page++;renderElements();};
$('#tabs').addEventListener('click',e=>{const b=e.target.closest('[data-filter]');if(!b)return;state.filter=b.dataset.filter;state.page=0;renderElements();});
$('#search-toggle').onclick=()=>{const box=$('#search-box');box.hidden=!box.hidden;$('#search-toggle').setAttribute('aria-expanded',String(!box.hidden));if(!box.hidden){$('#options-box').hidden=true;$('#options-toggle').setAttribute('aria-expanded','false');$('#search').focus();}};
$('#search-close').onclick=()=>{$('#search-box').hidden=true;$('#search-toggle').setAttribute('aria-expanded','false');$('#search').value='';state.search='';state.page=0;renderElements();};
$('#search').oninput=e=>{state.search=e.target.value.trim();state.page=0;renderElements();};
$('#options-toggle').onclick=()=>{const box=$('#options-box');box.hidden=!box.hidden;$('#options-toggle').setAttribute('aria-expanded',String(!box.hidden));if(!box.hidden){$('#search-box').hidden=true;$('#search-toggle').setAttribute('aria-expanded','false');}};
$('#category').onchange=e=>{state.category=e.target.value;state.page=0;renderElements();};$('#sort').onchange=e=>{state.sort=e.target.value;state.page=0;renderElements();};$('#hide-dead').onchange=e=>{state.hideDead=e.target.checked;state.page=0;renderElements();};
$('#sound').onclick=()=>{state.sound=!state.sound;$('#sound').setAttribute('aria-pressed',String(state.sound));$('#sound span').textContent=state.sound?'вкл':'выкл';};
$('#effect').onclick=()=>{state.effect=!state.effect;$('#effect').setAttribute('aria-pressed',String(state.effect));$('#effect span').textContent=state.effect?'вкл':'выкл';renderElements();renderSlots();};
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>go(b.dataset.view));$('#back').onclick=()=>{if(state.detail){state.detail=null;renderOverlay();}else goBack();};$('#overlay-prev').onclick=()=>{state.viewPage--;renderOverlay();};$('#overlay-next').onclick=()=>{state.viewPage++;renderOverlay();};
renderSlots();renderElements();
})();
