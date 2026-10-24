"""Measure the actual local queue endpoint using an isolated 50,000-row ledger."""

import http.client
import json
import shutil
import tempfile
import threading
import time
from pathlib import Path

from job_agent.common import timestamp, write_json
from job_agent.server import ReviewServer
from job_agent.store import Store


def main():
    project = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(dir=project / "data/benchmarks") as directory:
        root = Path(directory)
        shutil.copytree(project / "config", root / "config")
        server = ReviewServer(root, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        store = Store(root / "data/ledger.sqlite3")
        try:
            with store.transaction():
                store.db.executemany(
                    "INSERT INTO opportunities(company_key,opportunity_key,payload,material_hash,first_seen_at,state) VALUES(?,?,?,?,?,?)",
                    ((f"company{i % 100}.example", f"synthetic|{i}", json.dumps({"company": f"Company {i % 100}", "role": "Python Backend Engineer", "tier": "GOOD", "location": "India", "description": f"Python SQL Docker shard{i % 100}"}), "synthetic", timestamp(), "NEEDS_CLARIFICATION") for i in range(50_000)),
                )
            thread.start()
            measurements = {}
            for query in ("q=Python", "q=Python%20shard12&tier=GOOD&family=backend"):
                samples = []
                for index in range(35):
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    start = time.perf_counter()
                    connection.request("GET", "/api/v1/queue?limit=50&state=NEEDS_CLARIFICATION&" + query, headers={"Cookie": "agent_session=" + server.token})
                    response = connection.getresponse()
                    result = json.loads(response.read())
                    connection.close()
                    assert response.status == 200 and len(result["items"]) == 50
                    if index >= 5:
                        samples.append((time.perf_counter() - start) * 1000)
                measurements[query] = {"p95_ms": round(sorted(samples)[28], 2)}
            result = {"at": timestamp(), "synthetic_records": 50_000, "endpoint": "/api/v1/queue", "queries": measurements, "all_p95_under_50ms": all(item["p95_ms"] < 50 for item in measurements.values()), "note": "Actual authenticated HTTP endpoint on this computer; warm requests; synthetic ledger only."}
            write_json(project / "data/benchmarks/review-api.json", result)
            print(json.dumps(result, indent=2))
        finally:
            if thread.is_alive():
                server.shutdown()
                thread.join(timeout=3)
            server.server_close()
            store.close()


if __name__ == "__main__":
    main()
