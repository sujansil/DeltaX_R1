import test from 'node:test';import assert from 'node:assert/strict';
import {autoRevealSeconds,formatTime,outcomeKey,revealAllowed,api} from '../src/api.js';
test('timer rounds up and never displays negative time',()=>{assert.equal(formatTime(60001),'01:01');assert.equal(formatTime(-20),'00:00');assert.equal(formatTime(28*60000),'28:00')});
test('score reveal never covers active questions or paused state',()=>{for(const screen of ['ACTIVE','ACTIVE_UNANSWERED','ACTIVE_ACCEPTED','PAUSED'])assert.equal(revealAllowed({player_state:{screen}}),false);assert.equal(revealAllowed({player_state:{screen:'BETWEEN_QUESTIONS'}}),true)});
test('outcome identity is scoped to a round and finalized record',()=>{assert.equal(outcomeKey({identity:{round_code:'R1A'},own_outcome:{finalized:true,submission_id:4}}),'R1A:4');assert.equal(outcomeKey({own_outcome:{finalized:false}}),null)});
test('API normalizes structured session errors',async()=>{const prior=globalThis.fetch;globalThis.fetch=async()=>new Response(JSON.stringify({detail:{code:'SESSION_INACTIVE',message:'Revoked'}}),{status:401});try{await assert.rejects(()=>api('/api/test',{token:'fixture'}),e=>e.status===401&&e.code==='SESSION_INACTIVE'&&e.message==='Revoked')}finally{globalThis.fetch=prior}});

test('accepted outcomes cannot reveal hidden scores',()=>{assert.equal(outcomeKey({identity:{round_code:'R1A'},own_outcome:{finalized:true,submission_id:4,points:null,score_revealed:false}}),null)});
test('AUTO countdown follows authoritative due-time samples for different delays and zero',()=>{
 const phase=(remaining_ms,frozen=false)=>({next_reveal_at:'2026-11-07T12:00:00Z',remaining_ms,frozen});
 assert.equal(autoRevealSeconds(phase(8000),0),8);
 assert.equal(autoRevealSeconds(phase(21500),1500),20);
 assert.equal(autoRevealSeconds(phase(0),4000),0);
 assert.equal(autoRevealSeconds(phase(5100,true),9000),6);
 assert.equal(autoRevealSeconds({remaining_ms:8000},0),null);
});
