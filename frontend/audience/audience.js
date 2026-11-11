// Public display only: one endpoint, no authentication/session/admin requests.
const $ = id => document.getElementById(id);
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
let latest = null, received = 0, snapshotAt = -Infinity, failures = 0;
let lastSlide = '', lastPublication = null, poll, clock, controller, disposed = false, generation = 0;
let renderMath = null, unmount = null, stageMove = null;
const movements = new Map();
const controls = {current:{pointer:{x:0,y:0},drag:0,ripple:-100,focused:false,progress:0,reducedMotion:reduced.matches}};
const escape = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const rich = value => renderMath ? renderMath(String(value ?? '')) : escape(value);
const number = n => new Intl.NumberFormat().format(n);
// Remove redundant fractional zeros without ever converting the answer to a float.
const numeric = value => {const s=String(value);return /^-?\d+\.\d+$/.test(s)?s.replace(/0+$/,'').replace(/\.$/,''):s;};

function cancelMoves(){movements.forEach(a=>a.cancel());movements.clear();}
function renderRows(board, animate){
 const target=$('leaderboard'),existing=new Map([...target.children].map(el=>[el.dataset.id,el]));
 const positions=new Map([...existing].map(([id,el])=>[id,el.getBoundingClientRect()]));
 if(animate)cancelMoves();
 const combined=board?.round_code==='R1'||board?.scope?.round_code==='R1';
 const rows=combined?(board?.data || []):(board?.data || []).slice(0,16),nodes=[];
 for(const entry of rows){
  const id=String(entry.team_id),row=existing.get(id)||document.createElement('div');row.className='leader-row';row.dataset.id=id;
  row.innerHTML=`<span class="rank">#${escape(entry.rank)}</span><span class="team-name" title="${escape(entry.team_name)}">${escape(entry.team_name)}</span>${combined?`<span class="subscore">${escape(number(entry.r1a_score))}</span><span class="subscore">${escape(number(entry.r1b_score))}</span>`:''}<strong class="score">${escape(number(entry.score))}</strong>`;
  nodes.push(row);
 }
 if(nodes.length!==target.children.length||nodes.some((node,i)=>node!==target.children[i]))target.replaceChildren(...nodes);
 for(const [id] of existing)if(!rows.some(row=>String(row.team_id)===id)){movements.get(id)?.cancel();movements.delete(id);}
 if(animate&&!reduced.matches)for(const node of nodes){
  const before=positions.get(node.dataset.id);if(!before)continue;const after=node.getBoundingClientRect(),x=before.left-after.left,y=before.top-after.top;
  if(!x&&!y)continue;const a=node.animate([{transform:`translate(${x}px,${y}px)`},{transform:'none'}],{duration:700,easing:'cubic-bezier(.2,.75,.2,1)'});
  movements.set(node.dataset.id,a);a.onfinish=()=>{if(movements.get(node.dataset.id)===a)movements.delete(node.dataset.id);};
 }
}

