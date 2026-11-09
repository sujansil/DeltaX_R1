import { useEffect, useLayoutEffect, useMemo, useRef } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import { Environment, Lightformer } from '@react-three/drei';
import * as THREE from 'three';
import type { WorldProps } from './types';

const TAU = Math.PI * 2;
const GOLD = '#d7b67e';
const tempObject = new THREE.Object3D();

function RingDetails({ radius, width }: { radius: number; width: number }) {
  const tickRef = useRef<THREE.InstancedMesh>(null), jewelRef = useRef<THREE.InstancedMesh>(null);
  const bridgeRef = useRef<THREE.InstancedMesh>(null), pinRef = useRef<THREE.InstancedMesh>(null);
  useLayoutEffect(() => {
    const object = new THREE.Object3D();
    for (let i = 0; i < 96; i++) {
      const a = i / 96 * TAU, major = i % 8 === 0;
      object.position.set(Math.cos(a) * radius, Math.sin(a) * radius, .052);
      object.rotation.set(0, 0, a - Math.PI / 2);
      object.scale.set(major ? .021 : .012, major ? width * .73 : width * .37, .015);
      object.updateMatrix(); tickRef.current?.setMatrixAt(i, object.matrix);
      if (major) {
        object.position.set(Math.cos(a) * (radius + width * .72), Math.sin(a) * (radius + width * .72), .052);
        object.scale.setScalar(.055); object.updateMatrix(); jewelRef.current?.setMatrixAt(i / 8, object.matrix);
      }
    }
    for (let i = 0; i < 12; i++) {
      const a = i / 12 * TAU;
      object.position.set(Math.cos(a) * radius, Math.sin(a) * radius, -.05);
      object.rotation.set(0, 0, a); object.scale.set(width + .08, .06, .16); object.updateMatrix();
      bridgeRef.current?.setMatrixAt(i, object.matrix);
      object.position.z = .045; object.rotation.set(Math.PI / 2, 0, a); object.scale.set(1, 1, 1); object.updateMatrix();
      pinRef.current?.setMatrixAt(i, object.matrix);
    }
    [tickRef, jewelRef, bridgeRef, pinRef].forEach(ref => { if (ref.current) ref.current.instanceMatrix.needsUpdate = true; });
  }, [radius, width]);
  return <>
    <instancedMesh ref={tickRef} args={[undefined, undefined, 96]}><boxGeometry args={[1, 1, 1]} /><meshStandardMaterial color={GOLD} metalness={.55} roughness={.32} /></instancedMesh>
    <instancedMesh ref={jewelRef} args={[undefined, undefined, 12]}><octahedronGeometry args={[1]} /><meshStandardMaterial color={GOLD} metalness={.85} roughness={.22} /></instancedMesh>
    <instancedMesh ref={bridgeRef} args={[undefined, undefined, 12]}><boxGeometry args={[1, 1, 1]} /><meshStandardMaterial color="#908368" metalness={.85} roughness={.3} /></instancedMesh>
    <instancedMesh ref={pinRef} args={[undefined, undefined, 12]}><cylinderGeometry args={[.026, .026, .08, 8]} /><meshStandardMaterial color="#f2d5a8" metalness={.9} roughness={.2} /></instancedMesh>
  </>;
}

function Annulus({ radius, width = .2, index = 0 }: { radius: number; width?: number; index?: number }) {
  return <>
    <mesh>
      <ringGeometry args={[radius - width / 2, radius + width / 2, 192]} />
      <meshStandardMaterial color={index === 1 ? '#305768' : '#24445c'} metalness={.82} roughness={.28} side={THREE.DoubleSide} />
    </mesh>
    {[-1, 1].map(side => <mesh key={side} position={[0, 0, side * .045]}>
      <torusGeometry args={[radius + side * width / 2, .032, 8, 192]} />
      <meshStandardMaterial color={GOLD} metalness={.82} roughness={.24} emissive="#7b5130" emissiveIntensity={.08} />
    </mesh>)}
    <mesh position={[0, 0, .027]}>
      <torusGeometry args={[radius - width * .25, .009, 4, 192]} />
      <meshBasicMaterial color={index === 1 ? '#bdadf8' : '#91e2df'} toneMapped={false} />
    </mesh>
    <RingDetails radius={radius} width={width} />
  </>;
}

