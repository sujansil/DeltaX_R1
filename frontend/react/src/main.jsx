import React,{useState,useEffect,useRef,useCallback,lazy,Suspense} from 'react';
import {createRoot} from 'react-dom/client';
import katex from 'katex';
import {api,autoRevealSeconds,formatScore,formatTime,outcomeKey,revealAllowed} from './api.js';
import {DraftQueue} from './draft-queue.mjs';
import './style.css';
import CinematicLogin,{LoginPresentationBoundary} from './cinematic/CinematicLogin.jsx';
const MathScene=lazy(()=>import('./MathScene.jsx'));
const TOKEN='r1token';
const store={get:(key)=>{try{return sessionStorage.getItem(key)}catch{return null}},set:(key,value)=>{try{sessionStorage.setItem(key,value)}catch{}}};
function useReduced(){const [value,set]=useState(()=>matchMedia('(prefers-reduced-motion: reduce)').matches);useEffect(()=>{const m=matchMedia('(prefers-reduced-motion: reduce)'),fn=()=>set(m.matches);m.addEventListener('change',fn);return()=>m.removeEventListener('change',fn)},[]);return value;}
function Brand({small=false}){return <span className={'brand '+(small?'brand-small':'')}>DELTA <span className="brand-x">X</span></span>}
function RichText({text='',className=''}) {
 const parts=String(text).split(/(\$\$[\s\S]+?\$\$|\$[^$\n]+?\$|\\\([\s\S]+?\\\)|\\\[[\s\S]+?\\\])/g);
 return <span className={'rich-text '+className}>{parts.map((part,i)=>{
  const display=part.startsWith('$$')||part.startsWith('\\['), math=display||part.startsWith('$')||part.startsWith('\\(');
  if(!math)return <React.Fragment key={i}>{part}</React.Fragment>;
  const source=part.slice(display||part.startsWith('\\')?2:1,display||part.startsWith('\\')?-2:-1);
  return <span key={i} dangerouslySetInnerHTML={{__html:katex.renderToString(source,{throwOnError:false,displayMode:display,trust:false,strict:'ignore'})}}/>;
 })}</span>;
}
function Diagram({url,option=false}){if(!url)return null;return <img className={option?'option-image':'question-image'} src={url} alt={option?'Answer option diagram':'Question diagram'} loading="eager"/>;}
function Login({onLogin,notice,reduced}) {
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[activation,setActivation]=useState(false),[show,setShow]=useState(false),[success,setSuccess]=useState('');
 const classic=new URLSearchParams(window.location.search).get('login')==='classic';
 async function submit(e){e.preventDefault();if(busy)return;setBusy(true);setError('');setSuccess('');const f=new FormData(e.currentTarget);
 try{if(activation){await api('/api/auth/team/set-password',{method:'POST',body:{username:f.get('username').trim(),activation_token:f.get('activation'),new_password:f.get('password')}});setActivation(false);setSuccess('Password created. Sign in with your team username.');}
 else{const d=await api('/api/auth/team/login',{method:'POST',body:{username:f.get('username').trim(),password:f.get('password')}});onLogin(d.access_token);}}
 catch(e){setError(e.message)}finally{setBusy(false)}}
 const credentials=<section className="credential-section" id="credentials"><div className="credential-inner"><div className="section-number">{classic?'01 / ENTER':activation?'FIRST TIME / BEGIN HERE':'LET CURIOSITY LEAD'}</div><h1>{activation?'Activate your team.':classic?'Your team. Your next move.':<>Four minds.<br/><em>One clock.</em></>}</h1><p className="muted">{activation?'Use the activation details shared by the organisers.':'Sign in on your team device. In Math Rally, pass this same device between all four players.'}</p>
   {(notice||error)&&<div className="notice error" role="alert">{error||notice}</div>}{success&&<div className="notice" role="status">{success}</div>}
   <form onSubmit={submit} key={activation?'activation':'login'}>
    {activation?<><label>Team username<input name="username" required maxLength={120} autoComplete="username" autoCapitalize="none" spellCheck="false" placeholder="Your assigned username"/></label><label>Activation code<input name="activation" required maxLength={256} autoComplete="one-time-code" autoCapitalize="characters" spellCheck="false" placeholder="XXXX-XXXX"/></label></>:<label>Team username<input name="username" required maxLength={120} autoComplete="username" autoCapitalize="none" spellCheck="false" placeholder="Your assigned username"/></label>}
    <label>{activation?'Choose a password':'Password'}<span className="password-wrap"><input aria-label={activation?'Choose a password':'Password'} name="password" required type={show?'text':'password'} minLength={activation?8:1} maxLength={activation?128:256} autoComplete={activation?'new-password':'current-password'} placeholder="••••••••"/><button type="button" className="password-toggle" onClick={()=>setShow(!show)} aria-label={show?'Hide password':'Show password'}>{show?'Hide':'Show'}</button></span></label>
    <button className="primary full" disabled={busy}>{busy?'Connecting…':activation?'Create password':'Enter the arena'}<span aria-hidden="true">↗</span></button>
   </form>
   <button className="text-button activation-toggle" onClick={()=>{setActivation(!activation);setError('')}}>{activation?'Back to sign in':'First time? Activate your team'}</button>
   <div className="login-footnote"><span>WIZTIME / MATH RALLY</span><span>Think clearly. Play together.</span></div>
  </div></section>;
 const original=<main className="login-page"><a className="skip-link" href="#credentials">Skip to team login</a>
  <section className="login-hero" aria-label="Delta X mathematical arena"><div className="hero-top"><span className="eyebrow">A MATHEMATICAL ENCOUNTER</span><span className="edition">ROUND 01</span></div>
   <Suspense fallback={<div className="scene-placeholder"/>}><MathScene reduced={reduced}/></Suspense>
   <div className="hero-wordmark"><Brand/><p>Small changes. Extraordinary outcomes.</p></div>
   <a className="scroll-cue" href="#credentials">Scroll to enter<span aria-hidden="true">↓</span></a>
  </section>{credentials}
 </main>;
 if(classic)return original;
 return <LoginPresentationBoundary fallback={original}><CinematicLogin reduced={reduced} busy={busy} activation={activation}>{credentials}</CinematicLogin></LoginPresentationBoundary>;
}
function useClock(snapshot){const [tick,setTick]=useState(0);useEffect(()=>{if(!snapshot?.timer.running)return;const id=setInterval(()=>setTick(x=>x+1),150);return()=>clearInterval(id)},[snapshot?.timer.running]);
 const t=snapshot?.timer;return t?Math.max(0,t.remaining_ms-(t.running?performance.now()-snapshot._received+(snapshot._transit||0):0)):0;
}
function useAutoRevealClock(snapshot){const phase=snapshot?.r1a_phase,[tick,setTick]=useState(0);useEffect(()=>{if(!phase?.next_reveal_at||phase.frozen)return;const id=setInterval(()=>setTick(x=>x+1),150);return()=>clearInterval(id)},[phase?.next_reveal_at,phase?.frozen]);const elapsed=phase?.frozen?0:performance.now()-snapshot._received+(snapshot._transit||0);return autoRevealSeconds(phase,elapsed);}
function Timer({state,left}){const timer=state.timer,low=timer.running&&left<5000;return <div className={'timer '+(low?'time-low':'')}><span className="eyebrow">{timer.frozen?'CLOCK FROZEN':timer.mode==='QUESTION'?'TIME REMAINING':'TEAM TIME'}</span><span className="timer-value" role="timer" aria-label={`${formatTime(left)} remaining`}>{formatTime(left)}</span></div>}
function RelaySteps({state}){if(state.identity.round_code!=='R1B')return null;const p=state.player_state.current_player,count=state.rules.r1b.turn_count;return <div className="relay-steps" aria-label={'Current relay player '+p}>{Array.from({length:count},(_,i)=>i+1).map(n=><span key={n} className={n===p?'current':n<p?'complete':''}><b>{n<p?'✓':String(n).padStart(2,'0')}</b><small>{`PLAYER ${n}`}</small></span>)}</div>}
function NumericAnswer({state,request,onSubmit,busy,onError}){
 const initial=state.draft, [answer,setAnswer]=useState(initial?.answer||''),[status,setStatus]=useState(initial?.version?'Draft restored · saved':'Your draft saves automatically'),[conflict,setConflict]=useState(false);
 const queue=useRef(null),enabled=state.available_actions.includes('SAVE_DRAFT');
 useEffect(()=>{const q=new DraftQueue({turn:initial.turn_id,version:initial.version,answer:initial.answer,save:body=>request('/api/r1b/draft',{method:'PUT',body}),notify:msg=>{setStatus(msg);setConflict(q.conflict)}});queue.current=q;return()=>q.stop()},[initial.turn_id]);
 useEffect(()=>{queue.current?.setEnabled(enabled&&!busy)},[enabled,busy]);
 function change(e){setAnswer(e.target.value);queue.current?.edit(e.target.value)}
 async function submit(e){e.preventDefault();if(!answer.trim()||!Number.isFinite(Number(answer))){onError('Enter a finite numerical answer, or choose Skip.');return;}await onSubmit(answer,false)}
 return <form className="numeric-form" onSubmit={submit}><label htmlFor="numeric-answer">Your numerical answer</label><input id="numeric-answer" name="answer" type="text" inputMode="decimal" autoComplete="off" spellCheck="false" maxLength={500} placeholder="e.g. −12.75" value={answer} onChange={change} disabled={!enabled||busy||conflict}/>
  <div className="save-state" role="status"><span className={'status-dot '+(status.includes('Saved')||status.includes('saved')?'saved':'')}/>{status}</div>
  {conflict&&<button type="button" className="secondary full" onClick={()=>onError('Use Refresh above to restore the server draft. Copy any unsaved text first.')}>Draft conflict · see recovery instructions</button>}
  <div className="answer-actions"><button className="primary" disabled={!enabled||busy||conflict} type="submit">{busy?'Confirming…':'Submit answer'}<span aria-hidden="true">↗</span></button><button className="text-button" disabled={!enabled||busy} type="button" onClick={()=>onSubmit(answer,true)}>Skip this question</button></div>
  <p className="micro-copy">{state.rules.r1b.show_results_after_each_question?'At timeout, the last draft received by the server counts. Empty answers score zero; non-empty invalid answers count as wrong.':'At timeout, the last draft received by the server is used.'}</p>
 </form>
}
function Question({state,left,request,mutate,busy,onError}) {
 const question=state.question,[selected,setSelected]=useState(null),[confirmSkip,setConfirmSkip]=useState(null);
 const numeric=state.identity.round_code==='R1B';
 async function submitNumeric(answer,skip){if(skip){setConfirmSkip(answer);return;}await mutate('/api/r1b/submit',{turn_id:state.draft.turn_id,answer,skip:false});}
 return <section className="question-panel" key={question.id}>
  <div className="question-meta"><span className="eyebrow">{numeric?'MATH RALLY':'WIZTIME'} <span className="dim">/ {String(question.position).padStart(2,'0')} OF {String(numeric?state.rules.r1b.turn_count:state.rules.r1a.question_count).padStart(2,'0')}</span></span><Timer state={state} left={left}/></div>
  <div className="question-body"><h1><RichText text={question.text}/></h1><Diagram url={question.image_url}/></div>
  {numeric?<NumericAnswer key={state.draft?.turn_id} state={state} request={request} busy={busy} onError={onError} onSubmit={submitNumeric}/>:<form className="mcq-form" onSubmit={e=>{e.preventDefault();if(selected===null){onError('Choose an option first.');return;}mutate('/api/r1a/submit',{option_id:selected});}}>
   <fieldset disabled={busy||left<=0}><legend className="sr-only">Choose one answer</legend>{question.options?.map((o,i)=><label key={o.id} className={'answer-option '+(selected===o.id?'selected':'')}><input aria-label={`${String.fromCharCode(65+i)}: ${o.text||'Image option'}`} type="radio" name="option" checked={selected===o.id} onChange={()=>setSelected(o.id)} value={o.id}/><span className="option-letter">{String.fromCharCode(65+i)}</span><span className="option-content"><RichText text={o.text}/><Diagram url={o.image_url} option/></span><span className="option-check" aria-hidden="true">{selected===o.id?'✓':''}</span></label>)}</fieldset>
   <button className="primary full submit-answer" disabled={busy||selected===null||left<=0}>{busy?'Confirming…':'Lock answer'}<span aria-hidden="true">↗</span></button><p className="micro-copy">One answer. No revisiting. Your answer must reach the server in time.</p>
  </form>}
  {confirmSkip!==null&&<Confirm title="Skip this question?" text={state.rules.r1b.show_results_after_each_question?'This ends your turn permanently and scores zero.':'This ends your turn permanently.'} onCancel={()=>setConfirmSkip(null)} onConfirm={()=>{const a=confirmSkip;setConfirmSkip(null);mutate('/api/r1b/submit',{turn_id:state.draft.turn_id,answer:a,skip:true})}}/>}
 </section>;
}
const screenCopy={
 NOT_STARTED:['Before the first move.','Waiting for the organisers to open your round.'],
 ACTIVE_ACCEPTED:['Answer locked.','Your answer is safely recorded. Results appear when this question closes.'],
 BETWEEN_QUESTIONS:['A moment between moves.','The finalized leaderboard stays on screen until the next question opens.'],
 WAITING_TURN:['Pass the team device.','The next player continues on this same device.'],
 HANDOVER_READY:['Pass to the next player.','The next question stays hidden until that player presses READY.'],
 PAUSED:['A pause in the pattern.','The organiser has paused this round. Your clock is frozen.'],
 FINISHED:['Relay complete.','Please wait.'],
 EXPIRED:['Time has run its course.','Your budget is exhausted. Any eligible saved answer is finalized by the server.'],
 ENDED:['This chapter is complete.','The round has ended. Recorded results are preserved.']};
