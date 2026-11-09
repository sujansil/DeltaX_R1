import {Component,Suspense,lazy,useEffect,useState} from 'react';
import type {ReactNode} from 'react';
import {createRoot} from 'react-dom/client';
import {Canvas,flushSync as flushScene,useThree} from '@react-three/fiber';
import {Bloom,EffectComposer,Vignette} from '@react-three/postprocessing';
import katex from 'katex';
import type {WorldControls} from '../cinematic/types';
import '@fontsource-variable/outfit';
import '@fontsource-variable/manrope';
import '@fontsource/ibm-plex-mono/latin-400.css';

const Atrium=lazy(()=>import('../cinematic/Atrium'));
type Controls={current:WorldControls};
const escape=(s:string)=>s.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
export function richText(value:string){
 return value.split(/(\$\$[\s\S]+?\$\$|\$[^$\n]+?\$|\\\([\s\S]+?\\\)|\\\[[\s\S]+?\\\])/g).map(part=>{
  const display=part.startsWith('$$')||part.startsWith('\\['),math=display||part.startsWith('$')||part.startsWith('\\(');
  return math?katex.renderToString(part.slice(display||part.startsWith('\\')?2:1,display||part.startsWith('\\')?-2:-1),{throwOnError:false,displayMode:display,trust:false,strict:'ignore'}):escape(part);
 }).join('');
}
class Boundary extends Component<{children:ReactNode},{failed:boolean}>{
 state={failed:false};static getDerivedStateFromError(){return{failed:true};}
 componentDidCatch(){document.body.dataset.scene='fallback';}
 render(){return this.state.failed?null:this.props.children;}
}
function Lifecycle({lost}:{lost:()=>void}){
 const gl=useThree(s=>s.gl);
 useEffect(()=>{const fn=(e:Event)=>{e.preventDefault();lost();};gl.domElement.setAttribute('aria-hidden','true');gl.domElement.addEventListener('webglcontextlost',fn);return()=>gl.domElement.removeEventListener('webglcontextlost',fn);},[gl,lost]);
 return null;
}
function World({controls}:{controls:Controls}){
 const [lost,setLost]=useState(false),[mode,setMode]=useState(()=>({visible:!document.hidden,still:controls.current.reducedMotion,mobile:innerWidth<800}));
 useEffect(()=>{const update=()=>setMode(old=>{const next={visible:!document.hidden,still:controls.current.reducedMotion,mobile:innerWidth<800};return JSON.stringify(old)===JSON.stringify(next)?old:next;});window.addEventListener('atrium:visual-mode',update);window.addEventListener('resize',update);document.addEventListener('visibilitychange',update);return()=>{window.removeEventListener('atrium:visual-mode',update);window.removeEventListener('resize',update);document.removeEventListener('visibilitychange',update);};},[controls]);
 const [onLost]=useState(()=>()=>{setLost(true);document.body.dataset.scene='fallback';});
 if(lost)return null;
 return <Canvas camera={{position:[0,2,12],fov:45,near:.1,far:180}} dpr={[1,mode.mobile?1:1.35]} frameloop={!mode.visible?'never':mode.still?'demand':'always'} gl={{antialias:false,alpha:false,powerPreference:'low-power'}} onCreated={()=>{document.body.dataset.scene='ready';}}>
  <Lifecycle lost={onLost}/><Suspense fallback={null}><Atrium controls={controls} mobile={mode.mobile}/><EffectComposer multisampling={0} enableNormalPass={false}><Bloom luminanceThreshold={1.3} intensity={.3} mipmapBlur/><Vignette eskil={false} offset={.2} darkness={.4}/></EffectComposer></Suspense>
 </Canvas>;
}
export function mountAudience(holder:HTMLElement,controls:Controls){
 try{const gl=document.createElement('canvas').getContext('webgl2');if(!gl){document.body.dataset.scene='fallback';return()=>{};}gl.getExtension('WEBGL_lose_context')?.loseContext();}catch{document.body.dataset.scene='fallback';return()=>{};}
 const root=createRoot(holder);let disposed=false;
 root.render(<Boundary><World controls={controls}/></Boundary>);
 return()=>{if(disposed)return;disposed=true;root.unmount();flushScene(()=>{});};
}
