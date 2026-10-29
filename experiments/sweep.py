#!/usr/bin/env python3
"""E4: robustness sweep — does the archetype ranking survive variance?

Round-1 review weakness: all conclusions rested on ONE 12-step session with
one churn schedule, one compaction threshold, one task. This driver re-runs
the three archetypes across a deterministic grid:

  churn schedules  (env_change_steps): (4,8) default / (2,6) early / (6,10) late /
                    (3,9)+(5,7) irregular (non-uniform gaps) / none
  compaction       threshold: 12400 default / 11900 early-fire / never-fire
  session length   steps: 12 / 20 / 30
  task             toy-repo (bug-fix) / toy-features (add-feature churn shape) /
                    repo2 (second fixture: web-service bug-fix)

Plus a composition control (Round-3 review): every `none` condition is also
run with MC's warmer disabled (mc_nowarm rows), separating warmer self-hit
composition from turn-level caching in the null cells.

Every cell is scripted (deterministic) and replayed through both cache models.
Output: one row per (archetype, condition) with E1 + E2 summaries, persisted
to journal/results/<ts>_sweep.json.
"""

import itertools
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dataclasses import replace

from minicache import harness, metrics, simulator  # noqa: E402
from minicache.config import PROFILES, RunConfig  # noqa: E402

SCHEDULES = {
    "mid": (4, 8),
    "early": (2, 6),
    "late": (6, 10),
    "irr1": (3, 9),   # irregular: uneven gap between mutations
    "irr2": (5, 7),   # irregular: adjacent mutations
    "none": (10 ** 9, 10 ** 9 + 1),  # never fires
}
LENGTHS = {"s12": 12, "s20": 20, "s30": 30}
THRESHOLDS = {"compact-mid": 12400, "compact-early": 11900, "compact-never": None}
# "none" is the TRUE no-churn condition: environment changes AND tool
# availability churn are both disabled (Round-2 fix — the tool churn was
# previously hard-coded and survived the "none" schedule).
REPO2_COMPACT_AT = 6500  # smaller fixture -> proportionally earlier trigger (fires ~step 7)

FEATURES_TASK = (
    "This repository needs three small features. Work through them one file at "
    "a time: read the module, run the tests, add the feature, and re-run to "
    "confirm. Extend calc.py, shapes.py, and textutil.py in that order."
)


def run_cell(archetype: str, sched: str, length: str, thresh: str, task: str,
             seed: int = 0, warmer_override: bool | None = None) -> dict:
    compact_at = THRESHOLDS[thresh] if task != "repo2" else (
        None if THRESHOLDS[thresh] is None else REPO2_COMPACT_AT)
    cfg = RunConfig(
        max_turns=LENGTHS[length],
        seed=seed,
        env_change_steps=SCHEDULES[sched],
        compact_at_tokens=compact_at,
        tool_churn=(sched != "none"),
    )
    label = f"{archetype}|{sched}|{length}|{thresh}|{task}"
    mech = PROFILES[archetype]()
    if warmer_override is not None:
        mech.cache_warmer = warmer_override
        label = "mc_nowarm|" + label.split("|", 1)[1]

    if task == "features":
        out = harness.run_profile(label, mech, cfg, task_prompt=FEATURES_TASK,
                                  task="toy-features")
    elif task == "repo2":
        from minicache.tasks import REPO2_TASK_PROMPT, REPO2_AGENT_FILES_HINT
        out = harness.run_profile(label, mech, cfg,
                                  task_prompt=REPO2_TASK_PROMPT,
                                  agent_files_hint=REPO2_AGENT_FILES_HINT,
                                  task="repo2")
    else:
        out = harness.run_profile(label, mech, cfg, task="toy-repo")

    m = metrics.analyze(out["requests"])
    sim = simulator.replay_requests(out["requests"])
    a = sim["anthropic"]["summary"]
    o = sim["openai"]["summary"]
    return {
        "label": label,
        "archetype": archetype,
        "warmer": mech.cache_warmer,
        "schedule": sched,
        "length": length,
        "threshold": thresh,
        "task": task,
        "tool_churn": (sched != "none"),
        "trace": os.path.basename(out["trace"]),
        "n_requests": len(out["requests"]),
        "lcp": m["aggregate_lcp_ratio"],
        "invalidations": m["n_invalidation_events"],
        "transcript_resets": m["n_transcript_reset_events"],
        "a_hit": a["overall_hit_ratio"],
        "a_hit_turn": a["turn_only_hit_ratio"],
        "a_cost": a["cost_vs_nocache"],
        "a_cost_units": a["total_cost_units"],
        "o_hit": o["overall_hit_ratio"],
        "o_hit_turn": o["turn_only_hit_ratio"],
        "o_cost": o["cost_vs_nocache"],
        "o_cost_units": o["total_cost_units"],
    }


