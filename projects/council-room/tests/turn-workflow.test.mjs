import test from 'node:test';
import assert from 'node:assert/strict';
import {createTurnWorkflow} from '../council_chamber/room_static/turn-workflow.mjs';
const base='/api/v1/chambers/room/sessions/meeting';
function ids(){let n=0;return ()=>`id-${++n}`;}
function error(message,status,requestCompleted=false){return Object.assign(new Error(message),{status,requestCompleted});}

test('one action saves first, then invites, and clears the draft after save',async()=>{
 const events=[];
 const turns=createTurnWorkflow(async(path,method,body)=>{events.push([path,body]);},ids());
 const result=await turns.send(base,'Hello',true,()=>events.push(['draft cleared']));
 assert.equal(events[0][0],base+'/messages');assert.equal(events[0][1].body,'Hello');
 assert.equal(events[1][0],'draft cleared');assert.equal(events[2][0],base+'/hecate');
 assert.deepEqual(result,{saved:true,replied:true});assert.equal(turns.hasRetry(base),false);
});
test('member send never invokes Gemini',async()=>{
 const calls=[];const turns=createTurnWorkflow(async path=>calls.push(path),ids());
 assert.deepEqual(await turns.send(base,'Member message',false),{saved:true,replied:false});
 assert.deepEqual(calls,[base+'/messages']);
});
test('failed message save never invites Hecate or clears draft',async()=>{
 const calls=[];let cleared=false;
 const turns=createTurnWorkflow(async path=>{calls.push(path);throw error('Closed',409);},ids());
 await assert.rejects(turns.send(base,'Message',true,()=>{cleared=true;}),/Closed/);
 assert.deepEqual(calls,[base+'/messages']);assert.equal(cleared,false);
});
test('confirmed provider failure retries only Hecate with a fresh request ID',async()=>{
 const calls=[];let fail=true;
 const turns=createTurnWorkflow(async(path,method,body)=>{calls.push({path,body});if(path.endsWith('/hecate')&&fail)throw error('Timeout',502,true);},ids());
 await assert.rejects(turns.send(base,'Saved once',true),e=>e.messageSaved&&e.canRetryReply);
 assert.equal(turns.hasRetry(base),true);fail=false;await turns.invite(base);
 assert.equal(calls.filter(c=>c.path.endsWith('/messages')).length,1);
 const invitations=calls.filter(c=>c.path.endsWith('/hecate'));assert.notEqual(invitations[0].body.request_id,invitations[1].body.request_id);
});
test('ambiguous provider response keeps the same request ID on explicit retry',async()=>{
 const calls=[];let fail=true;
 const turns=createTurnWorkflow(async(path,method,body)=>{calls.push({path,body});if(path.endsWith('/hecate')&&fail)throw new Error('Connection lost');},ids());
 await assert.rejects(turns.send(base,'Saved once',true));fail=false;await turns.invite(base);
 const invitations=calls.filter(c=>c.path.endsWith('/hecate'));assert.equal(invitations[0].body.request_id,invitations[1].body.request_id);
 assert.equal(calls.filter(c=>c.path.endsWith('/messages')).length,1);
});
test('ambiguous message save reuses its ID and resolves exact duplicate conflict',async()=>{
 const calls=[];let saves=0;
 const turns=createTurnWorkflow(async(path,method,body)=>{calls.push({path,body});if(path.endsWith('/messages')){saves++;if(saves===1)throw new Error('Lost save response');throw error('Duplicate client_message_id',409);}},ids());
 await assert.rejects(turns.send(base,'Same body',true));await turns.send(base,'Same body',true);
 const messages=calls.filter(c=>c.path.endsWith('/messages'));assert.equal(messages[0].body.client_message_id,messages[1].body.client_message_id);
 assert.equal(calls.filter(c=>c.path.endsWith('/hecate')).length,1);
});
test('expired sign-in between operations retains saved message and never inserts twice',async()=>{
 const calls=[];let expired=true;
 const turns=createTurnWorkflow(async path=>{calls.push(path);if(path.endsWith('/hecate')&&expired)throw error('Sign-in expired',401);},ids());
 await assert.rejects(turns.send(base,'Saved before expiry',true),e=>e.messageSaved);
 expired=false;await turns.invite(base);assert.equal(calls.filter(p=>p.endsWith('/messages')).length,1);
});
test('double submit cannot overlap message/provider operations',async()=>{
 let release;const waiting=new Promise(resolve=>release=resolve);const calls=[];
 const turns=createTurnWorkflow(async path=>{calls.push(path);if(path.endsWith('/messages'))await waiting;},ids());
 const first=turns.send(base,'One message',true);
 assert.deepEqual(await turns.send(base,'One message',true),{busy:true});
 assert.deepEqual(await turns.invite(base),{busy:true});release();await first;
 assert.deepEqual(calls,[base+'/messages',base+'/hecate']);
});
test('standalone invitation and retry do not create a human message',async()=>{
 const calls=[];const turns=createTurnWorkflow(async path=>calls.push(path),ids());await turns.invite(base);
 assert.deepEqual(calls,[base+'/hecate']);
});

test('Mercury send targets only Mercury and saves the human message once',async()=>{
 const calls=[];const turns=createTurnWorkflow(async path=>calls.push(path),ids());
 await turns.send(base,'Please review',true,()=>{},'mercury');
 assert.deepEqual(calls,[base+'/messages',base+'/mercury']);
});
test('Mercury standalone invitation does not invoke Hecate',async()=>{
 const calls=[];const turns=createTurnWorkflow(async path=>calls.push(path),ids());
 await turns.invite(base,'mercury');assert.deepEqual(calls,[base+'/mercury']);
});
test('switching participant cannot consume another participant retry ID',async()=>{
 const calls=[];let fail=true;
 const turns=createTurnWorkflow(async(path,method,body)=>{calls.push({path,body});if(path.endsWith('/hecate')&&fail)throw new Error('Unknown outcome');},ids());
 await assert.rejects(turns.invite(base,'hecate'));await turns.invite(base,'mercury');
 assert.equal(turns.hasRetry(base,'hecate'),true);assert.equal(turns.hasRetry(base,'mercury'),false);
 fail=false;await turns.invite(base,'hecate');
 assert.equal(calls[0].body.request_id,calls[2].body.request_id);
 assert.notEqual(calls[0].body.request_id,calls[1].body.request_id);
});
test('unsupported participant is rejected without any API call',()=>{
 const calls=[];const turns=createTurnWorkflow(async path=>calls.push(path),ids());
 assert.throws(()=>turns.invite(base,'attacker'),/Unknown/);assert.deepEqual(calls,[]);
});

test('Jupiter send targets only Jupiter and keeps Mercury retry separate',async()=>{
 const calls=[];const turns=createTurnWorkflow(async path=>calls.push(path),ids());
 await turns.send(base,'Hello Jupiter',true,()=>{},'jupiter');
 assert.deepEqual(calls,[base+'/messages',base+'/jupiter']);
 assert.equal(turns.hasRetry(base,'mercury'),false);
});
test('Daedalus selection routes one saved message only to Codex',async()=>{
 const calls=[];const turns=createTurnWorkflow(async path=>calls.push(path),ids());
 await turns.send(base,'Hello Dae',true,()=>{},'daedalus');
 assert.deepEqual(calls,[base+'/messages',base+'/daedalus']);
 assert.equal(turns.hasRetry(base,'hecate'),false);
});