function GlassPanels() {
  const colors = ['#48bfbf', '#8182ce', '#e4b7a1', '#60cad0', '#a6a0df', '#ebbc9d'];
  return <>{colors.map((color, i) => <group key={color} rotation={[0, 0, i * TAU / 6]}>
    <mesh rotation={[0, .13, 0]}>
      <ringGeometry args={[1.74, 2.55, 32, 1, .09, .76]} />
      <meshPhysicalMaterial color={color} metalness={.28} roughness={.17} transparent opacity={.42} side={THREE.DoubleSide} clearcoat={1} iridescence={.6} iridescenceIOR={1.35} emissive={color} emissiveIntensity={.18} />
    </mesh>
    <mesh><torusGeometry args={[2.55, .012, 5, 40, .86]} /><meshStandardMaterial color={GOLD} metalness={.7} roughness={.3} /></mesh>
  </group>)}</>;
}

function CrystalLattice({ controls }: Pick<WorldProps, 'controls'>) {
  const struts = useRef<THREE.Group>(null);
  const faces = useRef<THREE.MeshPhysicalMaterial>(null);
  const buildTime = useRef(controls.current.reducedMotion ? 2 : 0);
  useFrame((_, delta) => {
    if (!controls.current.reducedMotion) buildTime.current += delta;
    if (buildTime.current > 2.1) return;
    struts.current?.children.forEach((strut, i) => {
      strut.scale.y = Math.max(.001, THREE.MathUtils.smoothstep(buildTime.current - i * .021, 0, .65));
    });
    if (faces.current) faces.current.opacity = THREE.MathUtils.smoothstep(buildTime.current, .5, 1.4) * .21;
  });
  const structure = useMemo(() => {
    const geometry = new THREE.IcosahedronGeometry(1.38, 0);
    const edges = new THREE.EdgesGeometry(geometry);
    const array = edges.attributes.position.array;
    const rods: { position: THREE.Vector3; quaternion: THREE.Quaternion; length: number }[] = [];
    const nodes: THREE.Vector3[] = [];
    const seen = new Set<string>();
    for (let i = 0; i < array.length; i += 6) {
      const a = new THREE.Vector3(array[i], array[i + 1], array[i + 2]);
      const b = new THREE.Vector3(array[i + 3], array[i + 4], array[i + 5]);
      const direction = b.clone().sub(a);
      rods.push({ position: a.clone().add(b).multiplyScalar(.5), quaternion: new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction.clone().normalize()), length: direction.length() });
      for (const node of [a, b]) {
        const key = node.toArray().map(n => n.toFixed(3)).join(',');
        if (!seen.has(key)) { seen.add(key); nodes.push(node); }
      }
    }
    geometry.dispose(); edges.dispose();
    return { rods, nodes };
  }, []);
  return <>
    <mesh><icosahedronGeometry args={[1.36, 0]} /><meshPhysicalMaterial ref={faces} color="#a1f0e1" metalness={.24} roughness={.15} transparent opacity={.21} side={THREE.DoubleSide} clearcoat={1} iridescence={.8} /></mesh>
    <group ref={struts}>{structure.rods.map((rod, i) => <mesh key={i} position={rod.position} quaternion={rod.quaternion}>
      <cylinderGeometry args={[.015, .015, rod.length, 6]} /><meshStandardMaterial color="#e2cfaa" metalness={.65} roughness={.25} emissive="#75c7c4" emissiveIntensity={.15} />
    </mesh>)}</group>
    {structure.nodes.map((position, i) => <mesh key={i} position={position}><icosahedronGeometry args={[.052, 0]} /><meshStandardMaterial color="#f8ddb3" metalness={.55} roughness={.2} /></mesh>)}
    <mesh rotation={[.2, .4, .1]}><octahedronGeometry args={[.65]} /><meshPhysicalMaterial color="#79d9d6" metalness={.4} roughness={.13} emissive="#5acfc6" emissiveIntensity={.55} clearcoat={1} iridescence={.7} /></mesh>
    <mesh rotation={[.3, .7, .3]}><dodecahedronGeometry args={[.9]} /><meshBasicMaterial color="#f0dab4" wireframe transparent opacity={.65} /></mesh>
  </>;
}

