"""Memory provider boundary and deterministic local demo implementation."""
from __future__ import annotations
import json, threading, uuid
from datetime import datetime, timezone
from pathlib import Path


SEED=[
 {"id":"INC-2481","service":"Payments API","title":"Elevated payment timeouts after connection pool saturation","symptoms":"p95 latency climbed above 4 seconds; timeout rate increased after a traffic spike. Database connections reached the configured maximum.","resolution":"Raised pool limit from 40 to 80 after validating DB headroom, enabled bounded acquisition timeout, and deployed queue backpressure. Restarted workers gradually.","outcome":"Resolved · 18 min","tags":["database","latency","connection pool"],"created_at":"2026-09-21T09:42:00Z"},
 {"id":"INC-2397","service":"Payments API","title":"Retry storm amplified downstream ledger slowness","symptoms":"Payment retries multiplied during intermittent ledger delays, creating a sharp rise in request volume and queue depth.","resolution":"Added exponential backoff with jitter, capped retries at 3, and applied a circuit breaker to the ledger client. Drained queue at a controlled rate.","outcome":"Resolved · 31 min","tags":["retries","queue","ledger"],"created_at":"2026-09-04T16:18:00Z"},
 {"id":"INC-2320","service":"Checkout","title":"Checkout errors following cache invalidation","symptoms":"5xx responses rose after a cache flush; origin CPU saturated as all product reads missed cache simultaneously.","resolution":"Staggered cache warming for the highest-traffic keys, restored TTLs, and rate-limited origin requests. Added a runbook step to avoid global invalidation during peak hours.","outcome":"Resolved · 24 min","tags":["cache","5xx","traffic"],"created_at":"2026-08-22T12:05:00Z"},
 {"id":"INC-2256","service":"Identity","title":"Token verification latency from signing key refresh","symptoms":"Authentication requests became slow and intermittently failed while signing keys refreshed across regions.","resolution":"Kept the previous key active during propagation, verified region health, then rotated. Added monitoring for key-fetch latency and a rollback procedure.","outcome":"Resolved · 12 min","tags":["authentication","latency","keys"],"created_at":"2026-08-02T07:30:00Z"}
]
class LocalMemoryProvider:
    def __init__(self,path:Path): self.path=path; self.lock=threading.RLock(); self.path.parent.mkdir(parents=True,exist_ok=True)
    def _load(self):
        if not self.path.exists():
            tmp=self.path.with_suffix(".tmp"); tmp.write_text(json.dumps(SEED,indent=2),encoding="utf-8"); tmp.replace(self.path)
        try:
            data=json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data,list): raise ValueError("expected incident list")
            return data
        except (json.JSONDecodeError,ValueError) as exc: raise OSError("Incident memory is corrupt; restore a valid JSON list") from exc
    def list_incidents(self):
        with self.lock: return sorted(self._load(),key=lambda x:x.get("created_at",""),reverse=True)
    @staticmethod
    def _score(q,incident):
        qtokens=set(q.lower().split()); hay=" ".join([incident.get("service",""),incident.get("title",""),incident.get("symptoms",""),incident.get("resolution","")," ".join(incident.get("tags",[]))]).lower()
        toks=set(hay.split()); overlap=len(qtokens&toks)/max(1,len(qtokens)); phrase=0.25 if q.lower() in hay else 0
        return min(0.99,overlap+phrase)
    def search(self,q):
        with self.lock: rows=self._load()
        return sorted([dict(i,similarity=round(self._score(q,i),2)) for i in rows if self._score(q,i)>0],key=lambda x:x["similarity"],reverse=True)[:10]
    def analyze(self,service,symptoms):
        query=f"{service} {symptoms}"; matches=self.search(query)[:3]
        evidence=[{"id":m["id"],"title":m["title"],"similarity":m["similarity"],"resolution":m["resolution"],"outcome":m["outcome"]} for m in matches]
        top=matches[0] if matches else None
        actions=[]
        if top:
            actions=[{"priority":"P1","title":"Check the closest precedent","detail":f"Review {top['id']} ({top['title']}) and confirm its trigger matches current telemetry before applying the recorded mitigation.","source":top["id"]},{"priority":"P1","title":"Stabilize before changing multiple variables","detail":"Capture current error rate, p95 latency, saturation, and recent deploys. Prefer reversible mitigation and verify impact after each step.","source":"Operational practice"},{"priority":"P2","title":"Protect downstream dependencies","detail":"Inspect queue depth and retry volume; use bounded retries, backoff, or circuit breaking if pressure is propagating.","source":top["id"]}]
        else:
            actions=[{"priority":"P1","title":"Establish incident facts","detail":"Check recent deploys, error-rate and latency trends, saturation, and dependency health. Compare against a known-good window.","source":"Operational practice"},{"priority":"P1","title":"Mitigate safely","detail":"Choose a reversible change, state the expected signal, and assign an owner to verify recovery. Avoid unverified destructive actions.","source":"Operational practice"},{"priority":"P2","title":"Capture what you learn","detail":"Once stable, record the root cause, mitigation, and outcome so future responders can benefit.","source":"Memory workflow"}]
        return {"service":service,"summary":f"{'Found '+str(len(matches))+' relevant precedent'+('s' if len(matches)!=1 else '')+'.' if matches else 'No close precedent found; start with evidence gathering and a reversible mitigation.'}","actions":actions,"evidence":evidence,"confidence":"High" if top and top['similarity']>.35 else "Moderate" if top else "Low","memory_mode":"Local demo memory"}
    def save(self,service,title,symptoms,resolution,outcome):
        item={"id":"INC-"+uuid.uuid4().hex[:6].upper(),"service":service,"title":title,"symptoms":symptoms,"resolution":resolution,"outcome":outcome,"tags":list(set(symptoms.lower().split()) & {"latency","timeout","database","cache","queue","retry","5xx","deploy"}),"created_at":datetime.now(timezone.utc).isoformat()}
        return self.save_with_item(item)
    def save_with_item(self,item):
        """Persist one complete incident atomically while serializing in-process writers."""
        with self.lock:
            rows=self._load()
            if any(existing.get("id")==item.get("id") for existing in rows):
                raise ValueError("incident id already exists")
            rows.append(item)
            tmp=self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(rows,indent=2,ensure_ascii=False),encoding="utf-8")
            tmp.replace(self.path)
            return item