def main():
    archetypes = ("pfs", "rc", "mc")
    cells = list(itertools.product(
        archetypes,
        list(SCHEDULES), list(LENGTHS), list(THRESHOLDS),
    ))
    rows = []
    for arch, sched, length, thresh in cells:
        r = run_cell(arch, sched, length, thresh, "bugfix")
        rows.append(r)
        print(f"{r['label']:<48} lcp={r['lcp']:.3f} a_hit={r['a_hit']:.3f} "
              f"a_cost={r['a_cost']:.3f} o_hit={r['o_hit']:.3f}")
    # alternate task shapes and second fixture, default condition only
    for task in ("features", "repo2"):
        for arch in archetypes:
            r = run_cell(arch, "mid", "s12", "compact-mid", task)
            rows.append(r)
            print(f"{r['label']:<48} lcp={r['lcp']:.3f} a_hit={r['a_hit']:.3f} "
                  f"a_cost={r['a_cost']:.3f} o_hit={r['o_hit']:.3f}")
    # composition control (Round-3): MC without its warmer in every TRUE
    # no-churn condition — separates warmer self-hit composition from
    # turn-level caching in the null cells
    for length in LENGTHS:
        for thresh in THRESHOLDS:
            r = run_cell("mc", "none", length, thresh, "bugfix", warmer_override=False)
            rows.append(r)
            print(f"{r['label']:<48} lcp={r['lcp']:.3f} a_hit={r['a_hit']:.3f} "
                  f"a_cost={r['a_cost']:.3f} o_hit={r['o_hit']:.3f}")

    from minicache import recorder
    out_path = recorder.save_results("sweep", {
        "config": {"schedules": list(SCHEDULES), "lengths": LENGTHS,
                   "thresholds": THRESHOLDS,
                   "tasks": ["bugfix", "features", "repo2"],
                   "note": "sweep cells DO persist JSONL traces (harness writes "
                           "every run); after persisting results they are moved to "
                           "journal/sweep-traces/ to keep the top-level journal to "
                           "the analyzed batches — every cell is regenerable from "
                           "this repo at seed 0. 'none' schedule disables BOTH env "
                           "changes and tool churn (true no-churn). 'features' task "
                           "changes the user task prompt AND the scripted traffic. "
                           "'repo2' runs the second fixture repository. mc_nowarm "
                           "rows are MC with the warmer disabled (composition "
                           "control for the no-churn null cells)."},
        "rows": rows,
    })
    # move this sweep's traces out of the top-level journal (archive, not delete)
    import shutil
    archive = os.path.join(recorder.JOURNAL_DIR, "sweep-traces")
    os.makedirs(archive, exist_ok=True)
    moved = 0
    for r in rows:
        for suffix in (".jsonl", ".responses.json"):
            src = os.path.join(recorder.JOURNAL_DIR, r["trace"].replace(".jsonl", suffix)) \
                if suffix == ".jsonl" else \
                os.path.join(recorder.JOURNAL_DIR, r["trace"].replace(".jsonl", suffix))
            if os.path.exists(src):
                shutil.move(src, os.path.join(archive, os.path.basename(src)))
                moved += 1
    print(f"\nsaved: {out_path} ({len(rows)} rows); archived {moved} trace files to journal/sweep-traces/")

    # ranking-stability summary
    print("\n=== archetype ranking by a_hit, per condition ===")
    conds = {}
    for r in rows:
        key = (r["schedule"], r["length"], r["threshold"], r["task"])
        conds.setdefault(key, []).append(r)
    pfs_wins = 0
    for key, rs in sorted(conds.items()):
        order = sorted(rs, key=lambda r: -r["a_hit"])
        names = [r["archetype"] for r in order]
        if names[0] == "pfs":
            pfs_wins += 1
        print(f"{key}: " + " > ".join("%s(%.3f)" % (r["archetype"], r["a_hit"]) for r in order))
    print(f"\nPFS first in {pfs_wins}/{len(conds)} conditions (a_hit)")


if __name__ == "__main__":
    main()