const ribbonVertex = `
  varying vec3 vNormal; varying vec3 vPosition; varying vec2 vUv;
  void main() { vUv=uv; vPosition=position; vNormal=normalize(normalMatrix*normal); gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }
`;
const ribbonFragment = `
  varying vec3 vNormal; varying vec3 vPosition; varying vec2 vUv;
  uniform float uTime;
  void main() {
    float edge = pow(1.-abs(dot(normalize(vNormal),vec3(0.,0.,1.))),1.8);
    vec3 a=vec3(.14,.56,.62); vec3 b=vec3(.54,.42,.77); vec3 c=vec3(.91,.64,.46);
    vec3 col=mix(a,b,smoothstep(.1,.75,vUv.x)); col=mix(col,c,pow(sin(vUv.x*3.14159),8.));
    float contour=pow(.5+.5*cos(vUv.y*62.8318),16.);
    col+=edge*.38+contour*.11;
    float ends=smoothstep(0.,.1,vUv.x)*smoothstep(0.,.1,1.-vUv.x);
    gl_FragColor=vec4(col, (.16+edge*.37+contour*.14)*ends);
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }
`;

function MathematicalSurface() {
  const geometry = useMemo(() => {
    const vertices: number[] = [], uvs: number[] = [], indices: number[] = [];
    const nu = 128, nv = 14;
    for (let i = 0; i <= nu; i++) {
      const u = i / nu * TAU;
      for (let j = 0; j <= nv; j++) {
        const v = (j / nv - .5) * .88;
        const radius = 1.91 + v * Math.cos(u * 1.5);
        vertices.push(Math.cos(u) * radius, Math.sin(u) * radius, v * Math.sin(u * 1.5) + Math.sin(u * 3) * .17);
        uvs.push(i / nu, j / nv);
      }
    }
    for (let i = 0; i < nu; i++) for (let j = 0; j < nv; j++) {
      const a = i * (nv + 1) + j, b = a + nv + 1;
      indices.push(a, b, a + 1, b, b + 1, a + 1);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3)); g.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2)); g.setIndex(indices); g.computeVertexNormals();
    return g;
  }, []);
  // This geometry is supplied as a mesh prop rather than a reconciled JSX child.
  useEffect(() => () => geometry.dispose(), [geometry]);
  return <mesh geometry={geometry} rotation={[.42, -.4, .2]}><shaderMaterial vertexShader={ribbonVertex} fragmentShader={ribbonFragment} uniforms={{ uTime: { value: 0 } }} transparent side={THREE.DoubleSide} depthWrite={false} /></mesh>;
}

