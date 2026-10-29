#!/usr/bin/env python3
"""Replay journaled traces through the E2 cache-economics simulator.

Reads every trace in journal/, replays it under both provider cache models,
and prints a comparison table. Optionally varies seconds_per_step / TTL for
sensitivity analysis.
"""

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from minicache import metrics, simulator  # noqa: E402

JOURNAL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "journal")


def load_traces(journal_dir: str) -> dict:
    traces = {}
    for path in sorted(glob.glob(os.path.join(journal_dir, "*.jsonl"))):
        reqs = []
        with open(path) as f:
            for line in f:
                if line.strip():
                    reqs.append(json.loads(line))
        traces[os.path.basename(path)] = reqs
    return traces


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal", default=JOURNAL_DIR)
    ap.add_argument("--seconds-per-step", type=float, default=30.0)
    ap.add_argument("--ttl", type=int, default=300)
    ap.add_argument("--sensitivity", action="store_true",
                    help="sweep seconds-per-step x TTL")
    args = ap.parse_args()

    traces = load_traces(args.journal)
    if not traces:
        print("no traces found — run experiments/run_profiles.py first")
        return

    table = {}
    print(f"{'trace':<44} {'reqs':>4} {'LCP%':>6} {'inv':>3} "
          f"{'A-hit%':>7} {'A-cost':>7} {'O-hit%':>7} {'O-cost':>7}")
    for name, reqs in traces.items():
        m = metrics.analyze(reqs)
        sim = simulator.replay_requests(reqs, seconds_per_step=args.seconds_per_step,
                                        anthropic_ttl=args.ttl)
        a, o = sim["anthropic"]["summary"], sim["openai"]["summary"]
        table[name] = {
            "metrics": {k: v for k, v in m.items() if k != "pairs"},
            "anthropic": a, "openai": o,
        }
        print(f"{name:<44} {m['n_turn_requests']:>4} "
              f"{m['aggregate_lcp_ratio']*100:>5.1f}% "
              f"{m['n_invalidation_events']:>3} "
              f"{a['overall_hit_ratio']*100:>6.1f}% {a['cost_vs_nocache']:>7.3f} "
              f"{o['overall_hit_ratio']*100:>6.1f}% {o['cost_vs_nocache']:>7.3f}")

    out_path = os.path.join(args.journal, "results")
    os.makedirs(out_path, exist_ok=True)
    res_path = os.path.join(out_path, "latest_replay_table.json")
    with open(res_path, "w") as f:
        json.dump({"seconds_per_step": args.seconds_per_step, "ttl": args.ttl,
                   "table": table}, f, ensure_ascii=False, indent=1)
    print(f"saved: {res_path}")

    if args.sensitivity:
        print("\nsensitivity: overall Anthropic hit ratio by (seconds_per_step, ttl)")
        steps_opts = [10.0, 30.0, 60.0, 120.0]
        ttl_opts = [300, 3600]
        header = "trace".ljust(44) + "".join(
            f"{int(s)}s/ttl{t:>5}".rjust(14) for s in steps_opts for t in ttl_opts)
        print(header)
        sens = {}
        for name, reqs in traces.items():
            row = name.ljust(44)
            sens[name] = {}
            for s in steps_opts:
                for t in ttl_opts:
                    sim = simulator.replay_requests(reqs, seconds_per_step=s,
                                                    anthropic_ttl=t)
                    h = sim["anthropic"]["summary"]["overall_hit_ratio"]
                    sens[name][f"step{int(s)}s_ttl{t}"] = h
                    row += f"{h*100:>13.1f}%"
            print(row)
        sens_path = os.path.join(out_path, "latest_sensitivity.json")
        with open(sens_path, "w") as f:
            json.dump({"steps_opts": steps_opts, "ttl_opts": ttl_opts,
                       "sensitivity": sens}, f, ensure_ascii=False, indent=1)
        print(f"saved: {sens_path}")


if __name__ == "__main__":
    main()
