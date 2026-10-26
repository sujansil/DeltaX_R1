import test from 'node:test';
import assert from 'node:assert/strict';
import {DraftQueue} from '../src/draft-queue.mjs';
const tick=()=>new Promise(r=>setTimeout(r,0));
test('autosaves serialize and preserve a newer edit while a save is pending',async()=>{
 const writes=[];let finish;
 const q=new DraftQueue({turn:'turn-1',save:d=>{writes.push(d);return new Promise(r=>finish=r)},delay:100000,maxWait:100000});
 q.edit('4');const one=q.flush();await tick();q.edit('42');await q.flush();assert.equal(writes.length,1);finish();await one;const two=q.flush();await tick();assert.equal(writes.length,2);assert.equal(writes[1].answer,'42');assert.equal(writes[1].version,2);finish();await two;q.stop();
});
test('clearing an answer is saved explicitly',async()=>{let saved;const q=new DraftQueue({turn:'turn',version:5,answer:'42',save:async d=>saved=d});q.edit('');await q.flush();assert.equal(saved.answer,'');assert.equal(saved.version,6);q.stop()});
test('pausing blocks saves without losing dirty text',async()=>{let count=0;const q=new DraftQueue({turn:'turn',save:async()=>count++});q.setEnabled(false);q.edit('12');await q.flush();assert.equal(count,0);q.setEnabled(true);await tick();assert.equal(count,1);q.stop()});
test('stale draft conflicts block silent overwrite',async()=>{const q=new DraftQueue({turn:'turn',save:async()=>{const e=Error('stale');e.status=409;throw e}});q.edit('12');await q.flush();assert.equal(q.conflict,true);assert.equal(q.dirty,true);q.stop()});
test('network failures retain unsaved text for retry',async()=>{const q=new DraftQueue({turn:'turn',save:async()=>{throw Error('offline')}});q.edit('12');await q.flush();assert.equal(q.answer,'12');assert.equal(q.dirty,true);q.stop()});
test('disposed turns cannot write a later turn',async()=>{let count=0;const q=new DraftQueue({turn:'old-turn',save:async()=>count++});q.edit('12');q.stop();await q.flush();assert.equal(count,0)});
