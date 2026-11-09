// Fictional public payloads only. Imported by local preview/tests, never the app.
export function fixture(view='STANDBY', now=Date.now()){
 const relay=['RELAY_STANDBY','REVIEW_READY','REVIEW_QUESTION','REVIEW_ANSWER','LEADERBOARD','THANK_YOU','RESULTS_PENDING'].includes(view);
 const hasQuestion=['QUESTION','PREVIEW','ANSWER','ANSWER_LEADERBOARD','REVIEW_QUESTION','REVIEW_ANSWER'].includes(view);
 const answerView=['ANSWER','ANSWER_LEADERBOARD','REVIEW_ANSWER'].includes(view);
 const names=['NOETHER COLLECTIVE','EIGENVECTORS','THE AXIOM SOCIETY','INFINITE DESCENT','VECTOR FOUR','Q.E.D.','THE NULL HYPOTHESIS','COBALT CONSTANT','HILBERT SPACE','THE IRRATIONALS','PRIME DIRECTIVE','FOURIER FRONTIER','ASYMPTOTES','MODULO','TANGENT LINE','ZERO KNOWLEDGE'];
 const q=hasQuestion?{id:relay?31:7,position:relay?1:7,type:relay?'NUMERIC':'MCQ',text:relay?'Evaluate $\\sum_{k=1}^{10} k^2$.':'What is the value of $e^{i\\pi} + 1$?',image_url:null,
  options:relay?[]:[{id:11,position:1,text:'$0$',image_url:null},{id:12,position:2,text:'$1$',image_url:null},{id:13,position:3,text:'$-1$',image_url:null},{id:14,position:4,text:'$i$',image_url:null}]}:null;
 return {version:1,snapshot_at:new Date(now).toISOString(),revision:1,view,round_code:relay?'R1B':'R1A',round_status:relay&&view!=='RELAY_STANDBY'?'ENDED':view==='STANDBY'||view==='PREVIEW'?'DRAFT':'LIVE',paused:false,question:q,
  answer:answerView?{question_id:q.id,position:q.position,type:q.type,...(relay?{value:'385.000000000000'}:{correct_option_id:11})}:null,
  leaderboard:['ANSWER_LEADERBOARD','LEADERBOARD'].includes(view)?{publication_id:1,published_at:new Date(now).toISOString(),after_question:relay?null:7,final:relay,
   data:names.map((name,i)=>({rank:i+1,team_id:i+1,team_name:name,team_code:'T'+(i+1),score:9460-i*315}))}:null,
  question_count:relay?4:23,deadline:view==='QUESTION'?new Date(now+30000).toISOString():null,remaining_ms:view==='QUESTION'?30000:null,server_now:new Date(now).toISOString(),next_question_at:null};
}
