import {useEffect,useRef} from 'react';
export default function MathScene({reduced=false}) {
 const holder=useRef(null);
 useEffect(()=>{
  const el=holder.current;let alive=true,frame,renderer,observer,resize,visible=true;
  const disposables=[];
  import('three').then(T=>{
   if(!alive)return;
   try {
    const scene=new T.Scene(); const camera=new T.PerspectiveCamera(36,1,.1,80);camera.position.set(7,5.4,9);camera.lookAt(0,0,0);
    renderer=new T.WebGLRenderer({alpha:true,antialias:devicePixelRatio<2,powerPreference:'low-power'});
    renderer.setPixelRatio(Math.min(devicePixelRatio,1.6));el.appendChild(renderer.domElement);
    const group=new T.Group();scene.add(group);
    const material=new T.LineBasicMaterial({color:0x7e919b,transparent:true,opacity:.44});disposables.push(material);
    function line(points,mat=material){const g=new T.BufferGeometry().setFromPoints(points);const l=new T.Line(g,mat);group.add(l);disposables.push(g);return l;}
    const surface=(x,z)=>.24*(x*x-z*z);
    for(let a=-3;a<=3.01;a+=.3) {
     line(Array.from({length:61},(_,j)=>{const b=-3+j*.1;return new T.Vector3(a,surface(a,b),b)}));
     line(Array.from({length:61},(_,j)=>{const b=-3+j*.1;return new T.Vector3(b,surface(b,a),a)}));
    }
    const curveMat=new T.LineBasicMaterial({color:0xb1b29a,transparent:true,opacity:.66});disposables.push(curveMat);
    const curve=Array.from({length:181},(_,i)=>{const x=-3+i/30,z=Math.sin(x*1.2)*1.8;return new T.Vector3(x,surface(x,z)+.035,z)});line(curve,curveMat);
    const grid=new T.GridHelper(11,22,0x39434c,0x263039);grid.position.y=-2.5;scene.add(grid);disposables.push(grid.geometry,grid.material);
    const sphereG=new T.SphereGeometry(.065,12,8),sphereM=new T.MeshBasicMaterial({color:0xb6b6a0});disposables.push(sphereG,sphereM);
    const point=new T.Mesh(sphereG,sphereM);group.add(point);
    resize=()=>{if(!alive)return;const w=el.clientWidth,h=el.clientHeight;renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();};resize();
    const ro=new ResizeObserver(resize);ro.observe(el);observer=new IntersectionObserver(([e])=>{visible=e.isIntersecting;});observer.observe(el);
    const start=performance.now();let previous=0;
    function draw(now){if(!alive)return;frame=requestAnimationFrame(draw);if(!visible||document.hidden||now-previous<40)return;previous=now;
     const t=reduced?0:Math.max(0,(now-start)/1000);group.rotation.y=Math.sin(t*.12)*.28-.15;group.rotation.z=Math.sin(t*.09)*.025;
     point.position.copy(curve[Math.floor((t*.035%1)*180)]);renderer.render(scene,camera);if(reduced)cancelAnimationFrame(frame);
    }
    frame=requestAnimationFrame(draw);el.classList.add('scene-ready');
    el._cleanup=()=>ro.disconnect();
   }catch{el.classList.add('scene-fallback');}
  }).catch(()=>el.classList.add('scene-fallback'));
  return()=>{alive=false;cancelAnimationFrame(frame);observer?.disconnect();el._cleanup?.();disposables.forEach(d=>Array.isArray(d)?d.forEach(x=>x.dispose()):d.dispose());renderer?.dispose();renderer?.domElement.remove();};
 },[reduced]);
 return <div className="math-scene" ref={holder} aria-hidden="true"><svg className="scene-poster" viewBox="0 0 500 500"><g fill="none" stroke="#6b7f89" opacity=".5">{Array.from({length:19},(_,i)=><path key={i} d={`M ${50+i*12} 400 Q ${200+i*5} ${-60+i*12} ${480-i*8} 320`}/>)}{Array.from({length:12},(_,i)=><path key={'b'+i} d={`M 30 ${170+i*18} Q 250 ${420-i*18} 470 ${140+i*18}`}/>)}</g></svg></div>;
}
