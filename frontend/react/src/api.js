export class ApiError extends Error {
  constructor(message, status, code) { super(message); this.status=status; this.code=code; }
}
export async function api(path, {token, method='GET', body, signal, timeout=12000}={}) {
  const controller=new AbortController();
  const cancel=()=>controller.abort(); signal?.addEventListener('abort',cancel,{once:true});
  const timer=setTimeout(cancel,timeout);
  try {
    const response=await fetch(path,{method,cache:'no-store',signal:controller.signal,
      headers:{...(token?{Authorization:`Bearer ${token}`} : {}),...(body!==undefined?{'Content-Type':'application/json'}:{})},
      body:body===undefined?undefined:JSON.stringify(body)});
    const data=await response.json().catch(()=>({}));
    if(!response.ok) {
      const d=data.detail;
      const message=Array.isArray(d)?d.map(x=>x.msg).join('; '):typeof d==='object'?d?.message:d;
      throw new ApiError(message||`Request failed (${response.status})`,response.status,d?.code);
    }
    return data;
  } catch(error) {
    if(error.name==='AbortError') throw new ApiError('Connection timed out. Checking the server before retrying.',0,'NETWORK');
    if(error instanceof TypeError) throw new ApiError('Cannot reach the server. Your clock may still be running.',0,'NETWORK');
    throw error;
  } finally {clearTimeout(timer);signal?.removeEventListener('abort',cancel);}
}
export const formatScore=n=>Number(n??0).toLocaleString('en-IN');
export function formatTime(ms) {const s=Math.ceil(Math.max(0,ms)/1000);return `${Math.floor(s/60).toString().padStart(2,'0')}:${(s%60).toString().padStart(2,'0')}`;}
export function outcomeKey(s) {const o=s?.own_outcome;return o?.finalized&&o.score_revealed!==false&&o.points!==null&&o.submission_id?`${s.identity.round_code}:${o.submission_id}`:null;}
export function revealAllowed(s) {return !!s&&!['ACTIVE','ACTIVE_UNANSWERED','ACTIVE_ACCEPTED','PAUSED'].includes(s.player_state.screen)&&!s.question;}
