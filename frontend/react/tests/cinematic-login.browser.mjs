// Real built frontend + synthetic HTTP fixtures. Never starts or contacts the backend.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdir,readFile,readdir} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';

const frontend=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const screenshots=path.resolve(frontend,'../.preview/cinematic-login');
await mkdir(screenshots,{recursive:true});
const types={'.js':'text/javascript','.css':'text/css','.html':'text/html','.woff2':'font/woff2','.woff':'font/woff','.ttf':'font/ttf','.svg':'image/svg+xml'};
const server=createServer(async(req,res)=>{
 try{
  const pathname=decodeURIComponent(new URL(req.url,'http://local').pathname);
  const file=path.resolve(frontend,'.'+pathname+(pathname.endsWith('/')?'index.html':''));
  if(!file.startsWith(frontend+path.sep))throw Error('path');
  const body=await readFile(file);res.writeHead(200,{'Content-Type':types[path.extname(file)]||'application/octet-stream'});res.end(body);
 }catch{res.writeHead(404);res.end('Not found');}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const base=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({headless:true,args:['--use-gl=angle','--use-angle=swiftshader','--enable-webgl','--ignore-gpu-blocklist'],...(process.env.DX_CHROMIUM?{executablePath:process.env.DX_CHROMIUM}:{})});
let checks=0;
const passed=name=>{checks++;console.log(`PASS ${checks}: ${name}`);};
const snapshot=()=>({
 contract_version:'1',snapshot_version:'fixture',snapshot_at:new Date().toISOString(),
 identity:{team_id:1,team_code:'TEAM01',team_name:'VECTOR FOUR',session_id:'fixture-session',round_code:'R1A',relay_slot:1,heartbeat_interval_seconds:15},
 round:{code:'R1A',status:'LIVE'},rules:{version:'fixture',r1a:{advance_mode:'MANUAL',show_results_after_each_question:true,question_count:23,question_duration_ms:30000},r1b:{show_results_after_each_question:false,turn_count:4,total_answering_budget_ms:1680000}},
 player_state:{screen:'ACTIVE_UNANSWERED',current_player:1,current_question_position:7,turn_status:'ACTIVE'},timer:{mode:'QUESTION',running:true,frozen:false,remaining_ms:18000,deadline:new Date(Date.now()+18000).toISOString()},
 score_total:4250,question:{id:7,position:7,text:'The existing R1 question interface.',image_url:null,options:[{id:1,position:1,text:'The existing answer option'}]},own_outcome:{accepted:false,finalized:false},draft:null,available_actions:['SUBMIT']
});

async function open({width=390,height=844,reduced=false,classic=false,noWebGL=false,failWorld=false,instrument=false,restored=false}={}){
 const context=await browser.newContext({viewport:{width,height},deviceScaleFactor:1,isMobile:width<760,hasTouch:width<760,reducedMotion:reduced?'reduce':'no-preference'});
 await context.addInitScript(({noWebGL,instrument,restored})=>{
  if(!sessionStorage.getItem('cinematic-fixture-cleared')){localStorage.clear();sessionStorage.setItem('cinematic-fixture-cleared','1');if(restored)localStorage.setItem('r1token','fixture-token');}
  if(noWebGL){const original=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(type,...args){return /^webgl|experimental-webgl/.test(type)?null:original.call(this,type,...args);};}
  if(instrument){
   window.__gpu={draws:0,buffersDeleted:0,texturesDeleted:0,contextsLost:0};
   for(const name of ['WebGLRenderingContext','WebGL2RenderingContext']){
    const prototype=window[name]?.prototype;if(!prototype)continue;
    for(const [method,counter] of [['drawArrays','draws'],['drawElements','draws'],['drawArraysInstanced','draws'],['drawElementsInstanced','draws'],['deleteBuffer','buffersDeleted'],['deleteTexture','texturesDeleted']]){
     const original=prototype[method];if(!original)continue;
     prototype[method]=function(...args){window.__gpu[counter]++;return original.apply(this,args);};
    }
   }
   const getContext=HTMLCanvasElement.prototype.getContext;
   HTMLCanvasElement.prototype.getContext=function(...args){this.addEventListener('webglcontextlost',()=>window.__gpu.contextsLost++,{once:true});return getContext.apply(this,args);};
  }
 },{noWebGL,instrument,restored});
 const page=await context.newPage(),errors=[],consoleErrors=[],calls=[],external=[];
 page.setDefaultTimeout(20_000);
 page.on('pageerror',e=>{errors.push(e.message);console.error(e.stack);});
 page.on('console',message=>{if(message.type()==='error')consoleErrors.push(message.text());});
 page.on('request',request=>{if(new URL(request.url()).origin!==base&&!request.url().startsWith('data:'))external.push(request.url());});
 let failure=null,activationFailure=null,delay=0,revoked=false;
 await page.route('**/api/**',async route=>{
  const request=route.request(),endpoint=new URL(request.url()).pathname;
  const body=request.postData()?JSON.parse(request.postData()):undefined;
  calls.push({path:endpoint,method:request.method(),body,authorization:request.headers().authorization});
  const fulfill=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
  if(endpoint==='/api/auth/team/login'){
   if(delay)await new Promise(resolve=>setTimeout(resolve,delay));
   if(failure)return fulfill({detail:failure},401);
   revoked=false;return fulfill({access_token:'fixture-token',team_code:'TEAM01',team_name:'VECTOR FOUR',round_code:'R1A',slot:1,heartbeat_interval_seconds:15});
  }
  if(endpoint==='/api/auth/team/set-password')return activationFailure?fulfill({detail:activationFailure},400):fulfill({ok:true});
  if(revoked)return fulfill({detail:{code:'SESSION_INACTIVE',message:'Session expired or revoked; log in again'}},401);
  if(endpoint==='/api/player/bootstrap')return fulfill(snapshot());
  if(endpoint==='/api/auth/team/logout'){revoked=true;return fulfill({ok:true});}
  return fulfill({ok:true});
 });
 if(failWorld){
  const assets=path.join(frontend,'player/assets');
  const world=(await Promise.all((await readdir(assets)).filter(name=>name.endsWith('.js')).map(async name=>({name,source:await readFile(path.join(assets,name),'utf8')})))).find(file=>file.source.includes('WEBGL_lose_context')&&file.source.includes('frameloop:'));
  assert(world,'find the cinematic world chunk');
  await page.route(`**/player/assets/${world.name}`,route=>route.abort('failed'));
 }
 await page.goto(base+'/player/'+(classic?'?login=classic':''));
 return {page,context,errors,consoleErrors,calls,external,setFailure:value=>{failure=value;},setActivationFailure:value=>{activationFailure=value;},setDelay:value=>{delay=value;},revoke:()=>{revoked=true;}};
}

async function fill(page,username='vector',password='example-password'){
 await page.getByLabel('Team username').fill(username);
 await page.getByLabel('Password',{exact:true}).fill(password);
}
async function assertGame(page){
 await page.getByRole('heading',{name:'The existing R1 question interface.'}).waitFor();
 assert.equal(await page.locator('.dx-login').count(),0);assert.equal(await page.locator('canvas').count(),0);
 assert.equal(new URL(page.url()).pathname,'/player/');
}
async function noErrors(run,expectedHttpStatuses=[]){assert.deepEqual(run.errors,[]);assert.deepEqual(run.consoleErrors.filter(message=>!expectedHttpStatuses.some(status=>message.includes(`status of ${status} (`))),[]);assert.deepEqual(run.external,[]);}

try{
 for(const [width,height] of [[390,844],[360,740],[1440,900]]){
  const run=await open({width,height}),{page}=run;
  await page.locator('.dx-world canvas').waitFor();await page.evaluate(()=>document.fonts.ready);await page.waitForTimeout(2400);
  assert(await page.getByLabel('Team username').isVisible());
  assert(await page.getByRole('button',{name:'Enter the arena'}).isVisible());
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  const submit=await page.getByRole('button',{name:'Enter the arena'}).boundingBox();assert(submit.y+submit.height<=height,'submit is initially in portrait viewport');
  await page.screenshot({path:path.join(screenshots,`r1-${width}x${height}.png`),fullPage:true});
  await noErrors(run);passed(`real WebGL render and complete form at ${width}×${height}`);await run.context.close();
 }

 {
  const run=await open(),{page}=run;run.setFailure('Invalid team credentials');run.setDelay(400);
  await fill(page,'  vector  ',' password with spaces ');await page.getByRole('button',{name:'Enter the arena'}).click();
  await page.getByRole('button',{name:'Connecting…'}).waitFor();assert(await page.getByRole('button',{name:'Connecting…'}).isDisabled());
  await page.getByRole('alert').filter({hasText:'Invalid team credentials'}).waitFor();
  assert.equal(await page.evaluate(()=>localStorage.getItem('r1token')),null);assert.equal(run.calls.filter(call=>call.path==='/api/player/bootstrap').length,0);
  assert.deepEqual(run.calls.filter(call=>call.path==='/api/auth/team/login').map(call=>call.body),[{username:'vector',password:' password with spaces '}]);
  assert.equal(await page.getByLabel('Password',{exact:true}).inputValue(),' password with spaces ');
  await noErrors(run,[401]);passed('existing invalid-credential error, busy lock, exact payload, and retry');await run.context.close();
 }

 {
  const run=await open({instrument:true}),{page}=run;
  await page.locator('.dx-world canvas').waitFor();await page.waitForFunction(()=>window.__gpu.draws>0);
  await fill(page,'  vector  ',' password with spaces ');
  const password=page.getByLabel('Password',{exact:true});await page.getByRole('button',{name:'Show password'}).click();assert.equal(await password.getAttribute('type'),'text');
  await page.getByRole('button',{name:'Hide password'}).click();assert.equal(await password.getAttribute('type'),'password');
  const before=await page.evaluate(()=>({...window.__gpu}));await password.press('Enter');await assertGame(page);
  assert.equal(await page.evaluate(()=>localStorage.getItem('r1token')),'fixture-token');
  const loginCalls=run.calls.filter(call=>call.path==='/api/auth/team/login');assert.equal(loginCalls.length,1);assert.deepEqual(loginCalls[0].body,{username:'vector',password:' password with spaces '});
  assert(run.calls.some(call=>call.path==='/api/player/bootstrap'&&call.authorization==='Bearer fixture-token'));
  assert(run.calls.some(call=>call.path==='/api/auth/team/heartbeat'&&call.authorization==='Bearer fixture-token'));
  passed('Enter submits once through existing auth and enters the existing interface immediately');
  await page.waitForTimeout(1300);const after=await page.evaluate(()=>({...window.__gpu}));
  assert(after.buffersDeleted>before.buffersDeleted,'GPU buffers disposed');assert(after.texturesDeleted>before.texturesDeleted,'GPU textures disposed');assert(after.contextsLost>before.contextsLost,'canvas context released');
  await page.waitForTimeout(350);assert.equal((await page.evaluate(()=>window.__gpu.draws)),after.draws,'no scene draws during gameplay');
  passed('GPU disposal and no background login rendering during gameplay');
  await page.reload();await assertGame(page);assert.equal(run.calls.filter(call=>call.path==='/api/auth/team/login').length,1);
  passed('refresh restores existing r1token without another login');
  await page.getByRole('button',{name:'Log out',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'Confirm',exact:true}).click();
  await page.getByTestId('cinematic-login').waitFor();await page.getByRole('alert').filter({hasText:'Signed out. Your team device session is available again.'}).waitFor();
  assert.equal(await page.evaluate(()=>localStorage.getItem('r1token')),null);assert(run.calls.some(call=>call.path==='/api/auth/team/logout'&&call.method==='POST'&&call.authorization==='Bearer fixture-token'));
  await noErrors(run);passed('server logout releases existing session and returns to cinematic login with notice');await run.context.close();
 }

 for(const classic of [false,true]){
  const run=await open({classic,reduced:true}),{page}=run;
  await page.getByRole('button',{name:'First time? Activate your team'}).click();
  await page.getByRole('heading',{name:'Activate your team.'}).waitFor();
  const username=page.getByLabel('Team username'),activation=page.getByLabel('Activation code'),password=page.getByLabel('Choose a password',{exact:true});
  await username.fill('  vector  ');await activation.fill('ABCD-EFGH');await password.fill('short');
  await page.getByRole('button',{name:'Create password'}).click();assert.equal(run.calls.length,0);assert.equal(await password.evaluate(element=>element.validity.tooShort),true);
  assert.equal(await username.getAttribute('maxLength'),'120');assert.equal(await activation.getAttribute('maxLength'),'256');assert.equal(await password.getAttribute('maxLength'),'128');
  run.setActivationFailure('Invalid or expired activation code');await password.fill(' new password ');await page.getByRole('button',{name:'Create password'}).click();
  await page.getByRole('alert').filter({hasText:'Invalid or expired activation code'}).waitFor();assert(await activation.isVisible());
  run.setActivationFailure(null);await page.getByRole('button',{name:'Create password'}).click();
  await page.getByRole('status').filter({hasText:'Password created. Sign in with your team username.'}).waitFor();
  assert.equal(await page.getByLabel('Activation code').count(),0);
  assert.deepEqual(run.calls.filter(call=>call.path==='/api/auth/team/set-password').map(call=>call.body),Array(2).fill({username:'vector',activation_token:'ABCD-EFGH',new_password:' new password '}));
  assert.equal(await page.evaluate(()=>localStorage.getItem('r1token')),null);
  await fill(page);await page.getByRole('button',{name:'Enter the arena'}).click();await assertGame(page);
  await noErrors(run,[400]);passed(`${classic?'classic':'cinematic'} activation validation, server error, exact token payload, success notice, and subsequent sign-in`);await run.context.close();
 }

 {
  const run=await open({restored:true,reduced:true}),{page}=run;await assertGame(page);run.revoke();
  await page.getByRole('button',{name:'Refresh',exact:true}).click();
  await page.getByTestId('cinematic-login').waitFor();await page.getByRole('alert').filter({hasText:'Your session expired or was revoked. Sign in again.'}).waitFor();
  assert.equal(await page.evaluate(()=>localStorage.getItem('r1token')),null);
  await noErrors(run,[401]);passed('401 recovery preserves existing session notice and returns to cinematic login');await run.context.close();
 }

 {
  const run=await open({reduced:true}),{page}=run;
  await page.locator('.dx-world canvas').waitFor();await page.evaluate(()=>document.fonts.ready);await page.waitForTimeout(2200);
  assert(await page.getByRole('button',{name:'Reduced motion follows your system preference'}).isDisabled());
  const a=await page.screenshot();await page.waitForTimeout(350);const b=await page.screenshot();assert(a.equals(b),'reduced motion produces a stable scene');
  await page.setViewportSize({width:360,height:480});await page.getByLabel('Team username').fill('vector');await page.getByLabel('Password',{exact:true}).fill('demo');
  await page.getByLabel('Password',{exact:true}).press('Tab');assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('aria-label')),'Show password');
  await page.keyboard.press('Tab');assert.match(await page.evaluate(()=>document.activeElement.textContent),/Enter the arena/);
  const button=page.getByRole('button',{name:'Enter the arena'});await button.scrollIntoViewIfNeeded();
  const box=await button.boundingBox();assert(box.y>=0&&box.y+box.height<=480);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  // Capture the visible short viewport without temporarily expanding it, which
  // would itself trigger the keyboard resize handling being verified.
  await page.screenshot({path:path.join(screenshots,'r1-short-viewport.png')});
  await page.keyboard.press('Enter');await assertGame(page);await noErrors(run);
  passed('reduced motion, keyboard Tab/Enter access, and short-viewport scrolling');await run.context.close();
 }

 {
  // Model Safari's visual viewport shrinking while its layout viewport stays tall.
  const run=await open({reduced:true,noWebGL:true}),{page}=run;
  await fill(page);await page.getByLabel('Password',{exact:true}).focus();
  await page.evaluate(()=>{Object.defineProperty(window.visualViewport,'height',{configurable:true,value:400});window.visualViewport.dispatchEvent(new Event('resize'));});
  await page.waitForFunction(()=>document.querySelector('.dx-login').dataset.keyboard==='open');
  await page.waitForTimeout(100);
  const input=await page.getByLabel('Password',{exact:true}).boundingBox();assert(input.y>=18&&input.y+input.height<=382,'focused input stays above modeled keyboard');
  await page.getByLabel('Password',{exact:true}).press('Tab');await page.keyboard.press('Tab');
  await page.evaluate(()=>window.visualViewport.dispatchEvent(new Event('resize')));await page.waitForTimeout(100);
  const button=await page.getByRole('button',{name:'Enter the arena'}).boundingBox();assert(button.y>=18&&button.y+button.height<=382,'submit can be focused above modeled keyboard');
  await page.keyboard.press('Enter');await assertGame(page);await noErrors(run);
  passed('visual-viewport keyboard inset keeps the focused input and submit above the keyboard');await run.context.close();
 }

 for(const noWebGL of [true,false]){
  const run=await open({noWebGL,reduced:true}),{page}=run;
  if(!noWebGL){await page.locator('.dx-world canvas').waitFor();await page.locator('.dx-world canvas').evaluate(canvas=>canvas.dispatchEvent(new Event('webglcontextlost',{cancelable:true})));}
  await page.getByText('Atmospheric view · WebGL 3D unavailable').waitFor();
  await fill(page);await page.getByRole('button',{name:'Enter the arena'}).click();await assertGame(page);
  await noErrors(run);passed(`${noWebGL?'unavailable':'lost'} WebGL falls back without blocking real authentication`);await run.context.close();
 }

 {
  const run=await open({failWorld:true,reduced:true}),{page}=run;
  await page.getByText('Atmospheric view · WebGL 3D unavailable').waitFor();await fill(page);
  await page.getByRole('button',{name:'Enter the arena'}).click();await assertGame(page);
  assert.equal(run.errors.filter(error=>!error.includes('dynamically imported')).length,0);
  passed('failed visual chunk leaves existing form operational');await run.context.close();
 }
 console.log(`${checks} cinematic integration checks passed (synthetic API fixtures, no backend/database). Screenshots: ${screenshots}`);
}catch(error){
 for(const context of browser.contexts())for(const page of context.pages())await page.screenshot({path:path.join(screenshots,'failure.png'),fullPage:true}).catch(()=>{});
 throw error;
}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
