import {Component,Suspense,lazy,useEffect,useLayoutEffect,useRef,useState} from 'react';
import gsap from 'gsap';
import '@fontsource-variable/outfit';
import '@fontsource-variable/manrope';
import '@fontsource/ibm-plex-mono/latin-400.css';
import AtmosphericFallback from './AtmosphericFallback.jsx';
import './login.css';

// Only the visual world is lazy. The real form is available during loading or failure.
const AtriumWorld=lazy(()=>import('./AtriumWorld.tsx'));

export class LoginPresentationBoundary extends Component {
 state={failed:false};
 static getDerivedStateFromError(){return {failed:true};}
 render(){return this.state.failed?this.props.fallback:this.props.children;}
}

function useMobile(){
 const [mobile,setMobile]=useState(()=>matchMedia('(max-width: 759px)').matches);
 useEffect(()=>{const media=matchMedia('(max-width: 759px)'),update=()=>setMobile(media.matches);media.addEventListener('change',update);return()=>media.removeEventListener('change',update);},[]);
 return mobile;
}

// Presentation only: no credentials, API calls, storage, navigation, or success gates.
export default function CinematicLogin({children,reduced,busy,activation}) {
 const mobile=useMobile(),[paused,setPaused]=useState(false);
 const root=useRef(null),drag=useRef(null),focus=useRef(false);
 const controls=useRef({pointer:{x:0,y:0},drag:0,ripple:-100,focused:false,progress:0,reducedMotion:reduced});
 const motionReduced=reduced||paused;
 controls.current.reducedMotion=motionReduced;
 controls.current.focused=focus.current||busy;

 useEffect(()=>{
  const move=e=>{controls.current.pointer.x=e.clientX/window.innerWidth*2-1;controls.current.pointer.y=1-e.clientY/window.innerHeight*2;};
  window.addEventListener('pointermove',move,{passive:true});
  return()=>window.removeEventListener('pointermove',move);
 },[]);

 useLayoutEffect(()=>{
  if(motionReduced)return;
  const context=gsap.context(()=>{
   gsap.fromTo('.dx-world',{opacity:.16},{opacity:1,duration:1.65,ease:'power2.out',clearProps:'opacity'});
   gsap.fromTo('.dx-header, .credential-inner',{y:12,opacity:.25},{y:0,opacity:1,duration:.85,stagger:.1,ease:'power3.out',clearProps:'all'});
  },root);
  return()=>context.revert();
 },[motionReduced]);

 useEffect(()=>{
  const element=root.current,viewport=window.visualViewport;
  let frame;
  const update=()=>{
   if(!element)return;
   const inset=viewport?Math.max(0,window.innerHeight-viewport.height-viewport.offsetTop):0;
   const open=mobile&&(inset>120||window.innerHeight<560);
   element.dataset.keyboard=open?'open':'closed';
   element.style.setProperty('--dx-keyboard-inset',`${open?inset:0}px`);
   cancelAnimationFrame(frame);
   if(open)frame=requestAnimationFrame(()=>{
    const active=document.activeElement;
    if(!element.contains(active)||!active.matches('input,button'))return;
    const rect=active.getBoundingClientRect(),top=viewport?.offsetTop||0,height=viewport?.height||window.innerHeight,bottom=top+height;
    if(rect.bottom>bottom-18||rect.top<top+18)window.scrollBy({top:rect.top-(top+(height-rect.height)/2),behavior:'instant'});
   });
  };
  update();viewport?.addEventListener('resize',update);window.addEventListener('resize',update);
  return()=>{cancelAnimationFrame(frame);viewport?.removeEventListener('resize',update);window.removeEventListener('resize',update);};
 },[mobile]);

 return <div className={`dx-login${motionReduced?' dx-motion-reduced':''}${activation?' dx-activation':''}`} ref={root} data-testid="cinematic-login">
  <a className="dx-skip" href="#credentials">Skip to team login</a>
  <div className="dx-world" data-testid="atrium-world"
   onPointerDown={e=>{drag.current={x:e.clientX,y:e.clientY,moved:false};}}
   onPointerMove={e=>{if(!drag.current)return;const dx=e.clientX-drag.current.x;if(Math.abs(dx)>3||Math.abs(e.clientY-drag.current.y)>5)drag.current.moved=true;controls.current.drag+=dx*.003;drag.current.x=e.clientX;}}
   onPointerUp={()=>{if(drag.current&&!drag.current.moved)controls.current.ripple=performance.now()/1000;drag.current=null;}}
   onPointerCancel={()=>{drag.current=null;}} onPointerLeave={()=>{drag.current=null;}}>
   <LoginPresentationBoundary fallback={<AtmosphericFallback unavailable/>}><Suspense fallback={<AtmosphericFallback/>}><AtriumWorld controls={controls} mobile={mobile}/></Suspense></LoginPresentationBoundary>
  </div>
  <div className="dx-shade" aria-hidden="true"/><div className="dx-grain" aria-hidden="true"/>
  <header className="dx-header"><div className="dx-identity"><span className="dx-wordmark">DELTA<span>X</span></span><span className="dx-edition">ROUND 01</span></div>
   <button type="button" className="dx-motion" aria-label={reduced?'Reduced motion follows your system preference':paused?'Resume ambient motion':'Pause ambient motion'} aria-pressed={motionReduced} disabled={reduced} onClick={()=>setPaused(value=>!value)}><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">{motionReduced?<path d="m5 3 7 5-7 5Z" fill="none" stroke="currentColor"/>:<path d="M5 3v10M11 3v10" stroke="currentColor"/>}</svg></button>
  </header>
  <div className="dx-world-title" aria-hidden="true"><span>01</span><p>THE INFINITE ATRIUM<small>AN OBSERVATORY OF POSSIBILITY</small></p></div>
  <main className="dx-content" onFocusCapture={e=>{if(e.target.closest('form')){focus.current=true;controls.current.focused=true;}}} onBlurCapture={e=>{if(!e.currentTarget.querySelector('form')?.contains(e.relatedTarget)){focus.current=false;controls.current.focused=busy;}}}>{children}</main>
  <div className="dx-annotation" aria-hidden="true"><span>+</span> SYMMETRY IS ONLY THE BEGINNING. <i/> ∞ / 01</div>
  <footer className="dx-footer"><span>↔ &nbsp; DRAG TO EXPLORE · TAP TO RESONATE</span><a href="?login=classic#credentials">Classic sign-in <span aria-hidden="true">↗</span></a></footer>
 </div>;
}