function Architecture() {
  const tileRef = useRef<THREE.InstancedMesh>(null);
  const tileData = useMemo(() => {
    const tiles: { x: number; z: number; color: THREE.Color }[] = [];
    for (let x = -15; x <= 15; x++) for (let z = -27; z <= 7; z++) {
      tiles.push({ x: x * 1.45, z: z * 1.45, color: new THREE.Color((x + z) % 2 === 0 ? '#184253' : '#102e46') });
    }
    return tiles;
  }, []);
  useFrame(() => {
    const mesh = tileRef.current;
    if (mesh && !mesh.userData.ready) {
      tileData.forEach((tile, i) => {
        tempObject.position.set(tile.x, -4.25, tile.z); tempObject.rotation.set(-Math.PI / 2, 0, Math.PI / 4); tempObject.scale.setScalar(1); tempObject.updateMatrix();
        mesh.setMatrixAt(i, tempObject.matrix); mesh.setColorAt(i, tile.color);
      });
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
      mesh.userData.ready = true;
    }
  });
  return <group>
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -4.29, -15]}><planeGeometry args={[140, 160]} /><meshStandardMaterial color="#102c40" metalness={.36} roughness={.44} /></mesh>
    <instancedMesh ref={tileRef} args={[undefined, undefined, tileData.length]}>
      <planeGeometry args={[.99, .99]} /><meshStandardMaterial metalness={.42} roughness={.39} />
    </instancedMesh>
    {[3.8, 5.4, 8, 12, 18].map((radius, i) => <mesh key={radius} rotation={[-Math.PI / 2, 0, 0]} position={[2.8, -4.21 + i * .002, -1]}>
      <ringGeometry args={[radius, radius + .018, 192]} /><meshBasicMaterial color={i < 2 ? '#95b4a4' : '#456f73'} transparent opacity={.55} />
    </mesh>)}
    {Array.from({ length: 9 }, (_, i) => {
      const z = 1 - i * 6.4;
      return <group key={i} position={[0, 0, z]}>
        {[-1, 1].map(side => <group key={side} position={[side * 9.3, 0, 0]}>
          <mesh position={[0, 2.2, 0]}><boxGeometry args={[.65, 13, .7]} /><meshStandardMaterial color="#33536c" metalness={.3} roughness={.4} /></mesh>
          <mesh position={[-side * .36, 2.2, .32]}><boxGeometry args={[.045, 12.7, .055]} /><meshBasicMaterial color="#74b7b0" /></mesh>
          <mesh position={[0, -3.5, 0]}><boxGeometry args={[1.12, 1.5, 1.12]} /><meshStandardMaterial color="#203c51" metalness={.45} roughness={.32} /></mesh>
          <mesh position={[0, 8.3, 0]}><boxGeometry args={[1.3, .3, 1.3]} /><meshStandardMaterial color="#9a8a6b" metalness={.6} roughness={.3} /></mesh>
        </group>)}
        <mesh position={[0, -4.1, 0]}><torusGeometry args={[9.25, .10, 6, 96, Math.PI]} /><meshStandardMaterial color="#456980" metalness={.5} roughness={.3} /></mesh>
        <mesh position={[0, -4.1, -.12]}><torusGeometry args={[9.45, .018, 4, 96, Math.PI]} /><meshBasicMaterial color="#93bdb2" transparent opacity={.65} /></mesh>
        <mesh position={[0, 8.65, 0]}><boxGeometry args={[19.4, .22, .36]} /><meshStandardMaterial color="#3c6079" metalness={.45} roughness={.35} /></mesh>
      </group>;
    })}
    {[-1, 1].map(side => <group key={side} position={[side * 12.5, 3, -20]}>
      <mesh><boxGeometry args={[.5, 15, 50]} /><meshStandardMaterial color="#16384e" metalness={.15} roughness={.5} /></mesh>
      {Array.from({ length: 9 }, (_, i) => <mesh key={i} position={[-side * .28, 0, -20 + i * 5]}><boxGeometry args={[.03, 10, .05]} /><meshBasicMaterial color="#38777e" /></mesh>)}
    </group>)}
    <group position={[3, 3, -37]}>
      <mesh><circleGeometry args={[7.9, 96]} /><meshBasicMaterial color="#286a81" /></mesh>
      <mesh position={[0, 0, .01]}><ringGeometry args={[7.6, 7.9, 96]} /><meshStandardMaterial color="#b0ac82" metalness={.7} roughness={.25} emissive="#7e744e" emissiveIntensity={.3} /></mesh>
      {Array.from({ length: 16 }, (_, i) => <mesh key={i} rotation={[0, 0, i * TAU / 16]} position={[0, 0, .02]}><boxGeometry args={[.035, 15.4, .05]} /><meshStandardMaterial color="#76a5a2" metalness={.5} roughness={.3} /></mesh>)}
      {[2.6, 5.1].map(r => <mesh key={r} position={[0, 0, .03]}><torusGeometry args={[r, .04, 6, 96]} /><meshStandardMaterial color="#76a5a2" metalness={.5} roughness={.3} /></mesh>)}
    </group>
  </group>;
}

function Dust({ controls }: Pick<WorldProps, 'controls'>) {
  const points = useRef<THREE.Points>(null);
  const positions = useMemo(() => {
    const values = new Float32Array(540 * 3);
    let seed = 187;
    const random = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
    for (let i = 0; i < 540; i++) { values[i * 3] = (random() - .5) * 30; values[i * 3 + 1] = random() * 14 - 4; values[i * 3 + 2] = random() * -35 + 8; }
    return values;
  }, []);
  useFrame((_, delta) => { if (points.current && !controls.current.reducedMotion) points.current.rotation.y += delta * .006; });
  return <points ref={points}><bufferGeometry><bufferAttribute attach="attributes-position" args={[positions, 3]} /></bufferGeometry><pointsMaterial color="#c5d8c4" size={.022} sizeAttenuation transparent opacity={.62} depthWrite={false} /></points>;
}

