"""Journal writer: one JSONL trace per run + INDEX.md entry per run.

The journal is append-only: runs never overwrite prior traces. INDEX.md carries
one human-readable row per run for the thesis narrative.
"""

import datetime as _dt
import json
import os

JOURNAL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "journal")


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def init_journal() -> str:
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    index = os.path.join(JOURNAL_DIR, "INDEX.md")
    if not os.path.exists(index):
        with open(index, "w") as f:
            f.write("# Experiment Journal\n\n"
                    "| run | ts | profile | task | steps | api | trace |\n"
                    "|---|---|---|---|---|---|---|\n")
    return index


def write_run(record: dict, requests: list, responses: list) -> str:
    """record: run metadata; requests: wire requests; responses: assistant turns."""
    init_journal()
    ts = record.get("ts", _now()).replace(":", "").replace("-", "")
    slug = f"{ts}_{record['profile']}_s{record.get('seed', 0)}"
    trace_path = os.path.join(JOURNAL_DIR, slug + ".jsonl")
    with open(trace_path, "w") as f:
        for item in requests:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    # responses stored beside the trace
    resp_path = os.path.join(JOURNAL_DIR, slug + ".responses.json")
    with open(resp_path, "w") as f:
        json.dump(responses, f, ensure_ascii=False, indent=1)
    index = os.path.join(JOURNAL_DIR, "INDEX.md")
    with open(index, "a") as f:
        f.write(
            f"| {slug} | {record.get('ts','')} | {record['profile']} | "
            f"{record.get('task','toy-repo')} | {record.get('steps','?')} | "
            f"{record.get('api_mode','?')} | [{slug}.jsonl](./{slug}.jsonl) |\n"
        )
    return trace_path


def save_results(kind: str, payload: dict) -> str:
    """Persist computed results (never overwritten: timestamped filename).

    The journal is append-only: a re-experiment writes a NEW results file next
    to the old ones, so any two runs can be diffed later.
    """
    results_dir = os.path.join(JOURNAL_DIR, "results")
    os.makedirs(results_dir, exist_ok=True)
    ts = _now().replace(":", "").replace("-", "")
    path = os.path.join(results_dir, f"{ts}_{kind}.json")
    with open(path, "w") as f:
        json.dump({"ts": _now(), "kind": kind, **payload}, f,
                  ensure_ascii=False, indent=1)
    return path
