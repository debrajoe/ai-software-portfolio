// One user gesture, two authenticated operations. Retry never inserts a message.
export function createTurnWorkflow(api, uuid) {
  const pending = new Map();
  const key = (base,target) => target+'|'+base;
  function checkTarget(target) { if (!['hecate','mercury','jupiter','daedalus'].includes(target)) throw new Error('Unknown council participant'); }
  let busy = false;
  async function locked(operation) {
    if (busy) return {busy:true};
    busy = true;
    try { return await operation(); } finally { busy = false; }
  }
  async function ask(turn) {
    try {
      await api(turn.base+'/'+turn.target, 'POST', {request_id:turn.invitationId});
      if (pending.get(key(turn.base,turn.target))===turn) pending.delete(key(turn.base,turn.target));
      return {saved:turn.saved, replied:true};
    } catch (error) {
      // A terminal local failure permits a NEW explicit invitation. Unknown
      // network outcomes reuse the ID so the server can replay its journal.
      if (error.status===502 && error.requestCompleted) turn.invitationId=uuid();
      error.messageSaved=turn.saved;
      error.canRetryReply=true;
      throw error;
    }
  }
  return {
    hasRetry(base,target='hecate') { const turn=pending.get(key(base,target));return !!turn && (turn.saved || turn.standalone); },
    send(base, body, canAsk, onSaved=()=>{}, target='hecate') {
      checkTarget(target);
      return locked(async()=>{
        let turn=pending.get(key(base,target));
        if (!turn || turn.base!==base || turn.body!==body || turn.standalone) {
          turn={base,target,body,messageId:uuid(),invitationId:uuid(),saved:false,standalone:false};
          pending.set(key(base,target),turn);
        }
        if (!turn.saved) {
          try {
            await api(base+'/messages','POST',{body,client_message_id:turn.messageId});
          } catch(error) {
            // The same locally generated ID/body is reused only after an
            // ambiguous save. This exact conflict confirms the earlier save.
            if (!(error.status===409 && error.message==='Duplicate client_message_id')) throw error;
          }
          turn.saved=true;
          onSaved();
        }
        if (!canAsk) { pending.delete(key(base,target)); return {saved:true,replied:false}; }
        return ask(turn);
      });
    },
    invite(base,target='hecate') {
      checkTarget(target);
      return locked(async()=>{
        let turn=pending.get(key(base,target));
        if (!turn || turn.base!==base || (!turn.saved && !turn.standalone)) {
          turn={base,target,invitationId:uuid(),saved:false,standalone:true};pending.set(key(base,target),turn);
        }
        return ask(turn);
      });
    }
  };
}

// A finite owner-selected list. Stops at the first failure; resumes only on a click.
export function createCouncilWorkflow(turns) {
 const pending=new Map();let busy=false;
 const allowed=['hecate','mercury','jupiter','daedalus'];
 const normalize=targets=>{
  if(!Array.isArray(targets)||!targets.length||targets.some(t=>!allowed.includes(t)))throw new Error('Choose at least one council member');
  return [...new Set(targets)];
 };
 const key=(base,targets)=>base+'|'+targets.join(',');
 async function run(group,onProgress,onReply){
  while(group.index<group.targets.length){
   const target=group.targets[group.index];onProgress(target);
   try{
    if(group.index===0&&!group.standalone&&!group.saved){
     await turns.send(group.base,group.body,true,()=>{group.saved=true;group.onSaved();},target);
    }else await turns.invite(group.base,target);
   }catch(error){error.messageSaved=group.saved;error.groupTarget=target;throw error;}
   group.index++;await onReply(target);
  }
  pending.delete(key(group.base,group.targets));return {saved:group.saved,replied:true};
 }
 async function locked(fn){if(busy)return {busy:true};busy=true;try{return await fn();}finally{busy=false;}}
 return {
  hasRetry(base,targets){if(!targets.length)return false;const g=pending.get(key(base,normalize(targets)));return !!g&&(g.saved||g.standalone);},
  send(base,body,targets,onSaved=()=>{},onProgress=()=>{},onReply=async()=>{}){
   const selected=normalize(targets);
   return locked(async()=>{
    const k=key(base,selected);let g=pending.get(k);
    if(!g||g.body!==body||g.standalone){g={base,body,targets:selected,index:0,saved:false,standalone:false,onSaved};pending.set(k,g);}
    return run(g,onProgress,onReply);
   });
  },
  invite(base,targets,onProgress=()=>{},onReply=async()=>{}){
   const selected=normalize(targets);
   return locked(async()=>{
    const k=key(base,selected);let g=pending.get(k);
    if(!g||(!g.saved&&!g.standalone)){g={base,targets:selected,index:0,saved:false,standalone:true};pending.set(k,g);}
    return run(g,onProgress,onReply);
   });
  }
 };
}
