#!/usr/bin/env python3
"""Run one or more profiles on the toy task and journal the wire streams."""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from minicache import harness, metrics, recorder, simulator  # noqa: E402
from minicache.config import PROFILES, RunConfig  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profiles", default="pfs,rc,mc")
    ap.add_argument("--api", choices=["script", "live"], default="script")
    ap.add_argument("--steps", type=int, default=12)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--compact-at", type=int, default=12400)
    ap.add_argument("--replay", action="store_true", help="also run the simulator")
    args = ap.parse_args()

    recorder.init_journal()
    cfg = RunConfig(max_turns=args.steps, seed=args.seed, compact_at_tokens=args.compact_at)

    results = {}
    for name in args.profiles.split(","):
        name = name.strip()
        mech = PROFILES[name]()
        out = harness.run_profile(name, mech, cfg, api_mode=args.api)
        m = metrics.analyze(out["requests"])
        r = {"record": out["record"],
             "trace": out["trace"],
             "metrics": {k: v for k, v in m.items() if k != "pairs"},
             "metrics_pairs": m["pairs"]}
        if args.replay:
            r["sim"] = simulator.replay_requests(out["requests"])
        results[name] = r
        print(f"== {name} ==")
        print(f"  trace: {out['trace']}")
        print(f"  E1 aggregate LCP ratio: {m['aggregate_lcp_ratio']}")
        print(f"  E1 invalidation events: {m['n_invalidation_events']}"
              + (f" {m['invalidation_events']}" if m["n_invalidation_events"] else ""))
        print(f"  breakpoints in last request: {m['breakpoints_last_request']}")
        if args.replay:
            for prov in ("anthropic", "openai"):
                s = r["sim"][prov]["summary"]
                print(f"  E2 {prov}: hit={s['overall_hit_ratio']} "
                      f"cost_vs_nocache={s['cost_vs_nocache']}")

    # persist everything for reuse / re-experiment comparison (never overwritten:
    # each run gets its own timestamped results file)
    out_path = recorder.save_results("profiles", {
        "config": {"steps": args.steps, "seed": args.seed,
                   "compact_at": args.compact_at, "api": args.api,
                   "profiles": args.profiles},
        "results": results,
    })
    print(f"saved: {out_path}")


if __name__ == "__main__":
    main()
