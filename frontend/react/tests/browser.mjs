// Browser contract tests use synthetic API fixtures, never event data.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const shots=process.env.DX_SCREENSHOTS; if(shots)await mkdir(shots,{recursive:true});
const types={'.js':'text/javascript','.css':'text/css','.html':'text/html','.woff2':'font/woff2','.woff':'font/woff','.ttf':'font/ttf','.svg':'image/svg+xml'};
const server=createServer(async(req,res)=>{try{const pathname=decodeURIComponent(new URL(req.url,'http://local').pathname).replace(/^\/admin-ui\//,'/admin/');const file=path.resolve(root,'.'+pathname+(pathname.endsWith('/')?'index.html':''));if(!file.startsWith(root+path.sep))throw Error('path');const body=await readFile(file);res.writeHead(200,{'Content-Type':types[path.extname(file)]||'application/octet-stream'});res.end(body)}catch{res.writeHead(404);res.end('Not found')}});
await new Promise(r=>server.listen(0,'127.0.0.1',r));const base='http://127.0.0.1:'+server.address().port;
const browser=await chromium.launch({headless:true,...(process.env.DX_CHROMIUM?{executablePath:process.env.DX_CHROMIUM,args:['--no-sandbox','--no-zygote','--single-process','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']}: {})});
let checks=0;
const clone=v=>JSON.parse(JSON.stringify(v));
function fixture(round='R1A',slot=1){return {contract_version:'1',snapshot_version:'fixture',snapshot_at:new Date().toISOString(),identity:{team_id:1,team_code:'TEAM01',team_name:'VECTOR FOUR',session_id:'fixture-session',round_code:round,relay_slot:slot,heartbeat_interval_seconds:15},round:{code:round,status:'LIVE'},rules:{version:'fixture',r1a:{advance_mode:'MANUAL',show_results_after_each_question:true,question_count:23,question_duration_ms:30000},r1b:{show_results_after_each_question:false,turn_count:4,total_answering_budget_ms:1680000}},player_state:{screen:round==='R1A'?'ACTIVE_UNANSWERED':'ACTIVE',current_player:1,current_question_position:7,turn_status:'ACTIVE'},timer:{mode:round==='R1A'?'QUESTION':'CONTINUOUS',running:true,frozen:false,remaining_ms:18000,deadline:new Date(Date.now()+18000).toISOString()},score_total:4250,question:{id:7,position:7,text:'A price rises by 20%, then falls by 20%. What is the net change?',image_url:null,options:[{id:1,position:1,text:'No change'},{id:2,position:2,text:'4% decrease'},{id:3,position:3,text:'4% increase'},{id:4,position:4,text:'2% decrease'}]},own_outcome:{accepted:false,finalized:false},draft:round==='R1B'?{turn_id:'00000000-0000-4000-8000-000000000001',version:0,answer:''}:null,available_actions:round==='R1A'?['SUBMIT']:['SUBMIT','SKIP','SAVE_DRAFT']}}
async function scenario(state,{reducedMotion='no-preference',width=390,height=844,classic=false,themeClassic=false}={}){
 const context=await browser.newContext({viewport:{width,height},isMobile:width<601,deviceScaleFactor:1,hasTouch:width<1024,reducedMotion});
 await context.addInitScript(()=>{if(!sessionStorage.getItem('fixture-cleared')){localStorage.clear();sessionStorage.setItem('fixture-cleared','1')}});
 const page=await context.newPage();const errors=[],calls=[];page.on('pageerror',e=>{errors.push(e.message);console.error(e.stack)});let revoked=false,submitted=0;
 await page.route('**/api/**',async route=>{const req=route.request(),p=new URL(req.url()).pathname;calls.push({p,method:req.method(),body:req.postDataJSON?.()});
  const fulfill=(d,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(d)});
  if(p.endsWith('/login'))return fulfill({access_token:'fixture-token'});
  if(revoked)return fulfill({detail:{code:'SESSION_INACTIVE',message:'Session expired or revoked; log in again'}},401);
  if(p==='/api/player/bootstrap'){const d=clone(state);d.snapshot_at=new Date().toISOString();return fulfill(d)}
  if(p.endsWith('/heartbeat')||p.endsWith('/departing'))return fulfill({ok:true});
  if(p.endsWith('/logout')){revoked=true;return fulfill({ok:true})}
  if(p.endsWith('/mine'))return fulfill({published:true,rank:12,score:4250,published_at:new Date().toISOString()});
  if(p==='/api/r1b/draft'&&req.method()==='PUT'){const d=req.postDataJSON();state.draft={...d,accepted_at:new Date().toISOString()};return fulfill(state.draft)}
  if(p.endsWith('/submit')){submitted++;state.own_outcome={accepted:true,finalized:true,submission_id:100,question_position:7,player_number:1,points:null,result:null,score_revealed:false};state.question=null;state.draft=null;state.available_actions=state.identity.round_code==='R1A'?[]:['READY'];state.player_state.screen=state.identity.round_code==='R1A'?'ACTIVE_ACCEPTED':'HANDOVER_READY';if(state.identity.round_code==='R1B')state.player_state.current_player=2;return fulfill({accepted:true,position:7,message:'Answer recorded'})}
  return fulfill({ok:true});
 });
 const query=new URLSearchParams();if(classic)query.set('login','classic');if(themeClassic)query.set('theme','classic');
 await page.goto(base+'/player/'+(query.size?'?'+query:''));
 return {page,context,errors,calls,revoke:()=>{revoked=true},submits:()=>submitted,login:async()=>{await page.getByLabel('Team username').fill('vector',{timeout:8000}).catch(async e=>{console.log('LOGIN DEBUG',await page.locator('body').innerText(),errors);throw e});await page.getByLabel('Password',{exact:true}).fill('example-password');await page.getByRole('button',{name:'Enter the arena'}).click();await page.getByText('VECTOR FOUR',{exact:true}).waitFor();}};
}
try{
 // Preserve the original scroll-only presentation behind the classic switch.
 const s=fixture(),a=await scenario(s,{classic:true});const {page}=a;
 await page.locator('canvas').waitFor({timeout:15000});
 assert(await page.getByRole('button',{name:'Enter the arena'}).boundingBox().then(b=>b.y>=800));checks++;
 if(shots)await page.screenshot({path:shots+'/01-login.png'});
 await page.getByRole('link',{name:'Scroll to enter'}).click();await page.waitForTimeout(650);
 if(shots)await page.screenshot({path:shots+'/02-credentials.png'});
 await a.login();await page.getByRole('heading',{name:/A price rises/}).waitFor();
 await page.waitForTimeout(600);if(shots)await page.screenshot({path:shots+'/03-question.png'});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));checks++;
 await page.getByRole('radio',{name:'B: 4% decrease',exact:true}).check();await page.getByRole('button',{name:'Lock answer'}).click();
 await page.getByRole('heading',{name:'Answer locked.'}).waitFor();assert.equal(a.submits(),1);assert.equal(await page.locator('.score-reveal').count(),0);checks++;
 // Full-screen result only after closure, and not again after reload.
 assert.match(await page.locator('.strip-score').innerText(),/4,250/);checks++;
 await page.reload();await page.getByRole('heading',{name:'Answer locked.'}).waitFor();assert.match(await page.locator('.strip-score').innerText(),/4,250/);assert.equal(await page.locator('.score-reveal').count(),0);checks++;
 s.player_state.screen='BETWEEN_QUESTIONS';s.timer.running=false;s.timer.remaining_ms=0;s.score_total=5000;s.own_outcome.points=750;s.own_outcome.result='CORRECT';s.own_outcome.score_revealed=true;
 s.leaderboard={published:true,automatic:true,after_question:7,total_teams:19,published_at:new Date().toISOString(),data:Array.from({length:16},(_,i)=>({team_id:i+1,team_name:i?'TEAM '+String(i+1).padStart(2,'0'):'VECTOR FOUR',rank:i+1,score:5000-i*100})),mine:{team_id:1,rank:1,score:5000}};
 await page.getByRole('button',{name:'Refresh',exact:true}).click();await page.locator('.score-reveal').waitFor();
 await page.waitForTimeout(600);if(shots)await page.screenshot({path:shots+'/04-score-reveal.png'});
 await page.getByRole('button',{name:'Continue'}).click();await page.getByRole('heading',{name:'Leaderboard',exact:true}).waitFor();if(shots)await page.screenshot({path:shots+'/05-leaderboard.png'});assert.match(await page.locator('.strip-score').innerText(),/5,000/);const board=page.locator('.board-table-wrap'),rows=board.locator('tbody tr');assert.equal(await rows.count(),16);assert.match(await rows.nth(15).innerText(),/#16/);const scroll=await board.evaluate(el=>({overflow:getComputedStyle(el).overflowY,scrollHeight:el.scrollHeight,clientHeight:el.clientHeight,scrollWidth:el.scrollWidth,clientWidth:el.clientWidth}));assert.equal(scroll.overflow,'auto');assert(scroll.scrollHeight>scroll.clientHeight);assert(scroll.scrollWidth<=scroll.clientWidth);await board.evaluate(el=>{el.scrollTop=el.scrollHeight});const scrolled=await board.evaluate(el=>({top:el.scrollTop,headTop:el.querySelector('thead th').getBoundingClientRect().top,wrapTop:el.getBoundingClientRect().top}));assert(scrolled.top>0);assert(Math.abs(scrolled.headTop-scrolled.wrapTop)<=2);assert(await rows.nth(15).isVisible());checks++;
 s.leaderboard={...s.leaderboard,total_teams:3,data:s.leaderboard.data.slice(0,3)};await page.getByRole('button',{name:'Refresh',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('.board-table-wrap tbody tr').length===3);assert.equal(await rows.count(),3);assert.equal(await page.getByText(/Top 16 shown/).count(),0);assert.equal(await page.getByText(/All 3 ranked teams shown/).count(),1);checks++;
 s.leaderboard={...s.leaderboard,total_teams:0,data:[],mine:null};await page.getByRole('button',{name:'Refresh',exact:true}).click();await page.getByText('No enrolled teams in this snapshot.').waitFor();assert.equal(await rows.count(),0);checks++;
 s.leaderboard={published:true,automatic:true,after_question:7,total_teams:19,published_at:new Date().toISOString(),data:Array.from({length:16},(_,i)=>({team_id:i+1,team_name:i?'TEAM '+String(i+1).padStart(2,'0'):'VECTOR FOUR',rank:i+1,score:5000-i*100})),mine:{team_id:1,rank:1,score:5000}};await page.reload();await page.getByRole('heading',{name:'Leaderboard',exact:true}).waitFor();assert.equal(await page.locator('.score-reveal').count(),0);checks++;
 s.player_state.screen='ACTIVE_UNANSWERED';s.player_state.current_question_position=8;s.question={id:8,position:8,text:'The organiser revealed question eight.',options:[{id:8,position:1,text:'A new option'}]};s.own_outcome={accepted:false,finalized:false};s.available_actions=['SUBMIT'];s.timer.running=true;s.timer.remaining_ms=30000;await page.getByRole('button',{name:'Refresh',exact:true}).click();await page.getByRole('heading',{name:'The organiser revealed question eight.'}).waitFor();assert.equal(await page.getByRole('heading',{name:'Leaderboard',exact:true}).count(),0);checks++;
 a.revoke();await page.getByRole('button',{name:'Refresh',exact:true}).click();await page.getByRole('button',{name:'Enter the arena'}).waitFor();assert.match(await page.locator('[role=alert]').innerText(),/expired|revoked/);checks++;assert.deepEqual(a.errors,[]);await a.page.close();
 // Polling never resets typing; autosave payloads match the server contract.
 const r=fixture('R1B');r.question.position=1;r.player_state.current_question_position=1;r.question.text='Find the numerical value of $2^5 + 10$.';r.timer.remaining_ms=28*60000;r.score_total=null;r.leaderboard={published:false,data:[],mine:null};
 const b=await scenario(r);await b.login();const input=b.page.getByLabel('Your numerical answer');await input.fill('-12.75');await b.page.getByText('Saved to server',{exact:true}).waitFor();await b.page.waitForTimeout(1700);assert.equal(await input.inputValue(),'-12.75');assert(b.calls.some(c=>c.method==='PUT'&&c.body.answer==='-12.75'&&c.body.turn_id===r.draft.turn_id));checks++;
 await b.page.reload();await b.page.getByLabel('Your numerical answer').waitFor();assert.equal(await b.page.getByLabel('Your numerical answer').inputValue(),'-12.75');checks++;
 assert.equal(await b.page.getByText(/results shown|results remain|leaderboard revealed|question ends/i).count(),0);checks++;
 const submittedTurn=r.draft.turn_id;await b.page.getByLabel('Your numerical answer').fill('42');await b.page.getByRole('button',{name:'Submit answer',exact:true}).click();await b.page.getByRole('heading',{name:'Pass to the next player.'}).waitFor();assert.equal(await b.page.locator('.score-reveal').count(),0);assert.equal(await b.page.locator('.strip-score').count(),0);assert.equal(await b.page.getByText(/results shown|results remain|leaderboard revealed|question ends/i).count(),0);assert.equal(b.submits(),1);const submittedBody=b.calls.find(c=>c.p==='/api/r1b/submit').body;assert.equal(submittedBody.answer,'42');assert.equal(submittedBody.turn_id,submittedTurn);checks++;
 r.available_actions=[];r.player_state.screen='FINISHED';r.player_state.turn_status='FINISHED';r.score_total=4000;r.own_outcome={accepted:true,finalized:true,score_revealed:true,submission_id:100,question_position:1,player_number:1,points:4000,result:'CORRECT'};r.leaderboard={published:true,final:true,publication_id:9,published_at:new Date().toISOString(),total_teams:1,data:[{team_id:1,team_name:'VECTOR FOUR',rank:1,score:4000}],mine:{team_id:1,rank:1,score:4000}};await b.page.getByRole('button',{name:'Refresh',exact:true}).click();await b.page.locator('.score-reveal').waitFor();await b.page.getByRole('button',{name:'Continue'}).click();await b.page.getByRole('heading',{name:'R1B results'}).waitFor();assert.match(await b.page.locator('.strip-score').innerText(),/4,000/);checks++;
 assert.deepEqual(b.errors,[]);await b.page.close();
 // Shared-device handover and narrow viewport: question remains hidden until READY.
 const waiting=fixture('R1B');waiting.question=null;waiting.draft=null;waiting.available_actions=['READY'];waiting.player_state.screen='HANDOVER_READY';waiting.player_state.current_player=2;waiting.player_state.turn_status='WAITING';
 const c=await scenario(waiting,{width:320,reducedMotion:'reduce'});await c.login();await c.page.getByRole('heading',{name:'Pass to the next player.'}).waitFor();assert.equal(await c.page.getByRole('button',{name:'Submit answer',exact:true}).count(),0);assert.equal(await c.page.getByRole('button',{name:'Player 2 · READY'}).count(),1);assert(await c.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));checks++;
 assert.deepEqual(c.errors,[]);await c.page.close();
 // Hidden R1A results use neutral acknowledgement with no result-policy promise.
 const hidden=fixture('R1A');hidden.rules.r1a.show_results_after_each_question=false;hidden.question=null;hidden.available_actions=[];hidden.player_state.screen='ACTIVE_ACCEPTED';hidden.own_outcome={accepted:true,finalized:false,score_revealed:false,points:null,result:null};hidden.score_total=null;
 const hiddenRun=await scenario(hidden);await hiddenRun.login();await hiddenRun.page.getByRole('heading',{name:'Answer locked.'}).waitFor();assert.equal(await hiddenRun.page.getByText(/results shown|results appear|leaderboard revealed|question ends/i).count(),0);checks++;await hiddenRun.page.close();
 // AUTO uses only the sampled backend due time: two delays, pause/refresh, missing due time, and zero.
 const auto=fixture('R1A');auto.rules.r1a.advance_mode='AUTO';auto.rules.r1a.show_results_after_each_question=false;auto.question=null;auto.available_actions=[];auto.player_state.screen='BETWEEN_QUESTIONS';auto.own_outcome={accepted:true,finalized:true,score_revealed:false,submission_id:77,question_position:1,points:null,result:null};auto.score_total=null;auto.leaderboard=null;auto.r1a_phase={phase:'RESULT',next_reveal_at:new Date(Date.now()+8000).toISOString(),remaining_ms:8000,frozen:false};
 const autoRun=await scenario(auto);await autoRun.login();await autoRun.page.getByRole('heading',{name:/Next question starts in [78]s/}).waitFor();assert.equal(await autoRun.page.locator('.waiting-panel h1').count(),1);assert.equal(await autoRun.page.locator('.waiting-panel p').count(),0);assert.equal(await autoRun.page.getByText(/Leaderboard|Finalising|opens automatically/).count(),0);checks++;
 auto.r1a_phase={phase:'RESULT',next_reveal_at:new Date(Date.now()+21500).toISOString(),remaining_ms:21500,frozen:false};await autoRun.page.reload();await autoRun.page.getByRole('heading',{name:/Next question starts in 2[01]s/}).waitFor();assert.equal(await autoRun.page.locator('.waiting-panel h1').count(),1);checks++;
 auto.r1a_phase={phase:'RESULT',next_reveal_at:new Date(Date.now()+5100).toISOString(),remaining_ms:5100,frozen:true};await autoRun.page.getByRole('button',{name:'Refresh',exact:true}).click();await autoRun.page.getByRole('heading',{name:'Paused · Next question starts in 6s'}).waitFor();await autoRun.page.waitForTimeout(1100);assert.equal(await autoRun.page.locator('.waiting-panel h1').innerText(),'Paused · Next question starts in 6s');checks++;
 auto.r1a_phase={phase:'RESULT',next_reveal_at:null,remaining_ms:0,frozen:false};await autoRun.page.getByRole('button',{name:'Refresh',exact:true}).click();await autoRun.page.getByRole('heading',{name:'Preparing next question…'}).waitFor();assert.equal(await autoRun.page.locator('.waiting-panel').innerText(),'Preparing next question…');checks++;
 auto.r1a_phase={phase:'RESULT',next_reveal_at:new Date().toISOString(),remaining_ms:0,frozen:false};await autoRun.page.getByRole('button',{name:'Refresh',exact:true}).click();await autoRun.page.getByRole('heading',{name:'Next question starts in 0s'}).waitFor();await autoRun.page.waitForTimeout(500);assert.equal(await autoRun.page.locator('.waiting-panel h1').innerText(),'Next question starts in 0s');checks++;await autoRun.page.close();
 // The skin switch affects presentation only; native questions and actions stay intact.
 for(const round of ['R1A','R1B'])for(const themeClassic of [false,true]){
  const state=fixture(round),r=await scenario(state,{themeClassic,reducedMotion:'reduce',width:themeClassic?390:1440});await r.login();
  assert.equal(await r.page.evaluate(()=>document.documentElement.classList.contains('r1-player-atrium')),!themeClassic);
  assert.equal(await r.page.locator('.game-shell canvas').count(),0);
  if(shots)await r.page.screenshot({path:shots+`/player-${round}-${themeClassic?'classic':'atrium'}.png`,fullPage:true});
  if(round==='R1A'){await r.page.locator('input[type="radio"]').nth(1).check();await r.page.getByRole('button',{name:'Lock answer'}).click();}
  else{await r.page.getByLabel('Your numerical answer').fill('42');await r.page.getByRole('button',{name:'Submit answer',exact:true}).click();}
  await r.page.getByRole('heading',{name:round==='R1A'?'Answer locked.':'Pass to the next player.'}).waitFor();assert.equal(r.submits(),1);assert.deepEqual(r.errors,[]);await r.page.close();checks++;
 }
 // Targeted responsive regression matrix: real native questions, media and controls.
 const viewports=[{width:390,height:844},{width:768,height:1024},{width:1366,height:768},{width:1920,height:1080}];
 const fits=async page=>assert(await page.evaluate(()=>{
  if(document.documentElement.scrollWidth>innerWidth)return false;
  return [...document.querySelectorAll('.game-shell button,.game-shell input:not([type=radio])')].filter(el=>el.getClientRects().length).every(el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.width>0;});
 }),'No horizontal overflow or clipped controls');
 for(const viewport of viewports)for(const round of ['R1A','R1B'])for(const themeClassic of [false,true]){
  const state=fixture(round);state.timer.running=false;state.timer.remaining_ms=30000;
  if(round==='R1B'){state.question.position=1;state.player_state.current_question_position=1;}
  state.question.text='For a geometric sequence with $a_n=3(2^{n-1})$, determine the exact value of the following expression, explaining how each term contributes to the total: $$\\sum_{k=1}^{12} \\frac{3(2^{k-1})}{2^k} + \\sqrt{144}.$$ Use the diagram and compare all the answer choices before submitting.';
  state.question.image_url='/ui-fixture.svg';state.question.options[0].image_url='/ui-fixture.svg';
  state.question.options[1].text='A longer mathematical answer: $\\frac{1}{2}+\\frac{1}{4}+\\frac{1}{8}$, with a detailed explanation and special characters < > &.';
  const r=await scenario(state,{...viewport,themeClassic,reducedMotion:'reduce'});
  await r.page.route('**/ui-fixture.svg',route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="360" viewBox="0 0 1200 360"><rect width="1200" height="360" fill="#14283a"/><path d="M80 290 580 70 1080 290Z" fill="none" stroke="#9bdcd4" stroke-width="5"/><text x="550" y="250" fill="#e5cca0" font-size="44">x + y</text></svg>'}));
  await r.login();await r.page.locator('.question-image').waitFor();await r.page.waitForFunction(()=>[...document.querySelectorAll('.question-panel img')].every(img=>img.complete&&img.naturalWidth>0));
  assert(await r.page.locator('.question-body .katex').count()>0);await fits(r.page);
  const shell=await r.page.locator('.game-shell').boundingBox();
  if(!themeClassic&&viewport.width>=601)assert(shell.width>=Math.min(viewport.width,1400)-2);
  if(themeClassic)assert(shell.width<=650);
  if(!themeClassic&&viewport.width>=1024){const q=await r.page.locator('.question-body').boundingBox(),f=await r.page.locator(round==='R1A'?'.mcq-form':'.numeric-form').boundingBox();assert(f.x>q.x+q.width);assert(Math.abs(f.y-q.y)<2);}
  if(round==='R1A'){assert(await r.page.getByRole('button',{name:'Lock answer'}).isDisabled());await r.page.locator('input[type=radio]').nth(1).check();assert(await r.page.getByRole('button',{name:'Lock answer'}).isEnabled());}
  else{await r.page.getByLabel('Your numerical answer').fill('123.75');await r.page.getByText('Saved to server',{exact:true}).waitFor();await r.page.getByRole('button',{name:'Skip this question'}).click();await r.page.getByRole('dialog',{name:'Skip this question?'}).waitFor();await fits(r.page);assert.equal(r.submits(),0);await r.page.getByRole('button',{name:'Cancel',exact:true}).click();}
  if(shots&&!themeClassic)await r.page.screenshot({path:shots+`/responsive-${round}-${viewport.width}.png`,fullPage:true});
  state.timer.remaining_ms=0;state.available_actions=[];await r.page.reload();await r.page.locator('.question-panel').waitFor();
  if(round==='R1A'){await r.page.getByRole('timer',{name:'00:00 remaining'}).waitFor();assert(await r.page.locator('input[type=radio]').first().isDisabled());}
  else{assert(await r.page.getByLabel('Your numerical answer').isDisabled());assert(await r.page.getByRole('button',{name:'Skip this question'}).isDisabled());}
  await fits(r.page);
  if(round==='R1B'){
   state.question=null;state.draft=null;state.player_state.screen='HANDOVER_READY';state.player_state.current_player=2;state.player_state.turn_status='WAITING';state.available_actions=['READY'];state.timer.frozen=true;
   await r.page.reload();await r.page.getByRole('button',{name:'Player 2 · READY'}).waitFor();await fits(r.page);assert(await r.page.getByRole('button',{name:'Player 2 · READY'}).isEnabled());
  }
  await r.page.getByRole('button',{name:'Log out'}).click();await r.page.getByRole('dialog').waitFor();await fits(r.page);await r.page.getByRole('button',{name:'Cancel',exact:true}).click();
  r.revoke();await r.page.getByRole('button',{name:'Refresh',exact:true}).click();await r.page.getByRole('button',{name:'Enter the arena'}).waitFor();assert.match(await r.page.locator('[role=alert]').innerText(),/expired|revoked/);
  assert.deepEqual(r.errors,[]);await r.context.close();checks++;
 }
 // Existing admin page plus functional management additions.
 const admin=await browser.newContext({viewport:{width:1200,height:900}});
 await admin.addInitScript(()=>localStorage.setItem('adminToken','fixture-admin'));
 const ap=await admin.newPage(),adminErrors=[];ap.on('pageerror',e=>adminErrors.push(e.message));
 await ap.route('**/api/**',async route=>{
  const p=new URL(route.request().url()).pathname;let data={ok:true};
  if(p.endsWith('/dashboard'))data={server_now:new Date().toISOString(),rounds:[{code:'R1A',status:'DRAFT',position:0,question_active:false,rules:{r1a:{question_count:23,question_duration_ms:30000}}}],r1b_teams:{items:[]}};
  else if(p.endsWith('/r1b/timer'))data={timer_mode:'CONTINUOUS',locked:false};
  else if(p.endsWith('/submissions')||p.endsWith('/teams')||p.endsWith('/audit'))data={total:0,items:[]};
  else if(p.endsWith('/questions'))data={total:1,items:[{id:7,position:7,enabled:true,question_text:'Sample question'}]};
  else if(p.endsWith('/questions/7'))data={id:7,round_code:'R1A',position:7,question_text:'Sample question',enabled:true,correct_option_id:1,options:[{id:1,position:1,option_text:'Sample option'}]};
  await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
 });
 await ap.goto(base+'/admin/');await ap.getByRole('heading',{name:'Round controls',exact:true}).waitFor();
 await ap.getByRole('button',{name:'Questions',exact:true}).click();await ap.getByRole('button',{name:'Edit',exact:true}).click();await ap.getByRole('heading',{name:'Question editor',exact:true}).waitFor();assert.deepEqual(adminErrors,[]);checks++;await ap.close();
 console.log(`${checks} browser scenario checks passed (synthetic API fixtures; no live database).`);
}finally{await browser.close();await new Promise(r=>server.close(r));}
