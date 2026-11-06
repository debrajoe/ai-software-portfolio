"""Test-only mutable loopback JWKS endpoint. Never imported by the runtime backend."""
from __future__ import annotations
import json,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

class JWKSProbe:
    def __init__(self,payload):
        self.payload=payload;self.mode='ok';self.requests=0;self.lock=threading.Lock();self.release=threading.Event()
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                with owner.lock:
                    owner.requests+=1;mode=owner.mode;payload=owner.payload
                if self.path!='/certs':self.send_error(404);return
                if mode=='timeout':owner.release.wait(timeout=2)
                if mode=='unavailable':self.send_response(503);body=b'Unavailable'
                else:
                    self.send_response(200)
                    body=b'{invalid-json' if mode=='malformed' else json.dumps(payload).encode()
                self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers()
                try:self.wfile.write(body)
                except (BrokenPipeError,ConnectionResetError):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=True
        self.url=f'http://127.0.0.1:{self.server.server_port}/certs'
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.02},daemon=True)
        self.closed=False
    def __enter__(self):self.thread.start();return self
    def close(self):
        if not self.closed:
            self.release.set();self.server.shutdown();self.server.server_close();self.thread.join(timeout=3);self.closed=True
    def __exit__(self,*args):self.close()
