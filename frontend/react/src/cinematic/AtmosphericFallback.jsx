export default function AtmosphericFallback({unavailable=false}) {
 return <div className="dx-atmosphere" data-testid="atrium-fallback">
  <svg viewBox="0 0 1000 1000" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
   <defs><radialGradient id="dx-core"><stop stopColor="#77d8cf" stopOpacity=".6"/><stop offset="1" stopColor="#1a4d80" stopOpacity="0"/></radialGradient><linearGradient id="dx-metal" x2="1" y2="1"><stop stopColor="#ddc49b"/><stop offset=".5" stopColor="#458fab"/><stop offset="1" stopColor="#ddd0ad"/></linearGradient></defs>
   <circle cx="580" cy="390" r="420" fill="url(#dx-core)"/>
   <g fill="none" stroke="url(#dx-metal)" transform="translate(580 390) rotate(-28)">
    {Array.from({length:9},(_,i)=><ellipse key={i} rx={100+i*28} ry={90+i*19} transform={`rotate(${i*17})`} opacity={.3+i*.06} strokeWidth={i%3===0?3:1}/>)}
    <path d="M0 -130 116 -55 116 65 0 140 -116 65 -116 -55Z M0 -130V140 M-116 -55 116 65 M116 -55 -116 65 M0 -130 -116 65 116 65Z" strokeWidth="2"/>
   </g>
   <g stroke="#568fbc" opacity=".17">{Array.from({length:11},(_,i)=><path key={i} d={`M${i*160-300} 1000 580 560 M0 ${580+i*i*4}H1000`}/>)}</g>
  </svg>
  {unavailable&&<span className="dx-fallback-caption">Atmospheric view · WebGL 3D unavailable</span>}
 </div>;
}
