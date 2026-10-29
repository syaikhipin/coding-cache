#!/usr/bin/env python3
"""E3: single-mechanism ablations.

Start from each harness profile and flip ONE mechanism at a time to another
harness's setting. Because the scripted agent and all schedules are identical
across runs, any metric delta is attributable to the flipped mechanism alone.
"""

import argparse
import dataclasses
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from minicache import harness, metrics, recorder, simulator  # noqa: E402
from minicache.config import PROFILES, RunConfig  # noqa: E402

# each ablation: (label, base archetype, dimension, new value)
ABLATIONS = [
    ("A1 RC+frozen-env",           "rc",  "env_mode",       "frozen"),
    ("A2 RC+frozen-tools",         "rc",  "tool_order",     "frozen"),
    ("A3 RC+stratified3",          "rc",  "system_stratification", "stratified3"),
    ("A4 RC+per_stratum_bp",       "rc",  "breakpoint_style", "per_stratum"),
    ("A5 RC+cache_aware",          "rc",  "compaction",     "cache_aware"),
    ("A6 RC+warmer",               "rc",  "cache_warmer",   True),
    ("B1 MC+frozen-tools",         "mc",  "tool_order",     "frozen"),
    ("B2 MC-no-warmer",            "mc",  "cache_warmer",   False),
    ("B3 MC+frozen-env",           "mc",  "env_mode",       "frozen"),
    ("B4 MC+cache_aware",          "mc",  "compaction",     "cache_aware"),
    ("B5 MC+per_stratum_bp",       "mc",  "breakpoint_style", "per_stratum"),
    ("C1 PFS-per_step-env",        "pfs", "env_mode",       "per_step"),
    ("C2 PFS-per_step-tools",      "pfs", "tool_order",     "per_step_sorted"),
    ("C3 PFS-cache_dead",          "pfs", "compaction",     "cache_dead"),
    ("C4 PFS-single-system",       "pfs", "system_stratification", "single"),
    ("C5 PFS+warmer",              "pfs", "cache_warmer",   True),
]


def run_variant(label, base, field, value, cfg):
    mech = PROFILES[base]()
    if field is not None:
        setattr(mech, field, value)
    out = harness.run_profile(label.replace(" ", "_").replace("->", "-"), mech, cfg)
    m = metrics.analyze(out["requests"])
    sim = simulator.replay_requests(out["requests"])
    a = sim["anthropic"]["summary"]
    return {
        "label": label, "base": base, "field": field, "value": str(value),
        "mechanisms": mech.summary(),
        "lcp": m["aggregate_lcp_ratio"],
        "invalidations": m["n_invalidation_events"],
        "a_hit": a["overall_hit_ratio"],
        "a_cost": a["cost_vs_nocache"],
        "trace": os.path.basename(out["trace"]),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=12)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--compact-at", type=int, default=12400)
    args = ap.parse_args()

    recorder.init_journal()
    cfg = RunConfig(max_turns=args.steps, seed=args.seed, compact_at_tokens=args.compact_at)

    rows = []
    # baselines first
    for name in ("pfs", "rc", "mc"):
        rows.append(run_variant(f"base-{name}", name, None, None, cfg))
    for label, base, field, value in ABLATIONS:
        rows.append(run_variant(label, base, field, value, cfg))

    base_by_name = {r["label"]: r for r in rows if r["label"].startswith("base-")}
    print(f"\n{'variant':<28} {'LCP':>6} {'inv':>3} {'A-hit':>7} {'A-cost':>7}  dLCP   dHit")
    for r in rows:
        if r["label"].startswith("base-"):
            print(f"{r['label']:<28} {r['lcp']:>6.3f} {r['invalidations']:>3} "
                  f"{r['a_hit']*100:>6.1f}% {r['a_cost']:>7.3f}")
        else:
            b = base_by_name["base-" + r["base"]]
            dl = r["lcp"] - b["lcp"]
            dh = r["a_hit"] - b["a_hit"]
            r["d_lcp"] = round(dl, 4)
            r["d_hit_pp"] = round(dh * 100, 2)
            print(f"{r['label']:<28} {r['lcp']:>6.3f} {r['invalidations']:>3} "
                  f"{r['a_hit']*100:>6.1f}% {r['a_cost']:>7.3f}  "
                  f"{dl:+.3f} {dh*100:+5.1f}pp")

    out_path = recorder.save_results("ablations", {
        "config": {"steps": args.steps, "seed": args.seed,
                   "compact_at": args.compact_at},
        "rows": rows,
    })
    print(f"\nsaved: {out_path}")


if __name__ == "__main__":
    main()
