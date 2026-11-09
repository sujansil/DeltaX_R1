import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdir,readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {chromium} from 'playwright';
import {fixture} from '../audience-fixtures.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..'),shots=path.resolve(root,'../.preview/r1-audience');await mkdir(shots,{recursive:true});
const mime={'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff':'font/woff','.woff2':'font/woff2','.ttf':'font/ttf'};
const server=createServer(async(req,res)=>{const url=new URL(req.url,'http://local');if(url.pathname==='/favicon.ico'){res.writeHead(204);res.end();return;}const file=path.resolve(root,'.'+url.pathname+(url.pathname.endsWith('/')?'index.html':''));if(!file.startsWith(root+path.sep)){res.writeHead(403);res.end();return;}try{const body=await readFile(file);res.writeHead(200,{'Content-Type':mime[path.extname(file)]||'application/octet-stream'});res.end(body);}catch{res.writeHead(404);res.end();}});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const base=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({headless:true,args:['--use-gl=angle','--use-angle=swiftshader','--enable-webgl','--ignore-gpu-blocklist'],...(process.env.DX_CHROMIUM?{executablePath:process.env.DX_CHROMIUM}:{})});
let checks=0;const pass=name=>console.log(`PASS ${++checks}: ${name}`);
async function open({view='STANDBY',width=1920,height=1080,graphics=false,reduced=false,noWebGL=false,failScene=false}={}){
 let current=fixture(view),offline=false;const errors=[],requests=[],failures=[];
 const context=await browser.newContext({viewport:{width,height},deviceScaleFactor:1,reducedMotion:reduced?'reduce':'no-preference'});
 await context.addInitScript(({noWebGL})=>{
  localStorage.setItem('r1token','sentinel-untouched');window.__gpu={draws:0,deleted:0};window.__moves=[];
  const animate=Element.prototype.animate;Element.prototype.animate=function(frames,options){if(this.classList.contains('leader-row'))window.__moves.push({id:this.dataset.id,frames});return animate.call(this,frames,options);};
  for(const name of ['WebGLRenderingContext','WebGL2RenderingContext']){const p=window[name]?.prototype;if(!p)continue;for(const [method,key] of [['drawArrays','draws'],['drawElements','draws'],['drawArraysInstanced','draws'],['drawElementsInstanced','draws'],['deleteBuffer','deleted']]){const fn=p[method];if(fn)p[method]=function(...args){window.__gpu[key]++;return fn.apply(this,args);};}}
  if(noWebGL){const fn=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(kind,...args){return /webgl/.test(kind)?null:fn.call(this,kind,...args);};}
 },{noWebGL});
 const page=await context.newPage();page.setDefaultTimeout(30000);page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push(r.url()));page.on('response',r=>{if(r.status()>=400&&!r.url().includes('/api/'))failures.push(r.url());});
 await page.route('**/api/**',route=>{assert.equal(new URL(route.request().url()).pathname,'/api/public/audience');assert.equal(route.request().method(),'GET');assert.equal(route.request().headers().authorization,undefined);return route.fulfill({status:offline?503:200,json:current});});
 if(failScene)await page.route('**/audience/assets/scene.js',r=>r.abort());
 await page.goto(base+'/audience/'+(graphics?'':'?render=fallback'));await page.waitForFunction(()=>document.getElementById('connection').textContent==='LIVE');
 if(!failScene)await page.waitForFunction(()=>!!document.querySelector('.katex')||document.body.dataset.scene==='fallback'||document.body.dataset.scene==='ready');
 return {page,context,errors,requests,failures,set:value=>{current=structuredClone(value);},offline:value=>{offline=value;},apply:async value=>{current=structuredClone(value);await page.evaluate(d=>window.__audienceTest.apply(d),current);}};
}
async function clean(run){assert.deepEqual(run.errors,[]);assert.deepEqual(run.failures,[]);assert(run.requests.every(url=>url.startsWith(base)||url.startsWith('data:')));assert.equal(await run.page.evaluate(()=>localStorage.getItem('r1token')),'sentinel-untouched');}
try{
 const run=await open(),{page}=run;
 for(const view of ['PREVIEW','QUESTION','ANSWER','ANSWER_LEADERBOARD','RELAY_STANDBY','REVIEW_READY','REVIEW_QUESTION','REVIEW_ANSWER','LEADERBOARD','THANK_YOU','RESULTS_PENDING']){
  const data=fixture(view);await run.apply(data);assert.equal(await page.locator('body').getAttribute('data-view'),view);
  if(data.question){assert(await page.locator('#question').isVisible());if(view.includes('ANSWER'))assert(await page.locator('#answer').isVisible());else assert.equal(await page.locator('.correct-mark').count(),0);}
  else assert.equal(await page.locator('#prompt').innerHTML(),'');
  assert.equal(await page.locator('.leader-row').count(),data.leaderboard?16:0);
 }
 await clean(run);pass('all public stages, full-screen preview, mandatory answers, relay standby and post-review conclusion');
 const paused=fixture('QUESTION');paused.paused=true;paused.remaining_ms=12000;await run.apply(paused);run.offline(true);await page.waitForTimeout(1100);assert.equal(await page.locator('#timer').innerText(),'00:12');run.offline(false);
 await run.apply(fixture('QUESTION'));await page.waitForFunction(()=>!!document.querySelector('.katex'));assert(await page.locator('.katex').count()>0);
 const image='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60"><text y="40">42</text></svg>');
 const media=fixture('ANSWER');media.question.image_url=image;media.question.options[0].image_url=image;media.question.options[1].text='<script>PRIVATE INJECTION</script>';
 await run.apply(media);assert.equal(await page.locator('#options script').count(),0);assert.equal(await page.locator('.correct-mark').count(),1);assert(await page.locator('#question-image').isVisible());
 const wrong=fixture('ANSWER');wrong.answer.question_id=999;await run.apply(wrong);assert.equal(await page.locator('.correct-mark').count(),0);
 const preview=fixture('PREVIEW');preview.answer=fixture('ANSWER').answer;await run.apply(preview);assert.equal(await page.locator('.correct-mark').count(),0);
 await run.apply(fixture('REVIEW_ANSWER'));assert.equal(await page.locator('.numeric-answer').innerText(),'385');assert.equal(await page.locator('.numeric-answer').getAttribute('data-exact-value'),'385.000000000000');
 const precise=fixture('REVIEW_ANSWER');precise.answer.value='123456789012345678.123456789012';await run.apply(precise);assert.equal(await page.locator('.numeric-answer').innerText(),precise.answer.value);
 await clean(run);pass('frozen authoritative timer, KaTeX, media, exact numeric answers and identity/stage answer gates');
 const first=fixture('LEADERBOARD');await run.apply(first);const second=fixture('LEADERBOARD');second.leaderboard.publication_id=2;[second.leaderboard.data[1],second.leaderboard.data[10]]=[second.leaderboard.data[10],second.leaderboard.data[1]];
 second.leaderboard.data[0].rank=1;second.leaderboard.data[1].rank=1;second.leaderboard.data[2].rank=3;second.leaderboard.data[0].score=-42;
 await run.apply(second);assert.equal(await page.locator('.rank').nth(1).innerText(),'#1');assert.equal(await page.locator('.score').first().innerText(),'-42');
 const moves=await page.evaluate(()=>window.__moves);assert(moves.some(m=>/translate\((?!0px)/.test(m.frames[0].transform)));
 await run.apply(second);assert.equal(await page.evaluate(()=>window.__moves.length),moves.length);
 await page.emulateMedia({reducedMotion:'reduce'});await page.waitForFunction(()=>[...document.querySelectorAll('.leader-row')].every(el=>getComputedStyle(el).transform==='none'));
 await clean(run);pass('top-16, ties, exact scores, two-axis keyed publication-only movement and reduced-motion cancellation');
 run.offline(true);await page.waitForFunction(()=>document.getElementById('connection').textContent.includes('RECONNECTING'));assert.equal(await page.locator('.leader-row').count(),16);
 run.offline(false);run.set(fixture('QUESTION'));await page.waitForFunction(()=>document.body.dataset.view==='QUESTION'&&document.getElementById('connection').textContent==='LIVE');assert.equal(await page.locator('.leader-row').count(),0);
 const stale=fixture('ANSWER');stale.snapshot_at='2000-01-01T00:00:00Z';await page.evaluate(d=>window.__audienceTest.apply(d),stale);assert.equal(await page.locator('body').getAttribute('data-view'),'QUESTION');
 await clean(run);pass('disconnect retention, authoritative reconnect, new-question preemption and stale response rejection');await run.context.close();
 for(const variant of ['no-webgl','failed-scene']){
  const r=await open({view:'QUESTION',graphics:true,noWebGL:variant==='no-webgl',failScene:variant==='failed-scene'});await r.page.waitForFunction(()=>document.body.dataset.scene==='fallback');assert(await r.page.locator('#question').isVisible());assert.deepEqual(r.errors,[]);await r.context.close();pass(`functional fallback ${variant}`);
 }
 if(!process.env.DX_SKIP_RENDER)for(const [view,width,height] of [['STANDBY',1920,1080],['QUESTION',1920,1080],['ANSWER_LEADERBOARD',1920,1080],['REVIEW_ANSWER',1920,1080],['LEADERBOARD',1280,720],['QUESTION',390,844]]){
  const r=await open({view,width,height,graphics:true});await r.page.waitForFunction(()=>document.body.dataset.scene==='ready'&&window.__gpu.draws>0);await r.page.evaluate(()=>document.fonts.ready);await r.page.waitForTimeout(700);
  assert(await r.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await r.page.screenshot({path:path.join(shots,`${view}-${width}.png`),fullPage:true});
  if(view==='STANDBY'){
   await r.page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide')));await r.page.waitForFunction(()=>!document.querySelector('#atrium-world canvas')&&window.__gpu.deleted>0);await r.page.waitForTimeout(200);const draws=await r.page.evaluate(()=>window.__gpu.draws);await r.page.waitForTimeout(200);assert.equal(await r.page.evaluate(()=>window.__gpu.draws),draws);
   await r.page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})));await r.page.waitForFunction(()=>document.body.dataset.scene==='ready'&&!!document.querySelector('#atrium-world canvas'));
   await r.page.evaluate(()=>document.querySelector('#atrium-world canvas').dispatchEvent(new Event('webglcontextlost',{cancelable:true})));await r.page.waitForFunction(()=>document.body.dataset.scene==='fallback'&&!document.querySelector('#atrium-world canvas'));
  }
  await clean(r);await r.context.close();pass(`real Atrium render, screenshot and layout ${view} ${width}×${height}`);
 }
 console.log(`${checks} R1 audience browser checks passed; synthetic public API only.`);
}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
