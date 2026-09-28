"""SignalOps local application server. Python stdlib only."""
from __future__ import annotations
import json, logging, os, re, threading, uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from hindsight_provider import ProviderError, configuration_from_env


ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("SIGNALOPS_DATA", ROOT / "data" / "incidents.json"))
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))
MAX_BODY = 16_384
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("signalops")
try:
    MEMORY_MODE, provider = configuration_from_env(DATA)
except RuntimeError as exc:
    log.error("Invalid memory-provider configuration: %s", exc)
    raise SystemExit(2)

class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message); self.status=status; self.code=code; self.message=message

def clean_text(value, field, minimum=2, maximum=2000):
    if not isinstance(value, str): raise ApiError(400,"INVALID_FIELD",f"{field} must be text.")
    value=value.strip()
    if len(value)<minimum: raise ApiError(400,"INVALID_FIELD",f"{field} must be at least {minimum} characters.")
    if len(value)>maximum: raise ApiError(400,"INVALID_FIELD",f"{field} must be no more than {maximum} characters.")
    if any(ord(c)<32 and c not in "\n\t\r" for c in value): raise ApiError(400,"INVALID_FIELD",f"{field} contains unsupported control characters.")
    return value

class Handler(BaseHTTPRequestHandler):
    server_version = "SignalOps/1.0"
    def log_message(self, fmt, *args): log.info("%s - %s", self.client_address[0], fmt % args)
    def send_json(self, status, payload):
        raw=json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(raw))); self.send_header("Cache-Control","no-store"); self.send_header("X-Content-Type-Options","nosniff"); self.send_header("Content-Security-Policy","default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; connect-src 'self'"); self.end_headers(); self.wfile.write(raw)
    def body(self):
        try: size=int(self.headers.get("Content-Length","0"))
        except ValueError: raise ApiError(400,"INVALID_LENGTH","Invalid request length.")
        if size<=0: raise ApiError(400,"EMPTY_BODY","Request body is required.")
        if size>MAX_BODY: raise ApiError(413,"PAYLOAD_TOO_LARGE","Request body exceeds 16 KB.")
        try: return json.loads(self.rfile.read(size))
        except (json.JSONDecodeError,UnicodeDecodeError): raise ApiError(400,"INVALID_JSON","Request body must be valid JSON.")
    def do_GET(self):
        path=urlparse(self.path).path
        try:
            if path=="/api/health": self.send_json(200,{"status":"ok","memory":MEMORY_MODE}); return
            if path=="/api/incidents": self.send_json(200,{"incidents":provider.list_incidents()}); return
            if path=="/api/search":
                q=clean_text(parse_qs(urlparse(self.path).query).get("q",[""])[0],"query",1,200)
                self.send_json(200,{"incidents":provider.search(q)}); return
            if path=="/": path="/index.html"
            rel=path.lstrip("/")
            candidate=(ROOT/"web"/rel).resolve()
            if not candidate.is_relative_to((ROOT/"web").resolve()) or not candidate.is_file(): raise ApiError(404,"NOT_FOUND","Page not found.")
            data=candidate.read_bytes(); typ="text/html; charset=utf-8" if candidate.suffix==".html" else "text/javascript; charset=utf-8" if candidate.suffix==".js" else "text/css; charset=utf-8"
            self.send_response(200); self.send_header("Content-Type",typ); self.send_header("Content-Length",str(len(data))); self.send_header("Cache-Control","no-store"); self.send_header("X-Content-Type-Options","nosniff"); self.send_header("Content-Security-Policy","default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; connect-src 'self'"); self.end_headers(); self.wfile.write(data)
        except ApiError as e: self.send_json(e.status,{"error":{"code":e.code,"message":e.message}})
        except ProviderError as e: self.send_json(e.status,{"error":{"code":e.code,"message":e.message}})
        except OSError: log.exception("Storage read failure"); self.send_json(503,{"error":{"code":"STORAGE_UNAVAILABLE","message":"Incident memory is unavailable. Check storage permissions and integrity."}})
        except Exception: log.exception("GET failed"); self.send_json(500,{"error":{"code":"INTERNAL_ERROR","message":"The request could not be completed. Please try again."}})
    def do_POST(self):
        try:
            if urlparse(self.path).path=="/api/analyze":
                data=self.body()
                if not isinstance(data,dict): raise ApiError(400,"INVALID_BODY","Expected a JSON object.")
                service=clean_text(data.get("service"),"service",2,80)
                symptoms=clean_text(data.get("symptoms"),"symptoms",8,2000)
                result=provider.analyze(service,symptoms)
                self.send_json(200,result); return
            if urlparse(self.path).path=="/api/incidents":
                data=self.body()
                if not isinstance(data,dict): raise ApiError(400,"INVALID_BODY","Expected a JSON object.")
                item=provider.save(clean_text(data.get("service"),"service",2,80),clean_text(data.get("title"),"title",5,160),clean_text(data.get("symptoms"),"symptoms",8,2000),clean_text(data.get("resolution"),"resolution",8,2000),clean_text(data.get("outcome"),"outcome",2,80))
                self.send_json(201,{"incident":item}); return
            raise ApiError(404,"NOT_FOUND","API route not found.")
        except ApiError as e: self.send_json(e.status,{"error":{"code":e.code,"message":e.message}})
        except ProviderError as e: self.send_json(e.status,{"error":{"code":e.code,"message":e.message}})
        except OSError: log.exception("Storage failure"); self.send_json(503,{"error":{"code":"STORAGE_UNAVAILABLE","message":"Memory storage is unavailable. Your changes were not saved."}})
        except Exception: log.exception("POST failed"); self.send_json(500,{"error":{"code":"INTERNAL_ERROR","message":"The request could not be completed. Please try again."}})

if __name__=="__main__":
    try:
        server=ThreadingHTTPServer((HOST,PORT),Handler); server.daemon_threads=True
        log.info("SignalOps listening on %s:%s",HOST,PORT); server.serve_forever()
    except OSError: log.exception("Unable to start server (port/permission issue)"); raise SystemExit(1)
