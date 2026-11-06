'use strict';
import {createTurnWorkflow,createCouncilWorkflow} from '/turn-workflow.mjs';
const $ = id => document.getElementById(id);
const state = {me:null, csrf:'', chamber:'', meetings:[], selected:null, cursor:0, offset:0, epoch:0, polling:false, retry:null, turnBusy:false, recipients:["hecate"], currentReply:""};
function note(message=''){ $('notice').textContent=message; $('notice').hidden=!message; }
function loggedOut(){state.me=null;state.csrf='';state.epoch++;state.selected=null;$('room').hidden=true;$('welcome').hidden=false;$('signout').hidden=true;$('messages').replaceChildren();}
async function api(path, method='GET', body){
  const headers={};if(method==='POST'){headers['Content-Type']='application/json';headers['X-CSRF-Token']=state.csrf;}
  const response=await fetch(path,{method,headers,credentials:'same-origin',body:body===undefined?undefined:JSON.stringify(body)});
  const data=await response.json();
  if(!response.ok){if(response.status===401)loggedOut();const error=new Error(typeof data.detail==='string'?data.detail:(data.detail?.message||'Please check your entry and try again.'));error.status=response.status;error.requestCompleted=data.detail?.request_completed===true;throw error;}
  return data;
}
const turns=createTurnWorkflow(api,()=>crypto.randomUUID());
const council=createCouncilWorkflow(turns);
function turnControls(busy){state.turnBusy=busy;$('chambers').disabled=busy;showSelected();drawMeetings();}
const roomPath = () => '/api/v1/chambers/'+encodeURIComponent(state.chamber);
const memberName = target => ({hecate:'Hecate',mercury:'Mercury',jupiter:'Jupiter',daedalus:'Daedalus'}[target] || target);
const recipientName = () => state.recipients.map(memberName).join(', ');
const meetingPath = () => roomPath()+'/sessions/'+encodeURIComponent(state.selected.id);
function showSelected(){
 const selected=state.selected;
 $('composer-dock').hidden=!selected||!!selected.closed_at;
 $('meeting-title').textContent=selected?selected.name:'Choose a meeting';
 $('meeting-status').textContent=selected?(selected.closed_at?'CLOSED · HISTORY':'OPEN · CONVERSATION'):'MEETING HISTORY';
 $('empty').hidden=!!selected;
 $('message-form').hidden=!selected||!!selected.closed_at;
 $('ask-hecate').hidden=!selected||!!selected.closed_at||!state.me?.can_manage_sessions;
 $('hecate-note').hidden=$('ask-hecate').hidden;
 $('recipient-label').hidden=$('ask-hecate').hidden;
 for(const box of document.querySelectorAll('[name=reply-member]'))box.disabled=state.turnBusy;
 $('ask-hecate').disabled=state.turnBusy||!state.recipients.length;
 $('ask-hecate').textContent=state.turnBusy?'Waiting for '+memberName(state.currentReply)+'…':(selected&&council.hasRetry(meetingPath(),state.recipients)?'Continue selected replies':'Ask selected members');
 $('send-message').disabled=state.turnBusy||(state.me?.can_manage_sessions&&!state.recipients.length);
 $('send-message').textContent=state.turnBusy?'Sending…':'Send';
 $('close-meeting').disabled=state.turnBusy;
 $('close-meeting').hidden=!selected||!!selected.closed_at||!state.me?.can_manage_sessions;
 $('closed-note').hidden=!selected||!selected.closed_at;
}
function drawMeetings(){
 $('meetings').replaceChildren();
 if(!state.meetings.length){const p=document.createElement('p');p.textContent='No meetings yet.';p.className='welcome-note';$('meetings').append(p);}
 for(const meeting of state.meetings){
   const button=document.createElement('button');button.type='button';button.disabled=state.turnBusy;button.className='meeting'+(state.selected?.id===meeting.id?' selected':'');
   const title=document.createElement('strong');title.textContent=meeting.name;
   const status=document.createElement('small');status.textContent=(meeting.closed_at?'CLOSED':'OPEN')+' · '+new Date(meeting.opened_at).toLocaleDateString();
   button.append(title,status);button.addEventListener('click',()=>selectMeeting(meeting).catch(e=>note(e.message)));$('meetings').append(button);
 }
 $('open-form').hidden=!state.me?.can_manage_sessions||state.meetings.some(m=>!m.closed_at)||!state.chamber;
}
async function loadMeetings(append=false){
 if(!state.chamber)return;
 const epoch=state.epoch;
 const data=await api(roomPath()+'/sessions?offset='+(append?state.offset:0)+'&limit=100');
 if(epoch!==state.epoch)return;
 state.meetings=append?[...state.meetings,...data.sessions]:data.sessions;state.offset=data.next_offset;
 $('more-meetings').hidden=data.sessions.length<100;
 if(state.selected){const selected=await api(meetingPath());if(epoch!==state.epoch)return;state.selected=selected;showSelected();}
 drawMeetings();
}
async function selectMeeting(meeting){
 if(state.turnBusy)return;
 state.epoch++;state.selected=meeting;state.cursor=0;state.retry=null;$('message').value='';$('messages').replaceChildren();note();showSelected();drawMeetings();
 await loadMessages();
}
async function loadMessages(){
 if(!state.selected)return;
 const epoch=state.epoch;
 const data=await api(meetingPath()+'/messages?after='+state.cursor+'&limit=100');
 if(epoch!==state.epoch)return;
 const container=$('messages');const atBottom=container.scrollHeight-container.scrollTop-container.clientHeight<60;
 for(const message of data.messages){
   if(document.getElementById('msg-'+message.id))continue;
   const item=document.createElement('li');item.id='msg-'+message.id;
   const speakers={Hecate:'hecate',Mercury:'mercury',Jupiter:'jupiter',Daedalus:'daedalus'};
   item.dataset.speaker=speakers[message.participant.display_name]||(message.participant.kind==='human'?'owner':'other');
   const meta=document.createElement('div');meta.className='message-meta';
   const name=document.createElement('strong');name.textContent=message.participant.display_name;
   const time=document.createElement('time');time.dateTime=message.created_at;time.textContent=new Date(message.created_at).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
   const text=document.createElement('div');text.className='message-text';text.textContent=message.body;
   meta.append(name,time);item.append(meta,text);container.append(item);
 }
 state.cursor=data.next_after;$('more-messages').hidden=data.messages.length<100;
 if(atBottom)container.scrollTop=container.scrollHeight;
}
async function chooseChamber(){
 if(state.turnBusy){$('chambers').value=state.chamber;return;}
 state.epoch++;state.chamber=$('chambers').value;state.selected=null;state.meetings=[];state.cursor=0;
 $('messages').replaceChildren();$('message').value='';$('more-messages').hidden=true;showSelected();note();await loadMeetings();
}
for(const box of document.querySelectorAll('[name=reply-member]'))box.addEventListener('change',()=>{
 if(state.turnBusy)return;
 state.recipients=Array.from(document.querySelectorAll('[name=reply-member]:checked'),b=>b.value);showSelected();note();
});
$('chambers').addEventListener('change',()=>chooseChamber().catch(e=>note(e.message)));
$('refresh').addEventListener('click',async()=>{try{await loadMeetings();await loadMessages();note();}catch(e){note(e.message);}});
$('more-meetings').addEventListener('click',()=>loadMeetings(true).catch(e=>note(e.message)));
$('more-messages').addEventListener('click',()=>loadMessages().catch(e=>note(e.message)));
$('open-form').addEventListener('submit',async event=>{
 event.preventDefault();const button=event.submitter;button.disabled=true;
 try{const meeting=await api(roomPath()+'/sessions','POST',{name:$('meeting-name').value.trim()});$('meeting-name').value='';await loadMeetings();await selectMeeting(meeting);note('Meeting opened. The council can join the conversation.');}catch(e){note(e.message);}finally{button.disabled=false;}
});
function waitingFor(target){state.currentReply=target;showSelected();note('Waiting for '+memberName(target)+'…');}
$('message-form').addEventListener('submit',async event=>{
 event.preventDefault();const body=$('message').value.trim();if(!body||!state.selected||state.turnBusy)return;
 const epoch=state.epoch,base=meetingPath(),canAsk=state.me?.can_manage_sessions===true,targets=[...state.recipients];
 if(canAsk&&!targets.length){note('Choose at least one council member.');return;}
 turnControls(true);
 const saved=()=>{if(epoch===state.epoch){$('message').value='';note('Your message is saved.');}};
 const replied=async()=>{if(epoch===state.epoch)await loadMessages();};
 try{
  if(canAsk)await council.send(base,body,targets,saved,waitingFor,replied);
  else await turns.send(base,body,false,saved);
  if(epoch===state.epoch){await loadMessages();note(canAsk?'Message sent. Selected members have replied.':'Message sent.');}
 }catch(e){
  if(epoch===state.epoch){await loadMessages().catch(()=>{});await loadMeetings().catch(()=>{});}
  note((e.messageSaved?'Your message is saved. ':'')+(e.groupTarget?memberName(e.groupTarget)+': ':'')+e.message+' No automatic retry was made. Use Continue selected replies to resume after the issue is resolved.');
 }finally{state.currentReply='';turnControls(false);}
});
$('ask-hecate').addEventListener('click',async()=>{
 if(!state.selected||state.turnBusy||!state.recipients.length)return;
 const epoch=state.epoch,base=meetingPath(),targets=[...state.recipients];turnControls(true);
 try{await council.invite(base,targets,waitingFor,async()=>{if(epoch===state.epoch)await loadMessages();});if(epoch===state.epoch)note('Selected members have replied.');}
 catch(e){note((e.groupTarget?memberName(e.groupTarget)+': ':'')+e.message+' No automatic retry was made. Continue selected replies resumes without repeating completed replies.');}
 finally{state.currentReply='';turnControls(false);}
});
$('close-meeting').addEventListener('click',async()=>{
 const button=$('close-meeting');button.disabled=true;
 try{const closed=await api(meetingPath()+'/close','POST',{});state.selected=closed;showSelected();await loadMeetings();note('Meeting closed. Its history remains available.');}catch(e){note(e.message);}finally{button.disabled=false;}
});
$('signout').addEventListener('click',async()=>{
 try{await api('/signout','POST',{});loggedOut();note('Signed out of this room.');}catch(e){note(e.message);}
});
async function enter(){
 try{
   const me=await api('/api/me');state.me=me;state.csrf=me.csrf;
   $('welcome').hidden=true;$('room').hidden=false;$('signout').hidden=false;
   $('greeting').textContent='Welcome, '+me.display_name+'. Take your seat.';
   const data=await api('/api/v1/chambers');$('chambers').replaceChildren();
   for(const chamber of data.chambers){const option=document.createElement('option');option.value=chamber.id;option.textContent=chamber.name;$('chambers').append(option);}
   if(!data.chambers.length){note('No chambers are available to this account. Ask the owner to add you.');drawMeetings();return;}
   await chooseChamber();
 }catch(e){if(e.status!==401)note(e.message);}
}
setInterval(async()=>{
 if(!state.me||!state.chamber||state.polling||state.turnBusy||document.hidden)return;
 state.polling=true;
 try{await loadMeetings();await loadMessages();}catch(e){note(e.message);}finally{state.polling=false;}
},5000);
enter();
