import {build} from 'esbuild';
import {mkdir,rm,readFile,readdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const here=path.dirname(fileURLToPath(import.meta.url)),out=path.resolve(here,'../audience/assets');
await rm(out,{recursive:true,force:true});await mkdir(out,{recursive:true});
await build({entryPoints:{scene:path.join(here,'src/audience/scene.tsx')},outdir:out,bundle:true,minify:true,splitting:true,format:'esm',jsx:'automatic',target:['es2020'],chunkNames:'chunk-[hash]',assetNames:'[name]-[hash]',loader:{'.woff2':'file','.woff':'file','.ttf':'file'},legalComments:'eof',define:{'process.env.NODE_ENV':'"production"'}});
await build({entryPoints:[path.join(here,'src/player-atrium.css')],outfile:path.resolve(here,'../player/player-atrium.css'),minify:true});
let notices='Delta X Infinite Atrium audience — third-party notices\n';
for(const name of ['react','react-dom','three','@react-three/fiber','@react-three/drei','@react-three/postprocessing','postprocessing','katex','@fontsource-variable/manrope','@fontsource-variable/outfit','@fontsource/ibm-plex-mono']){
 const folder=path.join(here,'node_modules',name),files=await readdir(folder),license=files.find(f=>/^license(\.md|\.txt)?$/i.test(f));
 if(license)notices+=`\n--- ${name} ---\n${await readFile(path.join(folder,license),'utf8')}\n`;
}
await writeFile(path.join(out,'THIRD_PARTY_NOTICES.txt'),notices);
console.log('Built audience assets and scoped player CSS. Existing player/login bundle untouched.');
