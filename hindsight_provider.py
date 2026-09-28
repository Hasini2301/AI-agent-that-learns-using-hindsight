"""Hindsight REST memory provider (server-side credentials only).

Uses the documented Hindsight Cloud/self-hosted retain and recall REST endpoints.
No automatic write retries: a timeout can occur after the remote service commits.
"""
from __future__ import annotations
import json, logging, os, re, socket
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
from datetime import datetime, timezone
from pathlib import Path
from memory import LocalMemoryProvider

log=logging.getLogger("signalops.hindsight")
DEFAULT_URL="https://api.hindsight.vectorize.io"
class ProviderError(Exception):
    def __init__(self,code,message,status=503):
        super().__init__(message); self.code=code; self.message=message; self.status=status

def configuration_from_env(data_path=None):
    mode=os.environ.get("SIGNALOPS_MEMORY_PROVIDER","local").strip().lower()
    if mode not in {"local","hindsight"}: raise RuntimeError("SIGNALOPS_MEMORY_PROVIDER must be 'local' or 'hindsight'.")
    if mode=="local": return mode,LocalMemoryProvider(Path(data_path or os.environ.get("SIGNALOPS_DATA","data/incidents.json")))
    base=os.environ.get("HINDSIGHT_API_URL",DEFAULT_URL).strip().rstrip("/")
    api_key=os.environ.get("HINDSIGHT_API_KEY","").strip()
    bank=os.environ.get("HINDSIGHT_BANK_ID","signalops-team").strip()
    timeout_raw=os.environ.get("HINDSIGHT_TIMEOUT_SECONDS","8")
    try: timeout=float(timeout_raw)
    except ValueError as exc: raise RuntimeError("HINDSIGHT_TIMEOUT_SECONDS must be a number from 1 to 30.") from exc
    parsed=urlparse(base)
    if parsed.scheme not in ("https","http") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("HINDSIGHT_API_URL must be an HTTP(S) base URL without embedded credentials, query, or fragment.")
    if parsed.scheme!="https" and os.environ.get("HINDSIGHT_ALLOW_INSECURE_HTTP")!="1":
        raise RuntimeError("HINDSIGHT_API_URL must use HTTPS. For trusted local self-hosted instances only, set HINDSIGHT_ALLOW_INSECURE_HTTP=1.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}",bank): raise RuntimeError("HINDSIGHT_BANK_ID has invalid characters or length.")
    if not (1<=timeout<=30): raise RuntimeError("HINDSIGHT_TIMEOUT_SECONDS must be from 1 to 30.")
    if parsed.hostname and parsed.hostname.lower() in {"localhost","127.0.0.1","::1"} and parsed.scheme=="http" and not api_key:
        log.warning("Hindsight self-hosted endpoint configured without API key; restrict network access.")
    return mode, HindsightMemoryProvider(base,api_key,bank,timeout,Path(data_path or os.environ.get("SIGNALOPS_DATA","data/incidents.json")))

