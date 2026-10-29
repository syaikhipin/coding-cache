#!/usr/bin/env python3
"""Regenerate journal/MANIFEST.json over exactly the analyzed set.

The manifest covers:
  - every top-level journal/*.jsonl trace (the analyzed set)
  - the newest results file of each batch kind (profiles / ablations / sweep)
  - EXPLICITLY: every sweep trace referenced by the newest sweep results
    (resolved from journal/sweep-traces/), with its own section, so the
    current sweep archive is checksummed and distinguishable from the
    superseded files living in the same directory
  - the response files beside each covered trace

latest_replay_table.json / latest_sensitivity.json are derived caches rebuilt
by replay.py and are deliberately excluded: their provenance contract (their
trace coverage must equal the manifest's trace set) is enforced by
verify_manuscript.py instead of by checksums.

Usage: python3 experiments/make_manifest.py [round-tag]
The round-tag labels which review round the artifacts belong to (e.g. "r3").
"""

import glob
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOURNAL = os.path.join(HERE, "journal")
RESULTS = os.path.join(JOURNAL, "results")
SWEEP_DIR = os.path.join(JOURNAL, "sweep-traces")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def newest(kind: str) -> str | None:
    cands = sorted(glob.glob(os.path.join(RESULTS, f"*_{kind}.json")))
    return os.path.basename(cands[-1]) if cands else None


def main():
    round_tag = sys.argv[1] if len(sys.argv) > 1 else ""
    traces = sorted(os.path.basename(p) for p in glob.glob(os.path.join(JOURNAL, "*.jsonl")))
    files = {}
    for t in traces:
        files[f"journal/{t}"] = {"sha256": sha256(os.path.join(JOURNAL, t))}
        resp = os.path.join(JOURNAL, t.replace(".jsonl", ".responses.json"))
        if os.path.exists(resp):
            files[f"journal/{os.path.basename(resp)}"] = {"sha256": sha256(resp)}
    batches = {}
    for kind in ("profiles", "ablations", "sweep"):
        name = newest(kind)
        if name:
            batches[name] = {"kind": kind, "sha256": sha256(os.path.join(RESULTS, name))}

    # current sweep archive: exactly the traces referenced by the newest sweep
    sweep_name = newest("sweep")
    sweep_traces = {}
    if sweep_name:
        rows = json.load(open(os.path.join(RESULTS, sweep_name)))["rows"]
        for r in rows:
            p = os.path.join(SWEEP_DIR, r["trace"])
            key = f"journal/sweep-traces/{r['trace']}"
            if os.path.exists(p):
                entry = {"sha256": sha256(p)}
                resp = p.replace(".jsonl", ".responses.json")
                if os.path.exists(resp):
                    entry["responses_sha256"] = sha256(resp)
                sweep_traces[key] = entry
            else:
                sweep_traces[key] = {"missing": True}

    manifest = {
        "note": "Analyzed set: top-level traces + newest batch results per kind, "
                "+ every sweep trace referenced by the newest sweep results "
                "(checksummed individually; the remaining files under "
                "journal/sweep-traces/ are superseded runs kept as provenance). "
                "latest_replay_table.json / latest_sensitivity.json are derived "
                "caches — verify_manuscript.py enforces that their trace "
                "coverage equals the top-level trace set.",
        "round_tag": round_tag,
        "generated": os.popen("date -u +%Y-%m-%dT%H:%M:%SZ").read().strip(),
        "n_traces": len(traces),
        "traces": files,
        "sweep_traces": sweep_traces,
        "batch_results": batches,
    }
    out = os.path.join(JOURNAL, "MANIFEST.json")
    with open(out, "w") as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
    missing = sum(1 for v in sweep_traces.values() if v.get("missing"))
    print(f"MANIFEST[{round_tag}]: {len(traces)} traces + {len(sweep_traces)} "
          f"sweep traces ({missing} missing) + {len(batches)} batch results -> {out}")


if __name__ == "__main__":
    main()
