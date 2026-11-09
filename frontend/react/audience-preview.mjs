import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {fixture} from './audience-fixtures.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),started=Date.now();
const types={'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff2':'font/woff2','.woff':'font/woff','.ttf':'font/ttf','.svg':'image/svg+xml'};
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,'http://local'),send=(status,type,data)=>{res.writeHead(status,{'Content-Type':type,'Cache-Control':'no-store'});res.end(data);};
 if(url.pathname==='/favicon.ico')return send(204,'text/plain','');
 if(url.pathname==='/api/public/audience'){
  const query=new URL(req.headers.referer||'http://local').searchParams,requested=query.get('demo')||'STANDBY';
  const data=fixture(requested.toUpperCase());
  if(query.get('motion')==='1'&&data.leaderboard){const n=Math.floor((Date.now()-started)/7000);data.leaderboard.publication_id=n+1;if(n%2){[data.leaderboard.data[1],data.leaderboard.data[10]]=[data.leaderboard.data[10],data.leaderboard.data[1]];data.leaderboard.data.forEach((row,i)=>{row.rank=i+1;row.score=9460-i*315;});}}
  return send(200,'application/json',JSON.stringify(data));
 }
 if(url.pathname.startsWith('/api/'))return send(503,'application/json',JSON.stringify({detail:'Synthetic audience preview only; no backend is connected.'}));
 const relative=url.pathname==='/'||url.pathname==='/audience/'?'audience/index.html':decodeURIComponent(url.pathname.slice(1));
 const file=path.resolve(root,relative+(relative.endsWith('/')?'index.html':''));
 if(!file.startsWith(root+path.sep)||!['audience/','player/'].some(prefix=>relative.startsWith(prefix)))return send(404,'text/plain','Not found');
 try{return send(200,types[path.extname(file)]||'application/octet-stream',await readFile(file));}catch{return send(404,'text/plain','Not found');}
});
const port=Number(process.env.PORT||8771);
server.listen(port,'127.0.0.1',()=>console.log(`SYNTHETIC R1 AUDIENCE — no backend/database\nhttp://127.0.0.1:${port}/audience/?demo=ANSWER_LEADERBOARD&motion=1\nOther demos: STANDBY, PREVIEW, QUESTION, ANSWER, RELAY_STANDBY, REVIEW_READY, REVIEW_QUESTION, REVIEW_ANSWER, LEADERBOARD, THANK_YOU\nAll content and scores are fictional.`));
for(const sig of ['SIGINT','SIGTERM'])process.on(sig,()=>server.close(()=>process.exit(0)));