function apply(data){
 if(data.version!==1||typeof data.view!=='string')throw Error('Unsupported public presentation');
 const stamp=Date.parse(data.snapshot_at);if(!Number.isFinite(stamp)||stamp<snapshotAt)return;
 snapshotAt=stamp;latest=data;received=performance.now();
 const oldSlide=lastSlide,key=JSON.stringify([data.view,data.round_code,data.question,data.answer]);
 document.body.dataset.view=data.view;
 $('round-label').textContent=data.round_code==='R1'?'ROUND ONE · FINAL':data.round_code==='R1B'?'MATH RALLY · R1B':'RAPID FIRE · R1A';
 $('phase-label').textContent=data.view.replaceAll('_',' ');$('paused').hidden=!data.paused;
 controls.current.reducedMotion=reduced.matches||data.paused;
 window.dispatchEvent(new Event('atrium:visual-mode'));
 const q=data.question,answer=data.answer,validAnswer=['ANSWER','ANSWER_LEADERBOARD','REVIEW_ANSWER'].includes(data.view)&&answer&&q&&answer.question_id===q.id&&answer.position===q.position;
 $('waiting').hidden=!!q||data.view==='LEADERBOARD';$('question').hidden=!q;
 const showBoard=['ANSWER_LEADERBOARD','LEADERBOARD'].includes(data.view);
 $('standings').hidden=!showBoard;
 document.body.dataset.board=String(showBoard);
 if(q&&key!==oldSlide){
  $('question-label').textContent=`${data.view==='PREVIEW'?'UPCOMING · ':''}QUESTION ${q.position}${data.question_count?' / '+data.question_count:''}`;
  $('question-kind').textContent=data.view==='PREVIEW'?'PREVIEW · NOT STARTED':data.view.includes('ANSWER')?'RELEASED ANSWER':q.type==='NUMERIC'?'MATH RALLY':'WIZTIME';
  $('prompt').innerHTML=rich(q.text);const image=$('question-image');image.hidden=!q.image_url;
  if(q.image_url)image.src=q.image_url;else image.removeAttribute('src');
  $('options').innerHTML=(q.options||[]).map((option,i)=>{
   const correct=validAnswer&&answer.type==='MCQ'&&answer.correct_option_id===option.id;
   return `<div class="option${correct?' correct':''}" data-option-id="${escape(option.id)}"><span class="option-key">${String.fromCharCode(65+i)}</span><div>${rich(option.text)}${option.image_url?`<img src="${escape(option.image_url)}" alt="Option ${String.fromCharCode(65+i)} diagram">`:''}</div>${correct?'<strong class="correct-mark">✓ CORRECT</strong>':''}</div>`;
  }).join('');
  $('answer').hidden=!data.view.includes('ANSWER');
  $('answer').innerHTML=validAnswer?(answer.type==='NUMERIC'?`<span class="eyebrow">CORRECT ANSWER</span><strong class="numeric-answer" data-exact-value="${escape(answer.value)}">${escape(numeric(answer.value))}</strong>`:'<span class="eyebrow">RELEASED SOLUTION</span><p>The correct option is highlighted above.</p>'):'<p>Answer unavailable. Waiting for authorized content.</p>';
 }else if(!q){
  $('prompt').replaceChildren();$('options').replaceChildren();$('answer').replaceChildren();$('answer').hidden=true;$('question-image').hidden=true;$('question-image').removeAttribute('src');
 }
 const copy={STANDBY:['A moment before the extraordinary.','Waiting for the next mathematical encounter.'],RELAY_STANDBY:['Four minds. One journey.','Math Rally is in progress. Pass the device. Keep the momentum.'],REVIEW_READY:['The journey, revisited.','Math Rally has ended. Waiting for the question review.'],THANK_YOU:['Beyond the final answer.','Thank you for being part of Delta X.'],CONTENT_UNAVAILABLE:['One moment.','Waiting for authorized question content.'],RESULTS_PENDING:['The final pattern is forming.','Waiting for the authorized standings to be published.'],LEADERBOARD:['The numbers tell a story.','Published standings.']};
 const [title,description]=copy[data.view]||copy.STANDBY;$('waiting-title').textContent=title;$('waiting-copy').textContent=description;
 const board=data.leaderboard,publication=board?.publication_id||null;
 const combined=board?.round_code==='R1'||board?.scope?.round_code==='R1';
 document.body.dataset.combined=String(combined);
 $('board-title').textContent=combined?'Final Round 1 standings':'Where the numbers stand.';
 $('board-columns').hidden=!combined;
 $('publication').textContent=board?`${combined?'R1(a) + R1(b) · All '+board.data.length+' participating teams':board.final?'Final publication':'After question '+board.after_question} · ${new Date(board.published_at).toLocaleTimeString()}${combined&&board.ties?.length?' · Equal scores share a rank; competition tie policy unresolved.':''}${combined&&board.qualification?.status==='BLOCKED_CUTOFF_TIE'?' · R2 qualification pending the official cutoff tie policy.':''}`:'Standings have not yet been published.';
 renderRows(showBoard?board:null,lastPublication!==null&&publication!==lastPublication);lastPublication=publication;
 if(key!==oldSlide){cancelMoves();stageMove?.cancel();if(!reduced.matches&&oldSlide){const stage=q?$('question'):$('waiting');stageMove=stage.animate([{transform:'translateY(9px)'},{transform:'none'}],{duration:550,easing:'ease-out'});}lastSlide=key;}
 drawClock();
}
function drawClock(){
 if(latest?.remaining_ms==null){$('timer').textContent='—';return;}
 const ms=Math.max(0,latest.remaining_ms-(latest.paused?0:performance.now()-received));
 const s=Math.ceil(ms/1000);$('timer').textContent=`${String(Math.floor(s/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;
}
async function fetchState(epoch){
 try{
  const request=new AbortController();controller=request;const timeout=setTimeout(()=>request.abort(),5000);
  let response;try{response=await fetch('/api/public/audience',{cache:'no-store',signal:request.signal});}finally{clearTimeout(timeout);}
  if(!response.ok)throw Error('Public state unavailable');const data=await response.json();if(disposed||epoch!==generation)return;
  apply(data);failures=0;$('connection').textContent='LIVE';$('connection').dataset.state='live';$('sync').textContent='SYNC '+new Date().toLocaleTimeString();
 }catch{if(!disposed&&epoch===generation&&++failures>=2){$('connection').textContent='RECONNECTING · LAST RECEIVED CONTENT';$('connection').dataset.state='stale';}}
 finally{if(!disposed&&epoch===generation)poll=setTimeout(()=>fetchState(epoch),500);}
}
async function start(){
 const epoch=++generation;clearTimeout(poll);clearInterval(clock);controller?.abort();unmount?.();unmount=null;
 disposed=false;if(latest){$('connection').textContent='RECONNECTING · LAST RECEIVED CONTENT';$('connection').dataset.state='stale';}fetchState(epoch);clock=setInterval(drawClock,200);
 try{const module=await import('/audience/assets/scene.js');if(disposed||epoch!==generation)return;renderMath=module.richText;if(new URLSearchParams(location.search).get('render')!=='fallback')unmount=module.mountAudience($('atrium-world'),controls);else document.body.dataset.scene='fallback';if(latest){lastSlide='';apply(latest);}}
 catch{if(!disposed&&epoch===generation)document.body.dataset.scene='fallback';}
}
function stop(){disposed=true;generation++;clearTimeout(poll);clearInterval(clock);controller?.abort();cancelMoves();stageMove?.cancel();unmount?.();unmount=null;document.body.dataset.scene='stopped';}
reduced.addEventListener('change',()=>{cancelMoves();stageMove?.cancel();controls.current.reducedMotion=reduced.matches||!!latest?.paused;window.dispatchEvent(new Event('atrium:visual-mode'));});
addEventListener('resize',cancelMoves);addEventListener('pagehide',stop);addEventListener('pageshow',e=>{if(e.persisted)start();});
start();
window.__audienceTest={apply,stop};
