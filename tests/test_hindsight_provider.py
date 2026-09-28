"""Offline tests for the documented Hindsight REST contract (stdlib unittest)."""
import json, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from hindsight_provider import HindsightMemoryProvider, ProviderError

class FakeHindsightHandler(BaseHTTPRequestHandler):
    last_path=None; last_payload=None; status=200; payload={"results":[{"id":"fact-123","text":"Production incident INC-OLD. Service: Payments API. Title: pool saturation. Confirmed resolution: bounded pool and backpressure."}]}
    def log_message(self,*args): pass
    def do_POST(self):
        type(self).last_path=self.path
        length=int(self.headers.get("Content-Length","0")); type(self).last_payload=json.loads(self.rfile.read(length))
        self.send_response(type(self).status); self.send_header("Content-Type","application/json"); self.end_headers(); self.wfile.write(json.dumps(type(self).payload).encode())

class HindsightProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(("127.0.0.1",0),FakeHindsightHandler); cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True); cls.thread.start()
    @classmethod
    def tearDownClass(cls): cls.server.shutdown(); cls.server.server_close(); cls.thread.join(timeout=2)
    def setUp(self):
        FakeHindsightHandler.status=200; FakeHindsightHandler.payload={"results":[{"id":"fact-123","text":"Production incident INC-OLD. Service: Payments API. Title: pool saturation. Confirmed resolution: bounded pool and backpressure."}]}
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.provider=HindsightMemoryProvider(f"http://127.0.0.1:{self.server.server_port}","test-key","team-test",2,Path(self.temp.name)/"incidents.json")
    def test_recall_maps_hindsight_evidence(self):
        brief=self.provider.analyze("Payments API","database pool saturation and timeouts")
        self.assertEqual(FakeHindsightHandler.last_path,"/v1/default/banks/team-test/memories/recall")
        self.assertIn("query",FakeHindsightHandler.last_payload)
        self.assertEqual(brief["memory_mode"],"Hindsight"); self.assertEqual(brief["evidence"][0]["id"],"fact-123")
        self.assertEqual(brief["confidence"],"Moderate")
    def test_save_retains_then_indexes_incident(self):
        item=self.provider.save("Payments API","Pool issue resolved","Connection pool saturated after traffic","Enabled backpressure and restored service","Resolved · 12 min")
        self.assertEqual(FakeHindsightHandler.last_path,"/v1/default/banks/team-test/memories")
        self.assertEqual(FakeHindsightHandler.last_payload["items"][0]["context"],"SignalOps production incident resolution")
        self.assertIn(item["id"],FakeHindsightHandler.last_payload["items"][0]["content"])
        self.assertEqual(self.provider.list_incidents()[0]["id"],item["id"])
    def test_malformed_recall_is_not_silently_empty(self):
        FakeHindsightHandler.payload={"unexpected":[]}
        with self.assertRaises(ProviderError) as ctx:self.provider.analyze("Payments API","connection pool timeouts")
        self.assertEqual(ctx.exception.code,"HINDSIGHT_INVALID_RESPONSE")
    def test_auth_failure_is_sanitized(self):
        FakeHindsightHandler.status=401; FakeHindsightHandler.payload={"error":"secret remote detail"}
        with self.assertRaises(ProviderError) as ctx:self.provider.analyze("Payments API","connection pool timeouts")
        self.assertEqual(ctx.exception.code,"HINDSIGHT_AUTH_FAILED")
        self.assertNotIn("secret",ctx.exception.message)

if __name__=="__main__": unittest.main()
