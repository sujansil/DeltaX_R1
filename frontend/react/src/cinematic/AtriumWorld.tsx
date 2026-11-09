import {Component,Suspense,lazy,useEffect,useState} from 'react';
import type {ReactNode} from 'react';
import {Canvas,useThree} from '@react-three/fiber';
import {Bloom,EffectComposer,Vignette} from '@react-three/postprocessing';
import AtmosphericFallback from './AtmosphericFallback.jsx';
import type {WorldProps} from './types';

const Atrium=lazy(()=>import('./Atrium'));
class WorldBoundary extends Component<{children:ReactNode},{failed:boolean}> {
 state={failed:false};
 static getDerivedStateFromError(){return {failed:true};}
 render(){return this.state.failed?<AtmosphericFallback unavailable/>:this.props.children;}
}

function hasWebGL(){
 if(new URLSearchParams(window.location.search).get('render')==='fallback')return false;
 try{const canvas=document.createElement('canvas'),gl=canvas.getContext('webgl2');if(!gl)return false;gl.getExtension('WEBGL_lose_context')?.loseContext();return true;}catch{return false;}
}

function RendererLifecycle({onLost}:{onLost:()=>void}){
 const gl=useThree(state=>state.gl);
 useEffect(()=>{
  const canvas=gl.domElement,lost=(event:Event)=>{event.preventDefault();onLost();};
  canvas.setAttribute('aria-hidden','true');canvas.addEventListener('webglcontextlost',lost);
  // R3F disposes the renderer/context after child render targets and materials.
  // Disposing gl here would clear its properties before Drei's FBO cleanup.
  return()=>canvas.removeEventListener('webglcontextlost',lost);
 },[gl,onLost]);
 return null;
}

export default function AtriumWorld({controls,mobile}:WorldProps){
 const [supported]=useState(hasWebGL),[lost,setLost]=useState(false),[visible,setVisible]=useState(()=>!document.hidden);
 useEffect(()=>{const change=()=>setVisible(!document.hidden);document.addEventListener('visibilitychange',change);return()=>document.removeEventListener('visibilitychange',change);},[]);
 // Stable callback: renderer disposal must run on unmount, not ordinary re-renders.
 const [onLost]=useState(()=>()=>setLost(true));
 if(!supported||lost)return <AtmosphericFallback unavailable/>;
 return <WorldBoundary><Canvas camera={{position:[0,2,12],fov:45,near:.1,far:180}} dpr={[1,mobile?1.5:1.75]} frameloop={visible?'always':'never'} gl={{antialias:true,alpha:false,powerPreference:'high-performance'}} fallback={<AtmosphericFallback unavailable/>}>
  <RendererLifecycle onLost={onLost}/>
  <Suspense fallback={null}><Atrium controls={controls} mobile={mobile}/><EffectComposer multisampling={4} enableNormalPass={false}><Bloom luminanceThreshold={1.05} mipmapBlur intensity={.55} radius={.65}/><Vignette eskil={false} offset={.18} darkness={.35}/></EffectComposer></Suspense>
 </Canvas></WorldBoundary>;
}