function Waiting({state,left,mutate,busy}) {const screen=state.player_state.screen,copy=[...(screenCopy[screen]||['Synchronising…','Checking the current state.'])],actions=state.available_actions;
 if(state.identity.round_code==='R1A'&&screen==='ACTIVE_ACCEPTED'&&!state.rules.r1a.show_results_after_each_question)copy[1]='Your answer is safely recorded.';
 return <section className="waiting-panel"><div className="waiting-art" aria-hidden="true"><svg viewBox="0 0 300 140">{Array.from({length:8},(_,i)=><path key={i} d={`M 0 ${70+i*5} Q 75 ${-30+i*8} 150 70 T 300 ${70-i*4}`}/>)}</svg></div>
 <span className="eyebrow">{state.identity.round_code==='R1A'?'WIZTIME':'MATH RALLY'} / {screen==='PAUSED'?'PAUSED':state.player_state.turn_status||'ROUND 01'}</span><h1>{copy[0]}</h1><p>{copy[1]}</p>
 {state.identity.round_code==='R1B'&&state.own_outcome.finalized&&!state.own_outcome.score_revealed&&<p className="ready-note">Answer recorded.</p>}
 {state.identity.round_code==='R1B'&&<RelaySteps state={state}/>}
 {screen==='ACTIVE_ACCEPTED'&&<Timer state={state} left={left}/>}
 {state.identity.round_code==='R1B'&&!['NOT_STARTED','FINISHED','ENDED','EXPIRED'].includes(screen)&&<><Timer state={state} left={left}/><p className="micro-copy">{state.timer.mode==='CONTINUOUS'?'Continuous mode: handovers use team time unless globally paused.':'Handover pause: time runs only while a question is active.'}</p></>}
 {actions.includes('START')&&<button className="primary full" disabled={busy} onClick={()=>mutate('/api/r1b/start',{})}>Prepare relay <span aria-hidden="true">↗</span></button>}
 {actions.includes('READY')&&<><p className="ready-note">Player {state.player_state.current_player}: take the shared device. READY reveals your question{!state.player_state.clock_started_at?` and starts the ${formatTime(state.rules.r1b.total_answering_budget_ms)} clock`:state.timer.frozen?' and resumes the clock':''}.</p><button className="primary full" disabled={busy} onClick={()=>mutate('/api/r1b/ready',{})}>{busy?'Opening…':`Player ${state.player_state.current_player} · READY`}<span aria-hidden="true">↗</span></button></>}
 {screen==='ENDED'&&<p className="micro-copy">Moving to R1B? Log out, then sign in again for a new round-scoped session.</p>}
 </section>;
}
function AutoBetween({state}){const seconds=useAutoRevealClock(state);if(seconds===null)return <section className="waiting-panel"><h1>Preparing next question…</h1></section>;return <section className="waiting-panel"><h1>{state.r1a_phase.frozen?`Paused · Next question starts in ${seconds}s`:`Next question starts in ${seconds}s`}</h1></section>}
function QuestionLeaderboard({state}) {
 const board=state.leaderboard;
 const r1b=state.identity.round_code==='R1B';
 return <section className="question-leaderboard" aria-label="Question leaderboard">
  <span className="eyebrow">{r1b?'MATH RALLY / PUBLISHED RESULTS':'WIZTIME / QUESTION CLOSED'}</span>
  <h1>{r1b?'R1B results':'Leaderboard'}</h1>
  <p className="board-intro">{board?.published?(r1b?'Immutable administrator publication':`After question ${String(board.after_question).padStart(2,'0')}`):'Finalising the results…'}</p>
  {board?.published&&<>
   <div className="board-table-wrap" tabIndex="0" aria-label={`Published leaderboard, ${board.data.length} teams`}><table><thead><tr><th scope="col">Rank</th><th scope="col">Team</th><th scope="col">Score</th></tr></thead><tbody>{board.data.map(row=><tr key={row.team_id} className={row.team_id===state.identity.team_id?'my-team':''}><td>#{row.rank}</td><th scope="row">{row.team_name}{row.team_id===state.identity.team_id&&<small>You</small>}</th><td>{formatScore(row.score)}</td></tr>)}</tbody></table></div>
   {!board.data.length&&<p className="micro-copy">No enrolled teams in this snapshot.</p>}
   {board.mine&&!board.data.some(row=>row.team_id===state.identity.team_id)&&<div className="own-standing"><span>Your team · #{board.mine.rank}</span><strong>{formatScore(board.mine.score)} pts</strong></div>}
   <p className="micro-copy">{board.data.length>5?'Scroll inside the table to see lower ranks. ':''}{board.total_teams>16?'Top 16 shown. ':board.data.length?`All ${board.data.length} ranked teams shown. `:''}{r1b?'Only an administrator publication changes these values.':'Updated automatically when the question closes.'}</p>
  </>}
  <p className="next-question-note">{r1b?'These scores are from the latest published R1B snapshot.':board?.final?'WizTime is complete. Your recorded results are saved.':state.round.status==='ENDED'?'WizTime is complete. Your recorded results are saved.':'The next question opens when the organiser reveals it.'}</p>
 </section>;
}
function Confirm({title,text,onCancel,onConfirm}){const cancel=useRef(null);useEffect(()=>{cancel.current?.focus();const fn=e=>{if(e.key==='Escape')onCancel();};window.addEventListener('keydown',fn);return()=>window.removeEventListener('keydown',fn)},[]);
 return <div className="modal-backdrop" onClick={onCancel}><section className="confirm-dialog" role="dialog" aria-modal="true" aria-label={title} onClick={e=>e.stopPropagation()} onKeyDown={e=>{if(e.key==='Tab'){const b=e.currentTarget.querySelectorAll('button');if(e.shiftKey&&document.activeElement===b[0]){e.preventDefault();b[b.length-1].focus()}else if(!e.shiftKey&&document.activeElement===b[b.length-1]){e.preventDefault();b[0].focus()}}}}><h2>{title}</h2><p>{text}</p><div className="dialog-actions"><button ref={cancel} className="secondary" onClick={onCancel}>Cancel</button><button className="primary" onClick={onConfirm}>Confirm</button></div></section></div>
}
function Reveal({event,reduced,onClose}){
 const [count,setCount]=useState(event.before);const closeRef=useRef(null);
 useEffect(()=>{closeRef.current?.focus();const start=performance.now();let frame;const animate=now=>{const p=Math.min(1,(now-start)/950);setCount(Math.round(event.before+(event.total-event.before)*(1-Math.pow(1-p,3))));if(p<1)frame=requestAnimationFrame(animate)};if(reduced)setCount(event.total);else frame=requestAnimationFrame(animate);const timer=setTimeout(onClose,reduced?1300:2700);return()=>{cancelAnimationFrame(frame);clearTimeout(timer)}},[event.key]);
 const label=event.result==='SKIPPED'?'QUESTION SKIPPED':event.result==='UNANSWERED'?'NO ANSWER RECORDED':event.points<0?'POINTS DEDUCTED':'POINTS EARNED';
 return <section className="score-reveal" role="dialog" aria-modal="true" aria-label={`${label}: ${event.points}. Team total ${event.total}`} onKeyDown={e=>{if(e.key==='Escape')onClose();if(e.key==='Tab')e.preventDefault()}}>
 <div className="reveal-curves" aria-hidden="true"><svg viewBox="0 0 500 620">{Array.from({length:18},(_,i)=><ellipse key={i} cx="250" cy="290" rx={110+i*4} ry={90+i*6} transform={`rotate(${i*6} 250 290)`}/>)}</svg></div>
 <div className="reveal-content"><span className="eyebrow">{label}</span><div className={'earned '+(event.points<0?'negative':'')}>{event.points>0?'+':''}{formatScore(event.points)}</div><div className="reveal-divider"/><span className="eyebrow">ROUND TOTAL</span><div className="reveal-total">{formatScore(count)}</div><p>One question closer.</p></div><button ref={closeRef} className="text-button reveal-close" onClick={onClose}>Continue <span aria-hidden="true">→</span></button>
 </section>
}
function App(){
 const reduced=useReduced(),[token,setToken]=useState(()=>localStorage.getItem(TOKEN)),[snapshot,setSnapshot]=useState(null),[standing,setStanding]=useState(null),[error,setError]=useState(''),[notice,setNotice]=useState(''),[network,setNetwork]=useState('connecting'),[busy,setBusy]=useState(false),[logoutConfirm,setLogoutConfirm]=useState(false),[reveal,setReveal]=useState(null),[restoreKey,setRestoreKey]=useState(0);
 const restoreRequested=useRef(false);
 const current=useRef(null),refresh=useRef(()=>{}),busyRef=useRef(false),candidate=useRef(null),first=useRef(true),aliveToken=useRef(token),requestRef=useRef(null);
 aliveToken.current=token;
 const signOut=useCallback((message='')=>{localStorage.removeItem(TOKEN);localStorage.removeItem('r1team');aliveToken.current=null;setToken(null);setSnapshot(null);current.current=null;setStanding(null);setReveal(null);setNotice(message);setError('');setNetwork('connecting');candidate.current=null;first.current=true;busyRef.current=false;setBusy(false);window.scrollTo(0,0)},[]);
 const request=useCallback(async(path,options={})=>{const t=aliveToken.current;try{return await api(path,{...options,token:t})}catch(e){if(e.status===401&&t===aliveToken.current)signOut('Your session expired or was revoked. Sign in again.');throw e}},[signOut]);requestRef.current=request;
 function accept(data,sent){if(!aliveToken.current)return;const received=performance.now();data._received=received;data._transit=Math.max(0,(received-sent)/2);const previous=current.current;if(previous&&Date.parse(data.snapshot_at)<Date.parse(previous.snapshot_at))return;
  const key=outcomeKey(data),ledger='dx-seen:'+data.identity.session_id;
  let seen=[];try{seen=JSON.parse(store.get(ledger)||'[]')}catch{}
  if(first.current){first.current=false;if(key&&!seen.includes(key)){seen.push(key);store.set(ledger,JSON.stringify(seen));}}
  else if(key&&!seen.includes(key)){candidate.current={key,points:data.own_outcome.points||0,total:data.score_total??null,result:data.own_outcome.result,position:data.own_outcome.question_position};}
  if(candidate.current&&revealAllowed(data)){
   const c=candidate.current;candidate.current=null;seen.push(c.key);store.set(ledger,JSON.stringify(seen.slice(-40)));
   if(c.total!==null)setReveal({...c,before:c.total-c.points});
  }
  // A reveal must never mask a new live question or an actionable handover.
  if(data.question||(data.available_actions.includes('READY')&&!(data.identity.round_code==='R1B'&&data.rules.r1b.show_results_after_each_question)))setReveal(null);
  if(data.leaderboard){const b=data.leaderboard;setStanding({published:b.published,published_at:b.published_at,rank:b.mine?.rank??null,score:b.mine?.score??null});}
  current.current=data;setSnapshot(data);if(restoreRequested.current){setRestoreKey(x=>x+1);restoreRequested.current=false;}setNetwork('online');
 }
 useEffect(()=>{
  if(!token)return;let cancelled=false,pollTimer,heartTimer,polling=false,lastStanding=0;const t=token;
  async function poll(){if(cancelled||polling)return;polling=true;const sent=performance.now();try{const d=await request('/api/player/bootstrap');if(cancelled||t!==aliveToken.current)return;accept(d,sent);

   }catch(e){if(!cancelled&&aliveToken.current){setNetwork('offline');setError(e.message)}}finally{polling=false;if(!cancelled)pollTimer=setTimeout(poll,document.hidden?4000:1400);}}
  let heartbeatBusy=false;
  async function beat(){if(heartbeatBusy||cancelled)return;heartbeatBusy=true;clearTimeout(heartTimer);try{await request('/api/auth/team/heartbeat',{method:'POST',body:{}});if(!cancelled)setNetwork('online')}catch(e){if(!cancelled&&aliveToken.current){setNetwork('offline');setError(e.message)}}finally{heartbeatBusy=false;if(!cancelled)heartTimer=setTimeout(beat,(current.current?.identity.heartbeat_interval_seconds||15)*1000)}}
  refresh.current=()=>{clearTimeout(pollTimer);poll()};beat();poll();
  const visible=()=>{if(!document.hidden){clearTimeout(heartTimer);beat();refresh.current()}},online=()=>{clearTimeout(heartTimer);beat();refresh.current()};
  const departing=()=>{if(!aliveToken.current)return;fetch('/api/auth/team/departing',{method:'POST',keepalive:true,headers:{Authorization:'Bearer '+aliveToken.current,'Content-Type':'application/json'},body:'{}'}).catch(()=>{})};
  document.addEventListener('visibilitychange',visible);window.addEventListener('online',online);window.addEventListener('pageshow',online);window.addEventListener('pagehide',departing);
  return()=>{cancelled=true;clearTimeout(pollTimer);clearTimeout(heartTimer);document.removeEventListener('visibilitychange',visible);window.removeEventListener('online',online);window.removeEventListener('pageshow',online);window.removeEventListener('pagehide',departing)};
 },[token,request]);
 useEffect(()=>{const fn=e=>{if(e.key===TOKEN&&e.newValue!==aliveToken.current){if(!e.newValue)signOut('Signed out in another tab.');else{first.current=true;current.current=null;setSnapshot(null);setToken(e.newValue)}}};window.addEventListener('storage',fn);return()=>window.removeEventListener('storage',fn)},[signOut]);
 const left=useClock(snapshot),expiredPoll=useRef('');
 useEffect(()=>{if(snapshot?.timer.running&&left<=0){const k=snapshot.snapshot_version;if(expiredPoll.current!==k){expiredPoll.current=k;refresh.current()}}},[snapshot,left]);
 useEffect(()=>{if(snapshot&&['ACTIVE_UNANSWERED','ACTIVE'].includes(snapshot.player_state.screen))window.scrollTo({top:0,behavior:'instant'})},[snapshot?.question?.id]);
 async function mutate(path,body){if(busyRef.current)return;busyRef.current=true;setBusy(true);setError('');
  try{await request(path,{method:'POST',body});refresh.current();}
  catch(e){setError(e.message);refresh.current();}
  finally{busyRef.current=false;setBusy(false)}}
 async function logout(){if(busyRef.current)return;setLogoutConfirm(false);busyRef.current=true;setBusy(true);try{await request('/api/auth/team/logout',{method:'POST',body:{}});signOut('Signed out. Your team device session is available again.')}catch(e){setError('Logout was not confirmed. '+e.message)}finally{busyRef.current=false;setBusy(false)}}
 const login=t=>{localStorage.setItem(TOKEN,t);first.current=true;candidate.current=null;current.current=null;setSnapshot(null);setToken(t);setNotice('');setError('');window.scrollTo(0,0)};
 if(!token)return <Login onLogin={login} notice={notice} reduced={reduced}/>;
 if(!snapshot)return <main className="connecting"><Brand small/><div className="loading-line"/><h1>Finding your place.</h1><p>{error||'Restoring your team, timer and current turn.'}</p><button className="secondary" onClick={()=>refresh.current()}>Retry connection</button><button className="text-button" onClick={()=>setLogoutConfirm(true)}>Log out</button>{logoutConfirm&&<Confirm title="Log out?" text="Release this session. Your progress stays saved." onCancel={()=>setLogoutConfirm(false)} onConfirm={logout}/>}</main>;
 const identity=snapshot.identity,ps=snapshot.player_state,liveQuestion=!!snapshot.question;
 const autoBetween=identity.round_code==='R1A'&&ps.screen==='BETWEEN_QUESTIONS'&&snapshot.rules.r1a.advance_mode==='AUTO'&&!snapshot.leaderboard?.final;
 const showBoard=(identity.round_code==='R1A'&&snapshot.leaderboard?.published&&(
   snapshot.leaderboard.final||(
     snapshot.rules.r1a.advance_mode==='MANUAL'&&snapshot.rules.r1a.show_results_after_each_question&&['BETWEEN_QUESTIONS','ENDED'].includes(ps.screen)&&!!ps.current_question_position
   )))||(identity.round_code==='R1B'&&snapshot.leaderboard?.final&&['FINISHED','EXPIRED','ENDED'].includes(ps.screen));
 return <main className="game-shell"><header className="game-header"><div className="game-brand-row"><Brand small/><div className="session-tools"><span className="slot-tag">TEAM DEVICE</span><button className="icon-button" aria-label="Log out" onClick={()=>setLogoutConfirm(true)}>↪</button></div></div>
  <div className="team-strip"><span className="team-name" title={identity.team_name}>{identity.team_name}</span>{snapshot.score_total!=null&&<span className="strip-score">{formatScore(snapshot.score_total)} <small>{identity.round_code==='R1B'?(snapshot.leaderboard?.final?'final R1B pts':'provisional R1B pts'):'R1A pts'}</small></span>}{standing?.rank!=null&&<span className="strip-rank" title={standing?.published_at?'Result view '+new Date(standing.published_at).toLocaleString():''}>{identity.round_code==='R1A'?'R1A rank':snapshot.leaderboard?.provisional?'Provisional R1B rank':'Final R1B rank'} <b>#{standing.rank}</b></span>}</div>
  {identity.round_code==='R1B'&&snapshot.prior_r1a_results?.final&&snapshot.prior_r1a_results.mine&&<p className="micro-copy">Final R1A score: {formatScore(snapshot.prior_r1a_results.mine.score)} · rank #{snapshot.prior_r1a_results.mine.rank}</p>}
 </header>
 <div className="connection-row"><span className={'connection-label '+network}><span className="status-dot"/>{network==='online'?'Connected':network==='offline'?'Reconnecting · server clock continues':'Connecting'}</span><button className="text-button tiny" disabled={busy} onClick={()=>{if(snapshot.draft&&!confirm('Restore the server-saved draft? Any unsaved edits in this tab will be discarded.'))return;setError('');restoreRequested.current=true;refresh.current()}}>Refresh</button></div>
 {error&&<div className="notice error" role="alert">{error}<button className="text-button tiny" onClick={()=>setError('')}>Dismiss</button></div>}
 <div className="round-content" key={identity.round_code}>{liveQuestion?<Question key={snapshot.question.id+':'+restoreKey} state={snapshot} left={left} request={request} mutate={mutate} busy={busy} onError={setError}/>:showBoard?<QuestionLeaderboard state={snapshot}/>:autoBetween?<AutoBetween state={snapshot}/>:<Waiting state={snapshot} left={left} mutate={mutate} busy={busy}/>}</div>
 <footer className="game-footer"><span>ROUND 01 / {identity.round_code==='R1A'?'WIZTIME':'MATH RALLY'}</span><span>{identity.team_code}</span></footer>
 {logoutConfirm&&<Confirm title="Leave this session?"
   text="Your saved progress stays. Logging out releases this team device session."
   onCancel={()=>setLogoutConfirm(false)} onConfirm={logout}/>}
 {reveal&&!liveQuestion&&!snapshot.available_actions.includes('READY')&&<Reveal key={reveal.key} event={reveal} reduced={reduced} onClose={()=>setReveal(null)}/>}
 </main>;
}
class ErrorBoundary extends React.Component {constructor(p){super(p);this.state={failed:false}}static getDerivedStateFromError(){return{failed:true}}render(){return this.state.failed?<main className="connecting"><h1>Let’s reconnect.</h1><p>The interface could not render. Your recorded progress is stored on the server.</p><button className="primary" onClick={()=>location.reload()}>Reload page</button></main>:this.props.children}}
createRoot(document.getElementById('root')).render(<ErrorBoundary><App/></ErrorBoundary>);