class HindsightMemoryProvider:
    """Hindsight-backed recall/retain plus a local incident journal for the UI."""
    def __init__(self,base_url,api_key,bank_id,timeout,data_path):
        self.base_url=base_url; self.api_key=api_key; self.bank_id=bank_id; self.timeout=timeout; self.local=LocalMemoryProvider(data_path)
        self.bank_path=quote(bank_id,safe="")
    def _request(self,operation,payload):
        url=f"{self.base_url}/v1/default/banks/{self.bank_path}/memories" + ("/recall" if operation=="recall" else "")
        headers={"Content-Type":"application/json","Accept":"application/json"}
        if self.api_key: headers["Authorization"]="Bearer "+self.api_key
        req=Request(url,data=json.dumps(payload,ensure_ascii=False).encode("utf-8"),headers=headers,method="POST")
        try:
            with urlopen(req,timeout=self.timeout) as response:
                raw=response.read(1_048_577)
                if len(raw)>1_048_576: raise ProviderError("HINDSIGHT_RESPONSE_TOO_LARGE","Hindsight returned an unexpectedly large response.",502)
                try: result=json.loads(raw)
                except (json.JSONDecodeError,UnicodeDecodeError) as exc: raise ProviderError("HINDSIGHT_INVALID_RESPONSE","Hindsight returned an unreadable response.",502) from exc
                if not isinstance(result,dict): raise ProviderError("HINDSIGHT_INVALID_RESPONSE","Hindsight returned an unexpected response shape.",502)
                return result
        except HTTPError as exc:
            # Do not reflect remote response bodies: they may contain sensitive diagnostics.
            status=exc.code
            if status in (401,403): raise ProviderError("HINDSIGHT_AUTH_FAILED","Hindsight rejected the configured credentials or permissions. Check the server-side API key.",503) from exc
            if status==402: raise ProviderError("HINDSIGHT_CREDITS_EXHAUSTED","Hindsight reports insufficient credits. Check the configured account.",503) from exc
            if status==404: raise ProviderError("HINDSIGHT_RESOURCE_NOT_FOUND","Hindsight bank or endpoint was not found. Verify the API URL and bank ID.",503) from exc
            if status==429: raise ProviderError("HINDSIGHT_RATE_LIMITED","Hindsight is rate limiting requests. Wait briefly, then retry.",503) from exc
            if status==400: raise ProviderError("HINDSIGHT_REJECTED_REQUEST","Hindsight rejected the request. Check the installed API version and bank configuration.",502) from exc
            raise ProviderError("HINDSIGHT_UPSTREAM_ERROR","Hindsight is temporarily unavailable. Retry shortly.",502) from exc
        except (TimeoutError,socket.timeout) as exc:
            raise ProviderError("HINDSIGHT_TIMEOUT","Hindsight did not respond before the timeout. The operation may have completed; verify before repeating a save.",504) from exc
        except URLError as exc:
            reason=exc.reason
            if isinstance(reason,(TimeoutError,socket.timeout)):
                raise ProviderError("HINDSIGHT_TIMEOUT","Hindsight did not respond before the timeout. The operation may have completed; verify before repeating a save.",504) from exc
            raise ProviderError("HINDSIGHT_NETWORK_ERROR","Could not connect to Hindsight. Check network access and the configured endpoint.",503) from exc
        except OSError as exc:
            raise ProviderError("HINDSIGHT_NETWORK_ERROR","Could not connect to Hindsight. Check network access and the configured endpoint.",503) from exc
    @staticmethod
    def _memory_text(item):
        if isinstance(item,str): return item
        if isinstance(item,dict):
            for k in ("text","content","memory","fact"):
                val=item.get(k)
                if isinstance(val,str): return val
        return ""
    def _recall(self,query):
        result=self._request("recall",{"query":query})
        rows=result.get("results")
        if not isinstance(rows,list): raise ProviderError("HINDSIGHT_INVALID_RESPONSE","Hindsight recall response did not include a results list.",502)
        cleaned=[]
        for row in rows[:10]:
            text=self._memory_text(row)
            if text.strip():
                rid=row.get("id") if isinstance(row,dict) else None
                cleaned.append({"id":str(rid)[:80] if rid else "HINDSIGHT","text":text[:3000]})
        return cleaned
    def analyze(self,service,symptoms):
        memories=self._recall(f"Past production incidents and their symptoms, causes, resolution steps, and outcomes relevant to service {service}: {symptoms}")
        evidence=[]
        for m in memories[:5]:
            # Content is treated as evidence only, never executable instructions.
            evidence.append({"id":m["id"],"title":m["text"][:240],"similarity":None,"resolution":m["text"],"outcome":"Recalled from Hindsight"})
        if evidence:
            actions=[{"priority":"P1","title":"Validate the closest recalled incident","detail":"Compare the recalled symptoms and resolution with current metrics and recent changes. Past fixes are evidence, not instructions; verify the conditions match before acting.","source":evidence[0]["id"]},{"priority":"P1","title":"Stabilize with a reversible mitigation","detail":"Capture error rate, latency, saturation, and dependency health. Apply one bounded, reversible change at a time and verify the expected signal.","source":"Operational practice"},{"priority":"P2","title":"Record the outcome after recovery","detail":"Save confirmed cause, mitigation, and outcome so future responders can recall this incident.","source":"Memory workflow"}]
            summary=f"Hindsight recalled {len(evidence)} relevant memory record{'s' if len(evidence)!=1 else ''}. Validate each against live telemetry before applying a mitigation."
            confidence="Moderate"
        else:
            actions=[{"priority":"P1","title":"Establish incident facts","detail":"Check recent deploys, error-rate and latency trends, saturation, and dependency health. Compare against a known-good window.","source":"Operational practice"},{"priority":"P1","title":"Mitigate safely","detail":"Choose a reversible change, state the expected signal, and assign an owner to verify recovery. Avoid unverified destructive actions.","source":"Operational practice"},{"priority":"P2","title":"Capture what you learn","detail":"Once stable, record the root cause, mitigation, and outcome so future responders can benefit.","source":"Memory workflow"}]
            summary="Hindsight is connected, but returned no matching memories. Start with evidence gathering and a reversible mitigation."
            confidence="Low"
        return {"service":service,"summary":summary,"actions":actions,"evidence":evidence,"confidence":confidence,"memory_mode":"Hindsight"}
    def save(self,service,title,symptoms,resolution,outcome):
        now=datetime.now(timezone.utc).isoformat()
        incident_id="INC-"+__import__("uuid").uuid4().hex[:6].upper()
        content=(f"Production incident {incident_id}. Service: {service}. Title: {title}. "
                 f"Symptoms: {symptoms}. Confirmed resolution: {resolution}. Outcome: {outcome}.")
        # Store in Hindsight first. Do not retry POST automatically: remote commit status
        # after timeout is ambiguous, and duplicate memories are worse than a visible error.
        self._request("retain",{"items":[{"content":content,"context":"SignalOps production incident resolution","timestamp":now}]})
        item={"id":incident_id,"service":service,"title":title,"symptoms":symptoms,"resolution":resolution,"outcome":outcome,"tags":[],"created_at":now}
        try: self.local.save_with_item(item)
        except (OSError,ValueError):
            log.exception("Hindsight retained incident %s but local list indexing failed",incident_id)
            item["index_warning"]="Saved to Hindsight, but the local incident list did not update. Do not resubmit; refresh or repair local storage."
        return item
    def list_incidents(self): return self.local.list_incidents()
    def search(self,q): return self.local.search(q)
