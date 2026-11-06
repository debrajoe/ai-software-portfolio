import test from 'node:test';
import assert from 'node:assert/strict';
import {createTurnWorkflow,createCouncilWorkflow} from '../council_chamber/room_static/turn-workflow.mjs';
const base='/api/v1/chambers/room/sessions/meeting';
function fixture(api){let n=0;return createCouncilWorkflow(createTurnWorkflow(api,()=>`id-${++n}`));}
function error(message,status=502){return Object.assign(new Error(message),{status,requestCompleted:status===502});}
test('one saved message invites selected members in order once each',async()=>{
 const calls=[];const progress=[];const replies=[];let saved=0;
 const group=fixture(async path=>calls.push(path));
 await group.send(base,'Hello',['jupiter','hecate','jupiter','daedalus'],()=>saved++,t=>progress.push(t),async t=>replies.push(t));
 assert.deepEqual(calls,[base+'/messages',base+'/jupiter',base+'/hecate',base+'/daedalus']);
 assert.deepEqual(progress,['jupiter','hecate','daedalus']);assert.deepEqual(replies,progress);assert.equal(saved,1);
});
test('failed later response stops queue; explicit continue retries it without repeating successful replies',async()=>{
 const calls=[];let failed=false;
 const group=fixture(async(path,method,body)=>{calls.push([path,body]);if(path.endsWith('/mercury')&&!failed){failed=true;throw error('Unavailable');}});
 const targets=['jupiter','mercury','daedalus'];
 await assert.rejects(group.send(base,'Hello',targets),/Unavailable/);
 assert.deepEqual(calls.map(c=>c[0]),[base+'/messages',base+'/jupiter',base+'/mercury']);assert.equal(group.hasRetry(base,targets),true);
 await group.invite(base,targets);
 assert.deepEqual(calls.map(c=>c[0]),[base+'/messages',base+'/jupiter',base+'/mercury',base+'/mercury',base+'/daedalus']);
 assert.notEqual(calls[2][1].request_id,calls[3][1].request_id);assert.equal(group.hasRetry(base,targets),false);
});
test('ambiguous response retry keeps invitation ID and does not repeat human message',async()=>{
 const calls=[];let fail=true;
 const group=fixture(async(path,method,body)=>{calls.push([path,body]);if(path.endsWith('/jupiter')&&fail){fail=false;throw error('Network',0);}});
 await assert.rejects(group.send(base,'Hello',['jupiter','hecate']));await group.invite(base,['jupiter','hecate']);
 assert.equal(calls.filter(c=>c[0].endsWith('/messages')).length,1);
 assert.equal(calls[1][1].request_id,calls[2][1].request_id);
});
test('failed message save contacts no participant',async()=>{
 const calls=[];const group=fixture(async path=>{calls.push(path);throw error('Closed',409);});
 await assert.rejects(group.send(base,'Hello',['jupiter','hecate']));assert.deepEqual(calls,[base+'/messages']);assert.equal(group.hasRetry(base,['jupiter','hecate']),false);
});
test('standalone selected invitations post no human message',async()=>{
 const calls=[];await fixture(async path=>calls.push(path)).invite(base,['mercury','daedalus']);assert.deepEqual(calls,[base+'/mercury',base+'/daedalus']);
});
test('all targets validated before posting anything',async()=>{
 const calls=[];const group=fixture(async path=>calls.push(path));
 assert.throws(()=>group.send(base,'Hello',[]),/Choose/);assert.throws(()=>group.invite(base,['jupiter','evil']),/Choose/);assert.deepEqual(calls,[]);
});
test('double click starts no second batch or human save',async()=>{
 let release;const wait=new Promise(r=>release=r);const calls=[];
 const group=fixture(async path=>{calls.push(path);if(path.endsWith('/jupiter'))await wait;});
 const first=group.send(base,'Hello',['jupiter','hecate']);await new Promise(r=>setTimeout(r,0));
 assert.deepEqual(await group.send(base,'Hello',['jupiter','hecate']),{busy:true});release();await first;
 assert.deepEqual(calls,[base+'/messages',base+'/jupiter',base+'/hecate']);
});
test('sign-in expiry stops later invitations',async()=>{
 const calls=[];const group=fixture(async path=>{calls.push(path);if(path.endsWith('/mercury'))throw error('Sign-in expired',401);});
 await assert.rejects(group.invite(base,['jupiter','mercury','daedalus']),/expired/);assert.deepEqual(calls,[base+'/jupiter',base+'/mercury']);
});
test('progress fetch failure after success does not repeat the completed response',async()=>{
 const calls=[];const group=fixture(async path=>calls.push(path));
 await assert.rejects(group.invite(base,['jupiter','hecate'],()=>{},async()=>{throw error('Read failed',0);}));
 await group.invite(base,['jupiter','hecate']);assert.deepEqual(calls,[base+'/jupiter',base+'/hecate']);
});