function AtriumSky() {
  return <mesh scale={100}>
    <sphereGeometry args={[1, 32, 32]} />
    <shaderMaterial side={THREE.BackSide} depthWrite={false} vertexShader={`varying vec3 vP; void main(){vP=position;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}`} fragmentShader={`varying vec3 vP; void main(){vec3 p=normalize(vP);vec3 c=mix(vec3(.035,.12,.23),vec3(.035,.085,.23),smoothstep(-.2,.6,p.y));float g=pow(max(0.,dot(p,normalize(vec3(1.,.3,-1.)))),8.);c+=vec3(.05,.12,.14)*g;gl_FragColor=vec4(c,1.);\n#include <colorspace_fragment>\n}`} />
  </mesh>;
}

export default function Atrium({ controls, mobile }: WorldProps) {
  const ringA = useRef<THREE.Group>(null), ringB = useRef<THREE.Group>(null), ringC = useRef<THREE.Group>(null);
  const center = useRef<THREE.Group>(null), monument = useRef<THREE.Group>(null), ripple = useRef<THREE.Mesh>(null);
  const keyLight = useRef<THREE.PointLight>(null);
  const { camera } = useThree();
  const target = useMemo(() => new THREE.Vector3(), []);
  const started = useRef<number | null>(null);
  const motionTime = useRef(controls.current.reducedMotion ? 1.8 : 0);
  const introProgress = useRef(controls.current.reducedMotion ? 1 : 0);
  const smoothed = useRef({ focus: 0, drag: 0, px: 0, py: 0 });
  useFrame(({ clock }, delta) => {
    if (started.current === null) started.current = clock.elapsedTime;
    const age = clock.elapsedTime - started.current;
    const control = controls.current;
    if (!control.reducedMotion) motionTime.current += Math.min(delta, .05);
    const t = motionTime.current;
    introProgress.current = Math.max(introProgress.current, control.reducedMotion ? 1 : THREE.MathUtils.smoothstep(age, 0, 1.9));
    const intro = introProgress.current;
    const m = smoothed.current;
    m.focus = THREE.MathUtils.damp(m.focus, control.focused ? 1 : 0, 2.1, delta);
    m.drag = THREE.MathUtils.damp(m.drag, control.drag, 3, delta);
    m.px = THREE.MathUtils.damp(m.px, control.pointer.x, 1.6, delta);
    m.py = THREE.MathUtils.damp(m.py, control.pointer.y, 1.6, delta);
    const beat = Math.pow(Math.max(0, Math.cos((t - 5) * TAU / 14)), 28);
    const align = Math.max(m.focus * .65, beat * .86, control.progress);
    const turn = t * .075;
    if (ringA.current) {
      ringA.current.rotation.set(THREE.MathUtils.lerp(.55 + Math.sin(t * .16) * .22, .12, align), THREE.MathUtils.lerp(-.22 + Math.cos(t * .11) * .22, 0, align), turn * .16 + .16);
      ringA.current.position.z = (1 - intro) * -1.8;
    }
    if (ringB.current) ringB.current.rotation.set(THREE.MathUtils.lerp(1.02 + Math.sin(t * .14) * .3, .12, align), THREE.MathUtils.lerp(-.40 + Math.sin(t * .12) * .35, 0, align), -turn * .4 - .35);
    if (ringC.current) ringC.current.rotation.set(THREE.MathUtils.lerp(-.65 + Math.sin(t * .13) * .25, .12, align), THREE.MathUtils.lerp(.92 + Math.cos(t * .1) * .23, 0, align), turn * .36 + .5);
    if (center.current) center.current.rotation.set(.18 + Math.sin(t * .2) * .08, t * .15, .12);
    if (monument.current) { monument.current.rotation.y = m.drag * .25 + m.px * .065; monument.current.position.y = (mobile ? 4.7 : 1.1) + Math.sin(t * .5) * .05; }
    const passage = Math.pow(control.progress, 2.3);
    const cameraDistance = (mobile ? 15.7 : 16.4) + (1 - intro) * 2 - passage * 16;
    camera.position.set(m.px * (mobile ? .13 : .23) + passage * (mobile ? 0 : 3.3), (mobile ? 1.3 : 2.5) + m.py * .12 + passage * (mobile ? 3.3 : -.6), cameraDistance);
    target.set(mobile ? 0 : .15, mobile ? 1.15 : .8, -1);
    target.x += passage * (mobile ? 0 : 3.1); target.y += passage * (mobile ? 3.55 : .3);
    camera.lookAt(target);
    if (camera instanceof THREE.PerspectiveCamera && camera.fov !== (mobile ? 47 : 43)) { camera.fov = mobile ? 47 : 43; camera.updateProjectionMatrix(); }
    if (keyLight.current) keyLight.current.intensity = 55 + intro * 35 + beat * 34 + control.progress * 130;
    if (ripple.current) {
      const elapsed = performance.now() / 1000 - control.ripple;
      const alive = control.ripple > 0 && elapsed < 2.2 && !control.reducedMotion;
      ripple.current.visible = alive;
      if (alive) { ripple.current.scale.setScalar(1 + elapsed * 4.2); (ripple.current.material as THREE.MeshBasicMaterial).opacity = (1 - elapsed / 2.2) * .5; }
    }
  });

  return <>
    <color attach="background" args={['#10283f']} />
    <fog attach="fog" args={['#102e49', 16, 61]} />
    <AtriumSky />
    <ambientLight intensity={.62} color="#779ebd" />
    <hemisphereLight args={['#a3d5e6', '#18334a', 1.15]} />
    <directionalLight position={[7, 10, 7]} color="#ffe2b4" intensity={3.1} />
    <directionalLight position={[-7, 2, 3]} color="#65bad6" intensity={2.1} />
    <pointLight ref={keyLight} position={[5, 6, 3]} color="#ffdab5" intensity={90} distance={28} decay={2} />
    <pointLight position={[-4, 3, 0]} color="#679be9" intensity={60} distance={25} decay={2} />
    <pointLight position={[3, 5, -12]} color="#7fe7e1" intensity={100} distance={35} decay={2} />
    <Environment resolution={128} frames={1}>
      <Lightformer position={[0, 7, 2]} scale={[10, 3, 1]} intensity={3} color="#e8f7f3" />
      <Lightformer position={[-6, 1, 3]} rotation={[0, Math.PI / 2, 0]} scale={[4, 12, 1]} intensity={2.5} color="#79c6e8" />
      <Lightformer position={[6, 3, -2]} rotation={[0, -Math.PI / 2, 0]} scale={[5, 9, 1]} intensity={3.5} color="#ffd1a0" />
      <Lightformer position={[0, 0, -8]} scale={[8, 8, 1]} intensity={1.5} color="#669bba" />
    </Environment>
    <Architecture />
    <group ref={monument} scale={mobile ? .88 : 1} position={[mobile ? 0 : 3.3, mobile ? 4.7 : 1.1, -.5]}>
      <group ref={ringA}><Annulus radius={3.92} width={.25} />
        <mesh><torusGeometry args={[4.13, .014, 4, 192]} /><meshStandardMaterial color="#90c9c6" metalness={.65} roughness={.25} /></mesh>
        {[-1, 1].map(side => <mesh key={side} position={[side * 4.13, 0, 0]} rotation={[0, 0, Math.PI / 2]}><cylinderGeometry args={[.13, .13, .42, 12]} /><meshStandardMaterial color={GOLD} metalness={.8} roughness={.28} /></mesh>)}
      </group>
      <group ref={ringB}><Annulus radius={3.28} width={.22} index={1} /><GlassPanels /></group>
      <group ref={ringC}><Annulus radius={2.73} width={.17} index={2} /></group>
      <MathematicalSurface />
      <group ref={center}><CrystalLattice controls={controls} /></group>
      <pointLight color="#79eddf" intensity={8} distance={8} decay={2} />
      <mesh ref={ripple} visible={false}><ringGeometry args={[.96, 1, 96]} /><meshBasicMaterial color="#ace9db" transparent opacity={.4} side={THREE.DoubleSide} depthWrite={false} toneMapped={false} /></mesh>
    </group>
    <Dust controls={controls} />
  </>;
}
