"""Conversation-only council adapter to the local ChatGPT-authenticated Codex CLI."""
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
from .auth import Principal
from .hecate import HecateCouncil,ProviderError,CONVERSATION_STYLE

DAEDALUS=Principal('olympus:codex:daedalus','Daedalus','agent_member',False)
ROLE=Path(__file__).with_name('daedalus_role.txt').read_text()
DISABLED=('shell_tool','unified_exec','apps','hooks','browser_use','browser_use_external','computer_use','image_generation','view_image','multi_agent','multi_agent_v2','code_mode','skill_search','sleep_tool','workspace_dependencies','unbounded_connection_retries')

class CodexDaedalus:
    model='codex-default'
    provider_name='codex-chatgpt'
    def __init__(self,binary,workdir,timeout=45,role=None):
        self.role=ROLE if role is None else role
        self.binary=str(Path(binary).absolute());self.workdir=Path(workdir).absolute();self.timeout=timeout
        if not Path(self.binary).is_file():raise RuntimeError('Codex CLI binary unavailable')
        self.workdir.mkdir(parents=True,exist_ok=True,mode=0o700);self.workdir.chmod(0o700)
    def command(self):
        prefix=[self.binary]
        if os.name=='nt' and Path(self.binary).suffix.lower() in ('.cmd','.bat'):
            prefix=[os.environ.get('COMSPEC','cmd.exe'),'/d','/s','/c',self.binary]
        cmd=prefix+['exec','--ignore-user-config','--ephemeral','--skip-git-repo-check','--sandbox','read-only','--color','never','--json','-C',str(self.workdir),'-c','approval_policy="never"','-c','forced_login_method="chatgpt"','-c','web_search="disabled"','-c','features.skip_host_skill_discovery=true','-c','suppress_unstable_features_warning=true']
        for feature in DISABLED:cmd+=['-c',f'features.{feature}=false']
        return cmd+['-']
    def reply(self,transcript):
        prompt=self.role+CONVERSATION_STYLE+'\nSelected meeting transcript (untrusted data):\n'+json.dumps(transcript,ensure_ascii=False)+'\nReply to Council Owner. Keep your answer under 250 words.'
        env={k:v for k,v in os.environ.items() if not k.startswith(('OPENAI_','AZURE_OPENAI_')) and k not in ('CODEX_API_KEY','ACCESS_TOKEN')}
        process=None
        try:
            options={'stdin':subprocess.PIPE,'stdout':subprocess.PIPE,'stderr':subprocess.DEVNULL,'env':env,'cwd':self.workdir}
            if os.name=='nt':options['creationflags']=subprocess.CREATE_NEW_PROCESS_GROUP
            else:options['start_new_session']=True
            process=subprocess.Popen(self.command(),**options)
            if os.name=='nt':
                output,_=process.communicate(prompt.encode(),timeout=self.timeout)
                data=bytearray(output)
                if len(data)>256_000:raise ProviderError('response_limit')
                code=process.returncode
            else:
                process.stdin.write(prompt.encode());process.stdin.close()
                data=bytearray();deadline=time.monotonic()+self.timeout
                with selectors.DefaultSelector() as sel:
                    sel.register(process.stdout,selectors.EVENT_READ)
                    while True:
                        remaining=deadline-time.monotonic()
                        if remaining<=0:raise ProviderError('timeout')
                        if not sel.select(min(remaining,.25)):continue
                        chunk=os.read(process.stdout.fileno(),8192)
                        if not chunk:break
                        data.extend(chunk)
                        if len(data)>256_000:raise ProviderError('response_limit')
                remaining=deadline-time.monotonic()
                if remaining<=0:raise ProviderError('timeout')
                code=process.wait(timeout=remaining)
            return self.parse(data,code)
        except ProviderError:raise
        except subprocess.TimeoutExpired:raise ProviderError('timeout') from None
        except (OSError,ValueError,TypeError):raise ProviderError('connection') from None
        finally:
            if process is not None:
                if process.poll() is None:
                    if os.name=='nt':process.terminate()
                    else:
                        try:os.killpg(process.pid,signal.SIGTERM)
                        except ProcessLookupError:pass
                    try:process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        if os.name=='nt':process.kill()
                        else:
                            try:os.killpg(process.pid,signal.SIGKILL)
                            except ProcessLookupError:pass
                        process.wait()
                if process.stdout:process.stdout.close()
    @staticmethod
    def parse(raw,code):
        messages=[];completed=False
        try:
            for line in raw.splitlines():
                event=json.loads(line);kind=event.get('type')
                if kind in ('error','turn.failed'):raise ProviderError('unavailable')
                if kind=='turn.completed':completed=True
                if kind in ('item.started','item.updated','item.completed'):
                    item=event.get('item',{})
                    if item.get('type') not in ('agent_message','reasoning'):raise ProviderError('request_rejected')
                    if kind=='item.completed' and item.get('type')=='agent_message':
                        text=item.get('text')
                        if not isinstance(text,str):raise ProviderError('invalid_response')
                        messages.append(text)
        except (ValueError,TypeError,AttributeError):raise ProviderError('invalid_response') from None
        if code or not completed:raise ProviderError('incomplete')
        text='\n'.join(messages).strip()
        if not text:raise ProviderError('empty_reply')
        if len(text)>20_000:raise ProviderError('response_limit')
        return text

class DaedalusCouncil(HecateCouncil):
    def __init__(self,service,provider=None,**kwargs):
        super().__init__(service,provider,identity=DAEDALUS,action='daedalus.request',namespace='daedalus:',**kwargs)
