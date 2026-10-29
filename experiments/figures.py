#!/usr/bin/env python3
"""Publication figures from persisted journal results (journal/results/*.json).

Nature-portfolio style: single-column 89 mm / double-column 183 mm widths,
300 dpi, colorblind-safe palette, sans-serif. Every figure is regenerated
from the saved result files — no re-experiment needed.
"""

import glob
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(HERE, "journal", "results")
FIGDIR = os.path.join(HERE, "paper", "figures")
os.makedirs(FIGDIR, exist_ok=True)

MM = 1 / 25.4
plt.rcParams.update({
    "font.size": 7, "font.family": "sans-serif",
    "pdf.fonttype": 42, "ps.fonttype": 42,  # embed TrueType (Type 42), not Type 3
    "axes.titlesize": 7, "axes.labelsize": 7,
    "xtick.labelsize": 6.5, "ytick.labelsize": 6.5,
    "legend.fontsize": 6.5, "legend.frameon": False,
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.dpi": 300, "savefig.dpi": 300,
    "axes.grid": True, "grid.alpha": 0.25, "grid.linewidth": 0.4,
})

sys.path.insert(0, HERE)
from minicache import simulator as sim_mod  # noqa: E402

# Okabe-Ito colorblind-safe palette
C = {"PFS": "#0072B2", "MC": "#E69F00", "RC": "#D55E00"}
ORDER = ["PFS", "MC", "RC"]
KEY = {"PFS": "pfs", "MC": "mc", "RC": "rc"}  # results-file keys

def _latest(kind: str) -> str:
    """Newest results file of a kind (timestamped names) — figures can never
    go stale against the batches (Round-2 provenance fix)."""
    cands = sorted(g for g in glob.glob(os.path.join(RESULTS, f"*_{kind}.json")))
    if not cands:
        raise FileNotFoundError(f"no {kind} results in {RESULTS}")
    return cands[-1]


profiles = json.load(open(_latest("profiles")))
ablations = json.load(open(_latest("ablations")))
sens = json.load(open(os.path.join(RESULTS, "latest_sensitivity.json")))


def _replay(name, **kw):
    """Recompute simulation from the persisted trace (profiles files do not
    carry the sim block; the trace is the source of truth)."""
    import json as _json
    import os as _os
    rec = profiles["results"][KEY[name]]
    t = rec["trace"]
    # Resolve trace paths against journal/ regardless of how the batch
    # runner recorded them (absolute on the recording machine, relative in
    # a relocated bundle) — same convention as verify_manuscript.py.
    if not _os.path.isabs(t) and not _os.path.exists(t):
        t = _os.path.join(HERE, "journal", _os.path.basename(t))
    reqs = [_json.loads(l) for l in open(t) if l.strip()]
    return sim_mod.replay_requests(reqs, **kw)


def a_summary(name):
    return _replay(name)["anthropic"]["summary"]


def o_summary(name):
    return _replay(name)["openai"]["summary"]


# --- Fig 1: prefix stability per request stream ----------------------------
fig, axes = plt.subplots(1, 2, figsize=(183 * MM, 60 * MM))
ax = axes[0]
for name in ORDER:
    pairs = profiles["results"][KEY[name]]["metrics_pairs"]
    xs = [p["to_step"] for p in pairs]
    ys = [p["lcp_ratio_prev"] * 100 for p in pairs]
    ax.plot(xs, ys, "-o", ms=2.2, lw=0.9, color=C[name], label=name)
for x in (4, 8):
    ax.axvline(x, color="grey", ls=":", lw=0.6)
ax.set_xlabel("Request (step)")
ax.set_ylabel("Pairwise LCP ratio (%)")
ax.set_title("a  Prefix stability between consecutive requests", loc="left")
ax.legend(loc="lower left", ncol=3, columnspacing=1.0, handlelength=1.2)
ax.set_ylim(0, 105)

ax = axes[1]
rows = ablations["rows"]
bases = {r["base"]: r for r in rows if r["label"].startswith("base-")}
abl = [r for r in rows if not r["label"].startswith("base-")]
# group by which base archetype the ablation perturbs, order by effect
groups = [("RC→stability dims", [r for r in abl if r["base"] == "rc"]),
          ("MC→dims", [r for r in abl if r["base"] == "mc"]),
          ("PFS→dims", [r for r in abl if r["base"] == "pfs"])]
