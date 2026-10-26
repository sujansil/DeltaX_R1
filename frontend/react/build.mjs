import { build } from 'esbuild';
import { cp, mkdir, readdir, writeFile, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const here = path.dirname(fileURLToPath(import.meta.url));
const out = path.resolve(here, '../player/assets');
await rm(out, {recursive: true, force: true});
await mkdir(out, {recursive: true});
await build({entryPoints: [path.join(here, 'src/main.jsx')], outdir: out,
  entryNames: 'player', chunkNames:'chunk-[hash]', assetNames:'[name]-[hash]',
  bundle: true, minify: true, splitting: true, format:'esm', target:['es2020'],
  jsx:'automatic', legalComments:'eof', define:{'process.env.NODE_ENV':'"production"'},
  loader:{'.woff2':'file','.woff':'file','.ttf':'file'}});
await cp(path.join(here, 'node_modules/katex/dist/katex.min.css'), path.join(out, 'katex.min.css'));
await cp(path.join(here, 'node_modules/katex/dist/fonts'), path.join(out,'fonts'), {recursive:true});
const packages = ['react','react-dom','three','katex','esbuild'];
let notices='Delta X frontend third-party notices\n\n';
for (const name of packages) {
 const files = await readdir(path.join(here,'node_modules',name));
 const license=files.find(f=>/^license(\.txt|\.md)?$/i.test(f));
 if(license) { const {readFile}=await import('node:fs/promises'); notices+=`\n--- ${name} ---\n`+await readFile(path.join(here,'node_modules',name,license),'utf8'); }
}
await writeFile(path.join(out,'THIRD_PARTY_NOTICES.txt'),notices);
console.log('Built self-hosted player assets; Node is not needed to serve the finished app.');
