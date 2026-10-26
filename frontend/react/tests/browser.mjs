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
function fixture(round='R1A',slot=1){return {contract_version:'1',snapshot_version:'fixture',snapshot_at:new Date().toISOString(),identity:{team_id:1,team_code:'TEAM01',team_name:'VECTOR FOUR',session_id:'fixture-session',round_code:round,relay_slot:slot,heartbeat_interval_seconds:15},round:{code:round,status:'LIVE'},player_state:{screen:round==='R1A'?'ACTIVE_UNANSWERED':'ACTIVE',current_player:1,current_question_position:7,turn_status:'ACTIVE'},timer:{mode:round==='R1A'?'QUESTION':'CONTINUOUS',running:true,frozen:false,remaining_ms:18000,deadline:new Date(Date.now()+18000).toISOString()},score_total:4250,question:{id:7,position:7,text:'A price rises by 20%, then falls by 20%. What is the net change?',image_url:null,options:[{id:1,position:1,text:'No change'},{id:2,position:2,text:'4% decrease'},{id:3,position:3,text:'4% increase'},{id:4,position:4,text:'2% decrease'}]},own_outcome:{accepted:false,finalized:false},draft:round==='R1B'?{turn_id:'00000000-0000-4000-8000-000000000001',version:0,answer:''}:null,available_actions:round==='R1A'?['SUBMIT']:['SUBMIT','SKIP','SAVE_DRAFT']}}
async function scenario(state,{reducedMotion='no-preference',width=390}={}){
 const context=await browser.newContext({viewport:{width,height:844},isMobile:true,deviceScaleFactor:1,hasTouch:true,reducedMotion});
 await context.addInitScript(()=>{if(!sessionStorage.getItem('fixture-cleared')){localStorage.clear();sessionStorage.setItem('fixture-cleared','1')}});
 const page=await context.newPage();const errors=[],calls=[];page.on('pageerror',e=>errors.push(e.message));let revoked=false,submitted=0;
 await page.route('**/api/**',async route=>{const req=route.request(),p=new URL(req.url()).pathname;calls.push({p,method:req.method(),body:req.postDataJSON?.()});
  const fulfill=(d,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(d)});
  if(p.endsWith('/login'))return fulfill({access_token:'fixture-token'});
  if(revoked)return fulfill({detail:{code:'SESSION_INACTIVE',message:'Session expired or revoked; log in again'}},401);
  if(p==='/api/player/bootstrap'){const d=clone(state);d.snapshot_at=new Date().toISOString();return fulfill(d)}
  if(p.endsWith('/heartbeat')||p.endsWith('/departing'))return fulfill({ok:true});
  if(p.endsWith('/logout')){revoked=true;return fulfill({ok:true})}
  if(p.endsWith('/mine'))return fulfill({published:true,rank:12,score:4250,published_at:new Date().toISOString()});
  if(p==='/api/r1b/draft'&&req.method()==='PUT'){const d=req.postDataJSON();state.draft={...d,accepted_at:new Date().toISOString()};return fulfill(state.draft)}
  if(p.endsWith('/submit')){submitted++;state.own_outcome={accepted:true,finalized:true,submission_id:100,question_position:7,player_number:1,points:750,result:'CORRECT'};state.score_total=state.identity.round_code==='R1A'?4250:5000;if(state.identity.round_code==='R1A'){state.own_outcome.points=null;state.own_outcome.result=null;state.own_outcome.score_revealed=false;}state.question=null;state.available_actions=[];state.player_state.screen=state.identity.round_code==='R1A'?'ACTIVE_ACCEPTED':'WAITING_TURN';return fulfill({accepted:true,position:7})}
  return fulfill({ok:true});
 });
 await page.goto(base+'/player/');
 return {page,context,errors,calls,revoke:()=>{revoked=true},submits:()=>submitted,login:async()=>{await page.getByLabel('Team username').fill('vector',{timeout:8000}).catch(async e=>{console.log('LOGIN DEBUG',await page.locator('body').innerText(),errors);throw e});await page.getByLabel('Password',{exact:true}).fill('example-password');await page.getByRole('button',{name:'Enter the arena'}).click();await page.getByText('VECTOR FOUR',{exact:true}).waitFor();}};
}
try{
 // Real 3D opening and scroll-only credential reveal.
 const s=fixture(),a=await scenario(s);const {page}=a;
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
 s.leaderboard={published:true,automatic:true,after_question:7,total_teams:1,published_at:new Date().toISOString(),data:[{team_id:1,team_name:'VECTOR FOUR',rank:1,score:5000}],mine:{team_id:1,rank:1,score:5000}};
 await page.getByRole('button',{name:'Refresh',exact:true}).click();await page.locator('.score-reveal').waitFor();
 await page.waitForTimeout(600);if(shots)await page.screenshot({path:shots+'/04-score-reveal.png'});
 await page.getByRole('button',{name:'Continue'}).click();await page.getByRole('heading',{name:'Leaderboard',exact:true}).waitFor();if(shots)await page.screenshot({path:shots+'/05-leaderboard.png'});assert.match(await page.locator('.strip-score').innerText(),/5,000/);checks++;await page.reload();await page.getByRole('heading',{name:'Leaderboard',exact:true}).waitFor();assert.equal(await page.locator('.score-reveal').count(),0);checks++;
 s.player_state.screen='ACTIVE_UNANSWERED';s.player_state.current_question_position=8;s.question={id:8,position:8,text:'The organiser revealed question eight.',options:[{id:8,position:1,text:'A new option'}]};s.own_outcome={accepted:false,finalized:false};s.available_actions=['SUBMIT'];s.timer.running=true;s.timer.remaining_ms=30000;await page.getByRole('button',{name:'Refresh',exact:true}).click();await page.getByRole('heading',{name:'The organiser revealed question eight.'}).waitFor();assert.equal(await page.getByRole('heading',{name:'Leaderboard',exact:true}).count(),0);checks++;
 a.revoke();await page.getByRole('button',{name:'Refresh',exact:true}).click();await page.getByRole('button',{name:'Enter the arena'}).waitFor();assert.match(await page.locator('[role=alert]').innerText(),/expired|revoked/);checks++;assert.deepEqual(a.errors,[]);await a.page.close();
 // Polling never resets typing; autosave payloads match the server contract.
 const r=fixture('R1B');r.question.position=1;r.player_state.current_question_position=1;r.question.text='Find the numerical value of $2^5 + 10$.';r.timer.remaining_ms=28*60000;
 const b=await scenario(r);await b.login();const input=b.page.getByLabel('Your numerical answer');await input.fill('-12.75');await b.page.getByText('Saved to server',{exact:true}).waitFor();await b.page.waitForTimeout(1700);assert.equal(await input.inputValue(),'-12.75');assert(b.calls.some(c=>c.method==='PUT'&&c.body.answer==='-12.75'&&c.body.turn_id===r.draft.turn_id));checks++;
 await b.page.reload();await b.page.getByLabel('Your numerical answer').waitFor();assert.equal(await b.page.getByLabel('Your numerical answer').inputValue(),'-12.75');checks++;
 await b.page.getByLabel('Your numerical answer').fill('42');await b.page.getByRole('button',{name:'Submit answer',exact:true}).click();await b.page.locator('.score-reveal').waitFor();assert.equal(b.submits(),1);assert.equal(b.calls.find(c=>c.p==='/api/r1b/submit').body.answer,'42');checks++;
 assert.deepEqual(b.errors,[]);await b.page.close();
 // Inactive slot and narrow viewport: no question/draft controls.
 const waiting=fixture('R1B',2);waiting.question=null;waiting.draft=null;waiting.available_actions=[];waiting.player_state.screen='WAITING_TURN';
 const c=await scenario(waiting,{width:320,reducedMotion:'reduce'});await c.login();await c.page.getByRole('heading',{name:'Your moment is coming.'}).waitFor();assert.equal(await c.page.getByRole('button',{name:'Submit answer',exact:true}).count(),0);assert(await c.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));checks++;
 assert.deepEqual(c.errors,[]);await c.page.close();
 // Existing admin page plus functional management additions.
 const admin=await browser.newContext({viewport:{width:1200,height:900}});
 await admin.addInitScript(()=>localStorage.setItem('adminToken','fixture-admin'));
 const ap=await admin.newPage(),adminErrors=[];ap.on('pageerror',e=>adminErrors.push(e.message));
 await ap.route('**/api/**',async route=>{
  const p=new URL(route.request().url()).pathname;let data={ok:true};
  if(p.endsWith('/dashboard'))data={server_now:new Date().toISOString(),rounds:[{code:'R1A',status:'DRAFT',position:0,question_active:false}],r1b_teams:{items:[]}};
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