ypos, ylab, vals, cols = [], [], [], []
y = 0
for gname, rs in groups:
    rs = sorted(rs, key=lambda r: r["d_hit_pp"])
    for r in rs:
        ypos.append(y)
        ylab.append(r["label"].replace("_", " "))
        vals.append(r["d_hit_pp"])
        cols.append(C[r["base"].upper()])
        y += 1
    y += 0.8
ax.barh(ypos, vals, color=cols, height=0.62)
ax.set_yticks(ypos)
ax.set_yticklabels(ylab)
ax.axvline(0, color="k", lw=0.7)
ax.invert_yaxis()
ax.set_xlabel("Δ Anthropic-cache hit ratio (pp)")
ax.set_title("b  Single-dimension ablation effects", loc="left")
ax.grid(axis="y", alpha=0)

fig.tight_layout()
fig.savefig(os.path.join(FIGDIR, "fig1_stability_ablations.pdf"))
fig.savefig(os.path.join(FIGDIR, "fig1_stability_ablations.png"))
plt.close(fig)

# --- Fig 2: cost economics + sensitivity -----------------------------------
fig, axes = plt.subplots(1, 3, figsize=(183 * MM, 58 * MM))

ax = axes[0]
x = np.arange(3)
w = 0.38
for i, prov in enumerate(("anthropic", "openai")):
    vals = [a_summary(n)["cost_vs_nocache"] if prov == "anthropic"
            else o_summary(n)["cost_vs_nocache"] for n in ORDER]
    ax.bar(x + (i - 0.5) * w, vals, w, color=["#4477AA", "#BBBBBB"][i],
           label="breakpoint pricing" if i == 0 else "automatic-prefix pricing")
ax.set_xticks(x)
ax.set_xticklabels(ORDER)
ax.axhline(1.0, color="k", lw=0.7, ls="--")
ax.set_ylabel("Cost vs no-cache (1.0 = no caching)")
ax.set_title("a  Cost under two cache models", loc="left")
ax.legend(loc="upper left")

# panel d (composition control): MC with and without its warmer in the
# no-churn sweep cells — the warmer's all-request "win" is self-hit
sweep_rows = json.load(open(_latest("sweep")))["rows"]

ax = axes[1]
# hit-ratio trajectories for TURN requests only (compaction/warmer shapes
# legitimately differ, so their per-request ratios are not comparable)
for name in ORDER:
    rep = _replay(name)
    ys = [r["hit_ratio"] * 100 for r in rep["anthropic"]["rows"]
          if r.get("kind") in (None, "turn")]
    ax.plot(range(len(ys)), ys, "-o", ms=2.0, lw=0.9, color=C[name], label=name)
ax.set_xlabel("Request (step)")
ax.set_ylabel("Anthropic-cache hit ratio (%)")
ax.set_title("b  Per-request hit trajectory", loc="left")
ax.legend(loc="lower right")
ax.set_ylim(0, 100)

ax = axes[2]
steps_opts = sens["steps_opts"]
ttl = 300
# turn-only hit ratios (exclude warmer/compact rows) so warmer self-hits
# do not distort the TTL-sensitivity comparison
for name in ORDER:
    ys = []
    for s in steps_opts:
        rep = _replay(name, seconds_per_step=float(s), anthropic_ttl=ttl)
        rows = [r for r in rep["anthropic"]["rows"] if r.get("kind") in (None, "turn")]
        ys.append(100 * sum(r["read"] for r in rows) / sum(r["tokens"] for r in rows))
    ax.plot(steps_opts, ys, "-o", ms=2.2, lw=0.9, color=C[name], label=name)
ax.set_xscale("log")
ax.set_xticks(steps_opts)
ax.set_xticklabels([str(int(s)) for s in steps_opts])
ax.set_xlabel("Simulated seconds per request")
ax.set_ylabel("Anthropic-cache hit ratio (%)")
ax.set_title("c  TTL sensitivity (TTL = 300 s)", loc="left")
ax.legend(loc="lower left")

fig.tight_layout()
fig.savefig(os.path.join(FIGDIR, "fig2_economics_sensitivity.pdf"))
fig.savefig(os.path.join(FIGDIR, "fig2_economics_sensitivity.png"))
plt.close(fig)
print("figures written to", FIGDIR)
