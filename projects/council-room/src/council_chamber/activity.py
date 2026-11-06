"""Private, one-way, content-free activity signals for the local desktop character."""
import json,logging,os,stat,tempfile,threading,time
from pathlib import Path
from uuid import uuid4
from .private_path import private_directory
logger=logging.getLogger(__name__)
class DesktopActivity:
    def __init__(self,path):
        self.path=Path(path);self.lock=threading.Lock();self.current=None
        parent=self.path.parent
        if not private_directory(parent):
            raise RuntimeError('Desktop activity requires a private local directory')
    def start(self):
        job=str(uuid4())
        with self.lock:self.current=job;self._write('thinking',job)
        return job
    def finish(self,job,status):
        if status not in ('reply_ready','problem'):return
        with self.lock:
            if job!=self.current:return
            self._write(status,job);self.current=None
    def _write(self,status,job):
        temp=None
        try:
            if self.path.is_symlink():raise OSError('Refusing symlink')
            data={'version':1,'event_id':str(uuid4()),'status':status,'timestamp':time.time()}
            fd,temp=tempfile.mkstemp(prefix='.dae-status-',dir=self.path.parent)
            with os.fdopen(fd,'w') as stream:
                if os.name!='nt':os.fchmod(stream.fileno(),0o600)
                json.dump(data,stream);stream.flush()
            os.replace(temp,self.path);temp=None
        except OSError:logger.warning('Desktop activity signal unavailable')
        finally:
            if temp:
                try:os.unlink(temp)
                except OSError:pass
