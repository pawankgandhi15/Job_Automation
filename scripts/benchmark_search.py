"""Offline search benchmark; synthetic data only, never opens the live ledger."""
import json
import statistics
import tempfile
import time
from pathlib import Path

from job_agent.common import timestamp, write_json
from job_agent.store import Store


def main():
    root = Path(__file__).resolve().parent.parent
    directory = root / "data/benchmarks"
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory) as temporary:
        store = Store(Path(temporary) / "synthetic.sqlite3")
        try:
            with store.transaction():
                store.db.executemany(
                    "INSERT INTO opportunities(company_key,opportunity_key,payload,material_hash,first_seen_at,state) VALUES(?,?,?,?,?,?)",
                    ((f"company{i % 100}.example", f"synthetic|{i}", json.dumps({"company": f"Company {i % 100}", "role": "Python Backend Engineer", "location": "India", "description": f"Python services, SQL, Docker, infrastructure. shard{i % 100}"}), "synthetic", timestamp(), "NEEDS_CLARIFICATION") for i in range(50_000)),
                )
            results = {}
            for query in ("Python", "Python shard12", "nonexistentterm"):
                samples = []
                for attempt in range(65):
                    started = time.perf_counter()
                    store.search(query, state="NEEDS_CLARIFICATION", limit=50)
                    elapsed = (time.perf_counter() - started) * 1000
                    if attempt >= 5:
                        samples.append(elapsed)
                results[query] = {"median_ms": round(statistics.median(samples), 2), "p95_ms": round(sorted(samples)[56], 2)}
            result = {"at": timestamp(), "synthetic_records": 50_000, "queries": results, "all_p95_under_50ms": all(item["p95_ms"] < 50 for item in results.values()), "note": "Local hardware measurement; warm queries; no live ledger access. Not a portable performance guarantee."}
            write_json(directory / "latest.json", result)
            print(json.dumps(result, indent=2))
        finally:
            store.close()


if __name__ == "__main__":
    main()
