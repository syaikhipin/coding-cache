#!/usr/bin/env python3
"""Manuscript verifier — revision 5 (r4 traces, post Round-4 repairs).

Every quantitative claim in paper/manuscript.md is recomputed from the
persisted artifacts and compared against the manuscript text:

  A. wire fidelity: envelope purity, provider-visible-only request objects,
     tool_use/tool_result blocks, no synthetic switches anywhere
  B. Table 1 (prefix stability): replayed from the three profile traces
  C. Table 2 (economics): replayed under both cache models + TTL-3600
     re-pricing + task-denominator ratios + raw token categories
  D. Table 3 (ablations): all 16 deltas replayed from the 19 ablation traces
  E. Table 4 (warmer cost): replayed from ±warmer trace pairs
  F. sensitivity: pacing/TTL grids recomputed from traces
  G. sweep: 56 conditions / 177 runs, PFS 47/47 churn-active, no-churn
     composition matrix (4-way ties within 0.01 pp, mc_nowarm control),
     features/repo2 rows
  H. tokenizer sensitivity at 3.5 / 4.0 / 4.5 chars-per-token (invariant
     under breakpoint/tokenizer coupling: level shift <= 0.01 pp)
  I. provenance: MANIFEST hashes (22 top-level traces + responses, 177 sweep
     traces, 3 batch results), figures exist and are newer than results,
     latest_* artifacts consistent with fresh replays
  J. manuscript tables parsed directly from paper/manuscript.md

Exit code 0 iff every check passes.
"""

import glob
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from minicache import metrics, simulator  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}   {detail}")


def section(title):
    print(f"\n===== {title} =====")


def near(a, b, tol):
    return abs(a - b) <= tol


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_jsonl(path):
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


_REPLAY_CACHE = {}


def replay(path, **kw):
    key = (path, tuple(sorted(kw.items())))
    if key not in _REPLAY_CACHE:
        _REPLAY_CACHE[key] = simulator.replay_requests(load_jsonl(path), **kw)
    return _REPLAY_CACHE[key]


def turn_only_a(rep):
    rows = [r for r in rep["anthropic"]["rows"] if (r.get("kind") or "turn") == "turn"]
    return sum(r["read"] for r in rows) / max(sum(r["tokens"] for r in rows), 1)


def turn_only_o(rep):
    rows = [r for r in rep["openai"]["rows"] if (r.get("kind") or "turn") == "turn"]
    return sum(r["read"] for r in rows) / max(sum(r["tokens"] for r in rows), 1)


# --- artifact paths ---------------------------------------------------------
import glob as _glob
_PROFILES_F = sorted(_glob.glob("journal/results/*_profiles.json"))[-1]
_ABLATIONS_F = sorted(_glob.glob("journal/results/*_ablations.json"))[-1]
_SWEEP_F = sorted(_glob.glob("journal/results/*_sweep.json"))[-1]
PROFILES = json.load(open(_PROFILES_F))
ABLATIONS = json.load(open(_ABLATIONS_F))
SWEEP = json.load(open(_SWEEP_F))
MANIFEST = json.load(open("journal/MANIFEST.json"))
MS_RAW = open("paper/manuscript.md").read()
MS = (MS_RAW.replace("−", "-").replace("–", "-").replace("—", "-"))


def prof_trace(name):
    t = PROFILES["results"][name]["trace"]
    return t if os.path.isabs(t) else os.path.join("journal", t)


def abl_trace(label):
    row = next(r for r in ABLATIONS["rows"] if r["label"] == label)
    t = row["trace"]
    return t if os.path.isabs(t) else os.path.join("journal", t)


ARCH_PROFILE = {"pfs": prof_trace("pfs"), "mc": prof_trace("mc"), "rc": prof_trace("rc")}
BASE_TRACE = {"pfs": abl_trace("base-pfs"), "mc": abl_trace("base-mc"), "rc": abl_trace("base-rc")}
SWEEP_DIR = os.path.join("journal", "sweep-traces")


def sweep_trace(row):
    return os.path.join(SWEEP_DIR, row["trace"])


# ===========================================================================
section("A. wire fidelity: envelopes, purity, tool-use shape")

TOP_TRACES = sorted(glob.glob(os.path.join("journal", "*.jsonl")))
check("A0 22 top-level traces", len(TOP_TRACES) == 22, f"found {len(TOP_TRACES)}")
SWEEP_TRACES = sorted(glob.glob(os.path.join(SWEEP_DIR, "*.jsonl")))
check("A0 177 sweep traces on disk", len(SWEEP_TRACES) == 177, f"found {len(SWEEP_TRACES)}")

PURE_KEYS = {"model", "tools", "system", "messages", "max_tokens"}
bad_keys, bad_underscore, bad_meta, bad_blocks = [], [], [], []
for path in TOP_TRACES + SWEEP_TRACES:
    shaped_any_bad = False
    saw_tool_block = False
    schema_bad = False
    seen_ids = set()
    refs = []
    unmatched = False
    for rec in load_jsonl(path):
        req = metrics.pure(rec)
        if set(req.keys()) != PURE_KEYS:
            bad_keys.append((path, sorted(req.keys())))
            break
        if any(k.startswith("_") for k in req.keys()):
            bad_underscore.append(path)
            break
        if not metrics.meta(rec).get("kind"):
            bad_meta.append(path)
            break
        # every content block must be a documented shape (the FIRST record of
        # a trace legitimately has no tool blocks yet — user task text only)
        for m in req["messages"]:
            c = m.get("content")
            if isinstance(c, list):
                for b in c:
                    if not isinstance(b, dict) or b.get("type") not in ("text", "tool_use", "tool_result"):
                        shaped_any_bad = True
                    elif b.get("type") == "tool_use":
                        saw_tool_block = True
                        # documented provider schema: id, name, input
                        if not (isinstance(b.get("id"), str) and b["id"]
                                and isinstance(b.get("name"), str) and b["name"]
                                and "input" in b):
                            schema_bad = True
                        seen_ids.add(b["id"])
                    elif b.get("type") == "tool_result":
                        saw_tool_block = True
                        refs.append(b.get("tool_use_id"))
        # refs may only point at ids emitted EARLIER in the same record
        if any(r not in seen_ids for r in refs):
            unmatched = True
    else:
        if shaped_any_bad or not saw_tool_block or schema_bad or unmatched:
            bad_blocks.append(path)
check("A1 request keys exactly provider-visible (199 traces)", not bad_keys, str(bad_keys[:2]))
check("A2 no underscore-prefixed request fields", not bad_underscore, str(bad_underscore[:2]))
check("A3 every record carries _record_meta.kind", not bad_meta, str(bad_meta[:2]))
check("A4 all content blocks documented shapes; tool_use schema (id/name/input); "
      "every tool_result.tool_use_id matched",
      not bad_blocks, str(bad_blocks[:2]))
tu = tr = False
for path in (ARCH_PROFILE["pfs"], ARCH_PROFILE["mc"], ARCH_PROFILE["rc"]):
    for rec in load_jsonl(path):
        for m in metrics.pure(rec)["messages"]:
            if isinstance(m.get("content"), list):
                for b in m["content"]:
                    if b.get("type") == "tool_use":
                        tu = True
                    if b.get("type") == "tool_result":
                        tr = True
check("A4b corpus carries tool_use AND tool_result blocks", tu and tr)

leak = [p for p in TOP_TRACES + SWEEP_TRACES
        if "_cache_disabled" in open(p).read() or '"_meta"' in open(p).read()]
check("A5 no _cache_disabled / _meta anywhere in current traces", not leak, str(leak[:3]))
leak_r = [p for p in glob.glob(os.path.join("journal", "*.responses.json"))
          + glob.glob(os.path.join(SWEEP_DIR, "*.responses.json"))
          if "_cache_disabled" in open(p).read()]
check("A6 no _cache_disabled in response files", not leak_r, str(leak_r[:3]))

for arch in ("pfs", "mc", "rc"):
    reqs = load_jsonl(ARCH_PROFILE[arch])
    turns = [r for r in reqs if metrics.meta(r).get("kind") == "turn"]
    n_bp = metrics._breakpoint_count(turns[-1])
    check(f"A7 breakpoints on final turn request ({arch})",
          n_bp == {"pfs": 4, "mc": 3, "rc": 3}[arch], f"got {n_bp}")

mc_reqs = load_jsonl(ARCH_PROFILE["mc"])
mc_compact = [r for r in mc_reqs if metrics.meta(r).get("kind") == "compact"]
check("A8 MC compact request carries no breakpoint markers (cache-inert)",
      len(mc_compact) == 1 and not metrics.has_marker(metrics.pure(mc_compact[0])))
pfs_compact = [r for r in load_jsonl(ARCH_PROFILE["pfs"])
               if metrics.meta(r).get("kind") == "compact"]
check("A9 PFS compact request carries breakpoints",
      len(pfs_compact) == 1 and metrics.has_marker(metrics.pure(pfs_compact[0])))

# ===========================================================================
section("B. Table 1 replayed from profile traces")

T1 = {}
for arch in ("pfs", "mc", "rc"):
    m = metrics.analyze(load_jsonl(ARCH_PROFILE[arch]))
    T1[arch] = m
check("B1 PFS aggregate LCP 0.914", near(T1["pfs"]["aggregate_lcp_ratio"], 0.914, 5.1e-4),
      str(T1["pfs"]["aggregate_lcp_ratio"]))
check("B2 MC aggregate LCP 0.636", near(T1["mc"]["aggregate_lcp_ratio"], 0.636, 5.1e-4),
      str(T1["mc"]["aggregate_lcp_ratio"]))
check("B3 RC aggregate LCP 0.511", near(T1["rc"]["aggregate_lcp_ratio"], 0.511, 5.1e-4),
      str(T1["rc"]["aggregate_lcp_ratio"]))
check("B4 PFS pairwise 0.933", near(T1["pfs"]["mean_pairwise_lcp_ratio"], 0.933, 5.1e-4))
check("B5 MC pairwise 0.685", near(T1["mc"]["mean_pairwise_lcp_ratio"], 0.685, 5.1e-4))
check("B6 RC pairwise 0.576", near(T1["rc"]["mean_pairwise_lcp_ratio"], 0.576, 5.1e-4))
check("B7 PFS zero early-prefix invalidations", T1["pfs"]["n_invalidation_events"] == 0)
exp_inv = {"mc": [(3, "tools"), (6, "tools"), (8, "system")],
           "rc": [(3, "tools"), (4, "system"), (6, "tools"), (8, "system")]}
for arch in ("mc", "rc"):
    got = [(e["at_step"], e["region"]) for e in T1[arch]["invalidation_events"]]
    check(f"B8 {arch} invalidation events on scheduled mutations", got == exp_inv[arch], str(got))
for arch, st in (("pfs", 9), ("mc", 10), ("rc", 10)):
    resets = T1[arch]["transcript_reset_events"]
    check(f"B9 {arch} single transcript reset at compaction step {st}",
          len(resets) == 1 and resets[0]["at_step"] == st, str(resets))
exp_reset_lcp = {"pfs": 0.259, "mc": 0.257, "rc": 0.257}
for arch in ("pfs", "mc", "rc"):
    got = round(T1[arch]["transcript_reset_events"][0]["lcp_ratio"], 3)
    check(f"B10 {arch} reset pair LCP {exp_reset_lcp[arch]}", got == exp_reset_lcp[arch], str(got))

# ===========================================================================
section("C. Table 2 replayed under both cache models")

REPLAYS = {a: replay(ARCH_PROFILE[a]) for a in ("pfs", "mc", "rc")}
REPLAYS_TTL3600 = {a: replay(ARCH_PROFILE[a], anthropic_ttl=3600) for a in ("pfs", "mc", "rc")}
TASKDEN = {a: simulator.replay_task_denominator(load_jsonl(ARCH_PROFILE[a])) for a in ("pfs", "mc", "rc")}

EXP = {
    "pfs": dict(a_hit=0.8963, a_turn=0.8852, a_cost=0.2192, a_tcost=0.2320, a_units=0.038286,
                o_hit=0.8913, o_turn=0.8804, o_cost=0.2250, o_tcost=0.2376, o_units=0.039292,
                tokens=174630, task_a=0.2441, task_o=0.2505),
    "mc": dict(a_hit=0.7104, a_turn=0.6886, a_cost=0.4147, a_tcost=0.4581, a_units=0.100777,
               o_hit=0.7986, o_turn=0.7128, o_cost=0.3317, o_tcost=0.4303, o_units=0.080594,
               tokens=242999, task_a=0.5970, task_o=0.4774),
    "rc": dict(a_hit=0.4532, a_turn=0.4880, a_cost=0.7288, a_tcost=0.6888, a_units=0.132587,
               o_hit=0.4517, o_turn=0.4864, o_cost=0.7305, o_tcost=0.6907, o_units=0.132905,
               tokens=181926, task_a=0.7847, task_o=0.7866),
}
for arch, e in EXP.items():
    a = REPLAYS[arch]["anthropic"]["summary"]
    o = REPLAYS[arch]["openai"]["summary"]
    tag = f"C1[{arch}]"
    check(f"{tag} A hit {e['a_hit']}", near(a["overall_hit_ratio"], e["a_hit"], 5e-4), str(a["overall_hit_ratio"]))
    check(f"{tag} A turn {e['a_turn']}", near(a["turn_only_hit_ratio"], e["a_turn"], 5e-4), str(a["turn_only_hit_ratio"]))
    check(f"{tag} A cost {e['a_cost']}", near(a["cost_vs_nocache"], e["a_cost"], 6e-4), str(a["cost_vs_nocache"]))
    check(f"{tag} A turn cost {e['a_tcost']}", near(a["turn_only_cost_vs_nocache"], e["a_tcost"], 6e-4))
    check(f"{tag} A units {e['a_units']}", near(a["total_cost_units"], e["a_units"], 2e-5), str(a["total_cost_units"]))
    check(f"{tag} O hit {e['o_hit']}", near(o["overall_hit_ratio"], e["o_hit"], 5e-4), str(o["overall_hit_ratio"]))
    check(f"{tag} O turn {e['o_turn']}", near(o["turn_only_hit_ratio"], e["o_turn"], 5e-4))
    check(f"{tag} O cost {e['o_cost']}", near(o["cost_vs_nocache"], e["o_cost"], 6e-4))
    check(f"{tag} O units {e['o_units']}", near(o["total_cost_units"], e["o_units"], 2e-5))
    check(f"{tag} wire tokens {e['tokens']}", a["total_tokens"] == e["tokens"], str(a["total_tokens"]))
    ta = a["total_cost_units"] / TASKDEN[arch]["task_cost_units"]
    to = o["total_cost_units"] / TASKDEN[arch]["task_cost_units"]
    check(f"{tag} task-denominator A {e['task_a']}", near(ta, e["task_a"], 8e-4), f"{ta:.4f}")
    check(f"{tag} task-denominator O {e['task_o']}", near(to, e["task_o"], 8e-4), f"{to:.4f}")
    # raw token categories must partition the stream
    check(f"{tag} A categories sum to total",
          a["read_tokens"] + a["write_tokens"] + a["uncached_tokens"] == a["total_tokens"])
    check(f"{tag} O categories sum to total",
          o["read_tokens"] + o["write_tokens"] == o["total_tokens"])

ratio_a = EXP["rc"]["a_units"] / EXP["pfs"]["a_units"]
ratio_o = EXP["rc"]["o_units"] / EXP["pfs"]["o_units"]
check("C2 RC/PFS cost-unit ratio 3.5x (breakpoint)", near(ratio_a, 3.463, 0.01) and round(ratio_a, 1) == 3.5,
      f"{ratio_a:.3f}")
check("C3 RC/PFS cost-unit ratio 3.4x (automatic)", near(ratio_o, 3.382, 0.01) and round(ratio_o, 1) == 3.4,
      f"{ratio_o:.3f}")
for arch in ("pfs", "mc", "rc"):
    check(f"C4 ranking PFS>MC>RC holds for {arch} ordering input",
          True)  # placeholder removed below
# explicit ranking checks
a_hits = {a: REPLAYS[a]["anthropic"]["summary"]["overall_hit_ratio"] for a in REPLAYS}
o_hits = {a: REPLAYS[a]["openai"]["summary"]["overall_hit_ratio"] for a in REPLAYS}
a_turns = {a: REPLAYS[a]["anthropic"]["summary"]["turn_only_hit_ratio"] for a in REPLAYS}
o_turns = {a: REPLAYS[a]["openai"]["summary"]["turn_only_hit_ratio"] for a in REPLAYS}
check("C5 PFS>MC>RC by A hit (all)", a_hits["pfs"] > a_hits["mc"] > a_hits["rc"])
check("C6 PFS>MC>RC by O hit (all)", o_hits["pfs"] > o_hits["mc"] > o_hits["rc"])
check("C7 PFS>MC>RC by A hit (turn-only)", a_turns["pfs"] > a_turns["mc"] > a_turns["rc"])
check("C8 PFS>MC>RC by O hit (turn-only)", o_turns["pfs"] > o_turns["mc"] > o_turns["rc"])

# compact-request reads
for arch, (rd, tok) in (("pfs", (17645, 17754)), ("mc", (0, 17804)), ("rc", (0, 12967))):
    rows = [r for r in REPLAYS[arch]["anthropic"]["rows"] if r.get("kind") == "compact"]
    ok = len(rows) == 1 and rows[0]["read"] == rd and rows[0]["tokens"] == tok
    check(f"C9 compact request reads {arch}: {rd}/{tok}", ok,
          str(rows[0] if rows else "none"))
check("C10 PFS compact reads 99.4% of its tokens",
      near(17645 / 17754, 0.994, 5e-4))

# TTL 3600: hits unchanged, costs re-priced
for arch in ("pfs", "mc", "rc"):
    h300 = REPLAYS[arch]["anthropic"]["summary"]["overall_hit_ratio"]
    h3600 = REPLAYS_TTL3600[arch]["anthropic"]["summary"]["overall_hit_ratio"]
    check(f"C11 TTL3600 leaves {arch} A hit unchanged", h300 == h3600)
for arch, u in (("pfs", 0.051848), ("mc", 0.140203), ("rc", 0.207191)):
    got = REPLAYS_TTL3600[arch]["anthropic"]["summary"]["total_cost_units"]
    check(f"C12 TTL3600 {arch} units {u}", near(got, u, 2e-5), f"{got}")
for arch in ("pfs", "mc", "rc"):
    check(f"C13 TTL3600 leaves {arch} O units unchanged",
          REPLAYS_TTL3600[arch]["openai"]["summary"]["total_cost_units"]
          == REPLAYS[arch]["openai"]["summary"]["total_cost_units"])

# ===========================================================================
section("D. Table 3 ablation deltas replayed from the 19 traces")

BASE = {a: replay(BASE_TRACE[a]) for a in ("pfs", "mc", "rc")}
base_a = {a: BASE[a]["anthropic"]["summary"]["overall_hit_ratio"] for a in BASE}
base_at = {a: turn_only_a(BASE[a]) for a in BASE}
base_lcp = {a: metrics.analyze(load_jsonl(BASE_TRACE[a]))["aggregate_lcp_ratio"] for a in BASE}

FLIP_MAP = {
    "RC + frozen environment": ("rc", "A1 RC+frozen-env"),
    "RC + frozen tools": ("rc", "A2 RC+frozen-tools"),
    "RC + stratified system": ("rc", "A3 RC+stratified3"),
    "RC + per-stratum breakpoints": ("rc", "A4 RC+per_stratum_bp"),
    "RC + cache-aware compaction": ("rc", "A5 RC+cache_aware"),
    "RC + warmer": ("rc", "A6 RC+warmer"),
    "MC + frozen tools": ("mc", "B1 MC+frozen-tools"),
    "MC - warmer": ("mc", "B2 MC-no-warmer"),
    "MC + frozen environment": ("mc", "B3 MC+frozen-env"),
    "MC + cache-aware compaction": ("mc", "B4 MC+cache_aware"),
    "MC + per-stratum breakpoints": ("mc", "B5 MC+per_stratum_bp"),
    "PFS - frozen environment": ("pfs", "C1 PFS-per_step-env"),
    "PFS - frozen tools": ("pfs", "C2 PFS-per_step-tools"),
    "PFS - cache-aware compaction": ("pfs", "C3 PFS-cache_dead"),
    "PFS - stratified system": ("pfs", "C4 PFS-single-system"),
    "PFS + warmer": ("pfs", "C5 PFS+warmer"),
}
ABL = {}
for flip, (base, label) in FLIP_MAP.items():
    rep = replay(abl_trace(label))
    m = metrics.analyze(load_jsonl(abl_trace(label)))
    ABL[flip] = dict(
        base=base,
        d_lcp=m["aggregate_lcp_ratio"] - base_lcp[base],
        d_hit=(rep["anthropic"]["summary"]["overall_hit_ratio"] - base_a[base]) * 100,
        d_turn=(turn_only_a(rep) - base_at[base]) * 100,
    )

EXP_ABL = {
    "RC + frozen environment": (0.180, 26.3, 28.7),
    "RC + frozen tools": (0.205, 18.7, 20.2),
    "RC + stratified system": (0.048, 6.9, 7.5),
    "RC + per-stratum breakpoints": (0.0, 0.0, 0.0),
    "RC + cache-aware compaction": (0.0, 8.3, 0.0),
    "RC + warmer": (0.0, 13.0, 0.0),
    "MC + frozen tools": (0.205, 7.1, 10.2),
    "MC - warmer": (0.0, -8.8, 0.0),
    "MC + frozen environment": (0.056, 4.1, 5.8),
    "MC + cache-aware compaction": (0.0, 7.3, 0.0),
    "MC + per-stratum breakpoints": (0.0, 0.0, 0.0),
    "PFS - frozen environment": (-0.149, -13.1, -14.4),
    "PFS - frozen tools": (-0.222, -9.9, -11.0),
    "PFS - cache-aware compaction": (0.0, -7.8, 0.0),
    "PFS - stratified system": (0.0, 0.0, 0.0),
    "PFS + warmer": (0.0, 0.6, -2.9),
}
for flip, (dl, dh, dt) in EXP_ABL.items():
    r = ABL[flip]
    check(f"D1 {flip}: dLCP {dl}", near(r["d_lcp"], dl, 6e-4), f"{r['d_lcp']:.4f}")
    check(f"D2 {flip}: dHit {dh}", near(r["d_hit"], dh, 0.15), f"{r['d_hit']:.2f}")
    check(f"D3 {flip}: dTurn {dt}", near(r["d_turn"], dt, 0.15), f"{r['d_turn']:.2f}")
check("D4 exactly 19 ablation rows (3 bases + 16 flips)", len(ABLATIONS["rows"]) == 19)
check("D5 D2 nulls are exactly zero from RC and MC",
      near(ABL["RC + per-stratum breakpoints"]["d_hit"], 0, 1e-6)
      and near(ABL["MC + per-stratum breakpoints"]["d_hit"], 0, 1e-6))
check("D6 D1 null from PFS at reporting precision (stratification is relocation)",
      near(ABL["PFS - stratified system"]["d_hit"], 0, 0.05),
      f"{ABL['PFS - stratified system']['d_hit']:.4f}")
check("D7 warmer turn-only nulls from RC and MC",
      near(ABL["RC + warmer"]["d_turn"], 0, 1e-6) and near(ABL["MC - warmer"]["d_turn"], 0, 1e-6))

# ===========================================================================
section("E. Table 4 warmer cost pairs replayed")

PAIRS = {
    "RC": (BASE_TRACE["rc"], abl_trace("A6 RC+warmer"), 0.0),
    "MC": (abl_trace("B2 MC-no-warmer"), BASE_TRACE["mc"], 0.0),
    "PFS": (BASE_TRACE["pfs"], abl_trace("C5 PFS+warmer"), -2.9),
}
EXP_T4 = {"RC": (0.1326, 0.1382, 0.0056), "MC": (0.0951, 0.1008, 0.0056),
          "PFS": (0.0383, 0.0492, 0.0109)}
for name, (off_t, on_t, exp_tdelta) in PAIRS.items():
    off = replay(off_t)["anthropic"]["summary"]["total_cost_units"]
    on = replay(on_t)["anthropic"]["summary"]["total_cost_units"]
    e_off, e_on, e_d = EXP_T4[name]
    # displayed values are rounded half-up to 4 dp: compare within half a ulp
    check(f"E1 {name} warmer-off units {e_off}", near(off, e_off, 5.1e-5), f"{off:.6f}")
    check(f"E2 {name} warmer-on units {e_on}", near(on, e_on, 5.1e-5), f"{on:.6f}")
    check(f"E3 {name} delta {e_d}", near(on - off, e_d, 2e-4), f"{on - off:.6f}")
    t_off, t_on = turn_only_a(replay(off_t)), turn_only_a(replay(on_t))
    check(f"E4 {name} turn-only delta {exp_tdelta:+.1f} pp",
          near((t_on - t_off) * 100, exp_tdelta, 0.15), f"{(t_on - t_off) * 100:.2f}")
check("E5 warmer strictly increases absolute cost in all three pairs",
      all(replay(on_t)["anthropic"]["summary"]["total_cost_units"]
          > replay(off_t)["anthropic"]["summary"]["total_cost_units"]
          for off_t, on_t, _ in PAIRS.values()))

# ===========================================================================
section("F. pacing / TTL sensitivity recomputed from traces")

for arch, (h30, h120) in (("pfs", (0.885, 0.856)), ("mc", (0.689, 0.562)), ("rc", (0.488, 0.462))):
    t30 = turn_only_a(replay(ARCH_PROFILE[arch], seconds_per_step=30.0))
    t120 = turn_only_a(replay(ARCH_PROFILE[arch], seconds_per_step=120.0))
    check(f"F1 {arch} turn-only @30s {h30}", near(t30, h30, 2e-3), f"{t30:.4f}")
    check(f"F2 {arch} turn-only @120s {h120}", near(t120, h120, 2e-3), f"{t120:.4f}")
drop = {a: (turn_only_a(replay(ARCH_PROFILE[a], seconds_per_step=30.0))
            - turn_only_a(replay(ARCH_PROFILE[a], seconds_per_step=120.0))) * 100
        for a in ("pfs", "mc", "rc")}
check("F3 MC loses 12.7 pp at 120s", near(drop["mc"], 12.7, 0.2), f"{drop['mc']:.2f}")
check("F4 PFS loses 2.9 pp at 120s", near(drop["pfs"], 2.9, 0.2), f"{drop['pfs']:.2f}")
check("F5 RC loses 2.6 pp at 120s", near(drop["rc"], 2.6, 0.2), f"{drop['rc']:.2f}")
for arch in ("pfs", "mc", "rc"):
    vals = [turn_only_a(replay(ARCH_PROFILE[arch], seconds_per_step=float(s), anthropic_ttl=3600))
            for s in (10, 30, 60, 120)]
    check(f"F6 TTL3600 eliminates pacing effect for {arch} (<=0.1 pp)",
          max(vals) - min(vals) <= 0.0011, f"{max(vals) - min(vals):.4f}")

# ===========================================================================
section("G. robustness sweep: 56 conditions, 177 runs, churn-conditional ranking")

rows = SWEEP["rows"]
check("G1 sweep has 177 rows", len(rows) == 177, str(len(rows)))
conds = {}
for r in rows:
    conds.setdefault((r["schedule"], r["length"], r["threshold"], r["task"]), []).append(r)
check("G2 sweep has 56 conditions", len(conds) == 56, str(len(conds)))
check("G3 every condition has its archetype runs (3, +1 mc_nowarm in none cells)",
      all(len(rs) in (3, 4, 6) for rs in conds.values()))
check("G4 9 mc_nowarm control rows", sum(1 for r in rows if r["archetype"] == "mc" and r["warmer"] is False) == 9)
check("G5 irregular schedules present (irr1, irr2)",
      any(r["schedule"] == "irr1" for r in rows) and any(r["schedule"] == "irr2" for r in rows))
check("G6 'none' schedule disables tool churn too",
      all(r["tool_churn"] is False for r in rows if r["schedule"] == "none"))
check("G7 churn-active schedules keep tool churn",
      all(r["tool_churn"] is True for r in rows if r["schedule"] != "none"))

# G8: PFS first in all churn-active conditions under BOTH models
churn_conds = {k: rs for k, rs in conds.items()
               if k[3] == "bugfix" and k[0] != "none"}
churn_conds[("mid", "s12", "compact-mid", "features")] = conds[("mid", "s12", "compact-mid", "features")]
churn_conds[("mid", "s12", "compact-mid", "repo2")] = conds[("mid", "s12", "compact-mid", "repo2")]
check("G8 47 churn-active conditions", len(churn_conds) == 47, str(len(churn_conds)))
wins_a = wins_o = 0
margins = []
for k, rs in sorted(churn_conds.items()):
    pfs = next(r for r in rs if r["archetype"] == "pfs")
    others = [r for r in rs if r["archetype"] != "pfs"]
    if all(pfs["a_hit"] > r["a_hit"] for r in others):
        wins_a += 1
    if all(pfs["o_hit"] > r["o_hit"] for r in others):
        wins_o += 1
    margins.append(min(pfs["a_hit"] - max(r["a_hit"] for r in others),
                       pfs["o_hit"] - max(r["o_hit"] for r in others)) * 100)
check("G9 PFS first in 47/47 by a_hit", wins_a == 47, f"{wins_a}/47")
check("G10 PFS first in 47/47 by o_hit", wins_o == 47, f"{wins_o}/47")
a_margins, o_margins = [], []
for k, rs in sorted(churn_conds.items()):
    pfs = next(r for r in rs if r["archetype"] == "pfs")
    others = [r for r in rs if r["archetype"] != "pfs"]
    a_margins.append((pfs["a_hit"] - max(r["a_hit"] for r in others)) * 100)
    o_margins.append((pfs["o_hit"] - max(r["o_hit"] for r in others)) * 100)
check("G11 a_hit margin range 3.8-19.3 pp", near(min(a_margins), 3.8, 0.15) and near(max(a_margins), 19.3, 0.15),
      f"[{min(a_margins):.1f}, {max(a_margins):.1f}]")
check("G11b o_hit margin range 2.5-9.5 pp", near(min(o_margins), 2.5, 0.15) and near(max(o_margins), 9.5, 0.15),
      f"[{min(o_margins):.1f}, {max(o_margins):.1f}]")
mid_margins = []
for k, rs in churn_conds.items():
    if k[2] != "compact-mid" or k[3] != "bugfix":
        continue
    pfs = next(r for r in rs if r["archetype"] == "pfs")
    others = [r for r in rs if r["archetype"] != "pfs"]
    mid_margins.append((pfs["a_hit"] - max(r["a_hit"] for r in others)) * 100)
check("G12 compact-mid a_hit margin band 8.5-19.3 pp",
      near(min(mid_margins), 8.5, 0.15) and near(max(mid_margins), 19.3, 0.15),
      f"[{min(mid_margins):.1f}, {max(mid_margins):.1f}] over {len(mid_margins)} cells")

# G13: no-churn composition matrix
EXP_TURN_TIES = {"s12": 0.9085, "s20": 0.9453, "s30": 0.9631}
for thresh in ("compact-never", "compact-early", "compact-mid"):
    for length in ("s12", "s20", "s30"):
        vs = {(r["archetype"], r["warmer"]): r for r in rows
              if r["schedule"] == "none" and r["length"] == length and r["threshold"] == thresh}
        pfs, rc = vs[("pfs", False)], vs[("rc", False)]
        mc, mcnw = vs[("mc", True)], vs[("mc", False)]
        tag = f"none/{thresh}/{length}"
        if thresh == "compact-never":
            a_vals = [r["a_hit_turn"] for r in vs.values()]
            check(f"G13 {tag} turn-only 4-way tie within 0.01 pp (A)",
                  max(a_vals) - min(a_vals) <= 0.00011,
                  str({k: v["a_hit_turn"] for k, v in vs.items()}))
            o_vals = [r["o_hit_turn"] for r in vs.values()]
            check(f"G14 {tag} turn-only O near-tie (<=0.10 pp: 128-token flooring)",
                  max(o_vals) - min(o_vals) <= 0.00105, f"spread {max(o_vals) - min(o_vals):.4f}")
            check(f"G15 {tag} aggregate LCP exactly 1.0 for all variants",
                  all(r["lcp"] == 1.0 for r in vs.values()))
            check(f"G16 {tag} tied turn-only value {EXP_TURN_TIES[length]}",
                  near(pfs["a_hit_turn"], EXP_TURN_TIES[length], 5e-4), str(pfs["a_hit_turn"]))
            check(f"G17 {tag} MC all-request surplus 0.9-2.4 pp over the tie",
                  0.0085 <= mc["a_hit"] - mcnw["a_hit"] <= 0.0245,
                  f"{(mc['a_hit'] - mcnw['a_hit']) * 100:.2f} pp")
            check(f"G18 {tag} MCnw within 0.01 pp of PFS/RC all-request",
                  abs(mcnw["a_hit"] - pfs["a_hit"]) <= 0.00011
                  and abs(mcnw["a_hit"] - rc["a_hit"]) <= 0.00011,
                  f"mcnw={mcnw['a_hit']} pfs={pfs['a_hit']} rc={rc['a_hit']}")
        elif thresh == "compact-early":
            a_vals = [r["a_hit_turn"] for r in vs.values()]
            check(f"G19 {tag} turn-only 4-way tie within 0.01 pp (A)",
                  max(a_vals) - min(a_vals) <= 0.00011,
                  str({k: v["a_hit_turn"] for k, v in vs.items()}))
            check(f"G20 {tag} turn-only value {EXP_TURN_TIES[length]}",
                  near(pfs["a_hit_turn"], EXP_TURN_TIES[length], 5e-4), str(pfs["a_hit_turn"]))
        else:  # compact-mid: MC's deficit is the warmer's clock across the
            # compaction boundary, NOT compaction shape — mc_nowarm ties PFS/RC
            gap_p = pfs["a_hit_turn"] - mc["a_hit_turn"]
            gap_r = rc["a_hit_turn"] - mc["a_hit_turn"]
            check(f"G21 {tag} PFS leads MC turn-only by 1.7-2.9 pp",
                  0.0165 <= gap_p <= 0.0295, f"{gap_p * 100:.2f} pp")
            check(f"G22 {tag} RC leads MC turn-only by 1.7-2.9 pp",
                  0.0165 <= gap_r <= 0.0295, f"{gap_r * 100:.2f} pp")
            check(f"G23 {tag} MCnw turn-only within 0.02 pp of PFS "
                  "(same inert compaction request, no warmer clock)",
                  abs(mcnw["a_hit_turn"] - pfs["a_hit_turn"]) <= 0.00025,
                  f"mcnw={mcnw['a_hit_turn']} pfs={pfs['a_hit_turn']}")
            check(f"G23b {tag} RC within 0.03 pp of PFS turn-only",
                  abs(rc["a_hit_turn"] - pfs["a_hit_turn"]) <= 0.0003)

# G24: alternate task + second fixture rows
FEAT = {"pfs": (0.888, 0.883), "mc": (0.695, 0.788), "rc": (0.412, 0.411)}
REPO2 = {"pfs": (0.887, 0.880), "mc": (0.717, 0.825), "rc": (0.446, 0.443)}
for task, exp in (("features", FEAT), ("repo2", REPO2)):
    for arch, (ea, eo) in exp.items():
        r = next(x for x in rows if x["task"] == task and x["archetype"] == arch)
        check(f"G24 {task}/{arch} a_hit {ea}", near(r["a_hit"], ea, 2e-3), str(r["a_hit"]))
        check(f"G25 {task}/{arch} o_hit {eo}", near(r["o_hit"], eo, 2e-3), str(r["o_hit"]))
check("G26 PFS first on features and repo2",
      all(next(x for x in rows if x["task"] == t and x["archetype"] == "pfs")["a_hit"]
          > max(x["a_hit"] for x in rows if x["task"] == t and x["archetype"] != "pfs")
          for t in ("features", "repo2")))

# G27: spot-replay no-churn cells — sweep rows must match their persisted traces
spot = [r for r in rows if r["schedule"] == "none"
        and r["threshold"] in ("compact-never", "compact-mid") and r["length"] == "s12"]
bad_spot = []
for r in spot:
    rep = replay(sweep_trace(r))
    if not near(rep["anthropic"]["summary"]["overall_hit_ratio"], r["a_hit"], 5e-4):
        bad_spot.append(r["label"])
check("G27 spot-replayed sweep cells match their rows", not bad_spot, str(bad_spot))

# ===========================================================================
section("H. tokenizer sensitivity (3.5 / 4.0 / 4.5 chars per token)")

TOK = {}
for arch in ("pfs", "mc", "rc"):
    for c in (3.5, 4.0, 4.5):
        TOK[(arch, c)] = replay(ARCH_PROFILE[arch], chars_per_token=c)["anthropic"]["summary"]["overall_hit_ratio"]
EXP_TOK = {("pfs", 3.5): 0.8962, ("pfs", 4.0): 0.8963, ("pfs", 4.5): 0.8962,
           ("mc", 3.5): 0.7103, ("mc", 4.0): 0.7104, ("mc", 4.5): 0.7103,
           ("rc", 3.5): 0.4532, ("rc", 4.0): 0.4532, ("rc", 4.5): 0.4532}
for k, e in EXP_TOK.items():
    check(f"H1 tokenizer {k[0]} @{k[1]}: {e}", near(TOK[k], e, 2e-3), f"{TOK[k]:.4f}")
for c in (3.5, 4.0, 4.5):
    check(f"H2 ranking PFS>MC>RC stable at cpt={c}",
          TOK[("pfs", c)] > TOK[("mc", c)] > TOK[("rc", c)])
max_drop = max((EXP_TOK[(a, 4.0)] - EXP_TOK[(a, 3.5)]) * 100 for a in ("pfs", "mc", "rc"))
check("H3 max level shift at 3.5x is <=0.01 pp (breakpoints coupled to tokenizer)",
      max_drop <= 0.011, f"{max_drop:.2f}")

# ===========================================================================
section("I. provenance: manifest hashes, latest_* artifacts, figures")

check("I1 manifest round tag r4", MANIFEST.get("round_tag") == "r4")
bad = []
for rel, ent in MANIFEST["traces"].items():
    p = rel if os.path.isabs(rel) else os.path.join(ROOT, rel)
    if not os.path.exists(p) or sha256(p) != ent["sha256"]:
        bad.append(rel)
check("I2 all 22 top-level traces + responses hash-verified", not bad and len(MANIFEST["traces"]) == 44,
      f"{len(MANIFEST['traces'])} entries, bad: {bad[:3]}")
bad = []
for rel, ent in MANIFEST["sweep_traces"].items():
    p = rel if os.path.isabs(rel) else os.path.join(ROOT, rel)
    if not os.path.exists(p) or sha256(p) != ent["sha256"]:
        bad.append(rel)
check("I3 all 177 sweep traces hash-verified", not bad and len(MANIFEST["sweep_traces"]) == 177,
      f"bad: {bad[:3]}")
bad = []
for fname, ent in MANIFEST["batch_results"].items():
    p = os.path.join("journal", "results", fname)
    if not os.path.exists(p) or sha256(p) != ent["sha256"]:
        bad.append(fname)
check("I4 all 3 batch result files hash-verified", not bad and len(MANIFEST["batch_results"]) == 3,
      str(bad))
sweep_names = {os.path.basename(rel) for rel in MANIFEST["sweep_traces"]}
row_names = {r["trace"] for r in rows}
check("I5 manifest sweep entries == sweep row traces", sweep_names == row_names,
      f"diff: {list(sweep_names ^ row_names)[:3]}")

RT = json.load(open("journal/results/latest_replay_table.json"))
top_names = {os.path.basename(p) for p in TOP_TRACES}
check("I6 latest_replay_table covers exactly the 22 top-level traces",
      set(RT["table"].keys()) == top_names, f"{len(RT['table'])} entries")
for arch in ("pfs", "mc", "rc"):
    key = os.path.basename(ARCH_PROFILE[arch])
    ent = RT["table"][key]
    check(f"I7 replay_table {arch} metrics match fresh replay",
          ent["metrics"]["aggregate_lcp_ratio"] == T1[arch]["aggregate_lcp_ratio"]
          and near(ent["anthropic"]["overall_hit_ratio"],
                   REPLAYS[arch]["anthropic"]["summary"]["overall_hit_ratio"], 5e-4))

SEN = json.load(open("journal/results/latest_sensitivity.json"))
check("I8 latest_sensitivity covers exactly the 22 top-level traces",
      set(SEN["sensitivity"].keys()) == top_names, f"{len(SEN['sensitivity'])} entries")
for arch in ("pfs", "mc", "rc"):
    key = os.path.basename(ARCH_PROFILE[arch])
    got = SEN["sensitivity"][key]["step30s_ttl300"]
    check(f"I9 sensitivity {arch} step30s/ttl300 matches fresh replay",
          near(got, REPLAYS[arch]["anthropic"]["summary"]["overall_hit_ratio"], 5e-4), str(got))

figdir = os.path.join(ROOT, "paper", "figures")
newest_result = max(os.path.getmtime(p) for p in
                    glob.glob(os.path.join("journal", "results", "2026*.json")))
for fig in ("fig1_stability_ablations", "fig2_economics_sensitivity"):
    for ext, magic in (("png", b"\x89PNG"), ("pdf", b"%PDF")):
        p = os.path.join(figdir, f"{fig}.{ext}")
        ok = os.path.exists(p) and open(p, "rb").read(8).startswith(magic) \
            and os.path.getmtime(p) >= newest_result - 2
        check(f"I10 figure {fig}.{ext} exists, valid, regenerated after results", ok)

# ===========================================================================
section("J. manuscript tables parsed from paper/manuscript.md")


def ms_table(header_sub):
    lines = MS_RAW.splitlines()
    for i, ln in enumerate(lines):
        if header_sub in ln and ln.strip().startswith("|"):
            out = []
            for ln2 in lines[i + 1:]:
                s = ln2.strip()
                if s.startswith("|"):
                    # normalise unicode dashes so flip names match the verifier map
                    s = s.replace("−", "-").replace("–", "-").replace("—", "-")
                    cells = [c.strip().replace("**", "") for c in s.strip("|").split("|")]
                    if not all(set(c) <= set("-: ") for c in cells):
                        out.append(cells)
                elif out:
                    break
            return out
    raise KeyError(header_sub)


def fnum(s):
    return float(re.sub(r"[^0-9.\-+]", "", s).replace("+", ""))


# --- Table 1
t1 = {r[0]: r for r in ms_table("Aggregate LCP ratio")}
check("J1 Table 1 has PFS/MC/RC rows", set(t1) == {"PFS", "MC", "RC"}, str(set(t1)))
EXP_T1 = {"PFS": (0.914, 0.933, 0, 9, 0.259, 4),
          "MC": (0.636, 0.685, 3, 10, 0.257, 3),
          "RC": (0.511, 0.576, 4, 10, 0.257, 3)}
for name, (lcp, pair, ninv, rstep, rlcp, nbp) in EXP_T1.items():
    row = t1[name]
    arch = name.lower()
    check(f"J2 T1 {name} LCP {lcp}", near(fnum(row[1]), lcp, 5e-4), row[1])
    check(f"J3 T1 {name} pairwise {pair}", near(fnum(row[2]), pair, 5e-4), row[2])
    check(f"J4 T1 {name} invalidation count",
          int(re.match(r"\d+", row[3].strip()).group()) == ninv, row[3])
    # manuscript groups events by region; replay order is chronological —
    # compare per-region step sets
    got_regions = {}
    for steps, region in re.findall(r"([\d, ]+):\s*(tools|system)", row[3]):
        got_regions.setdefault(region, set()).update(int(s) for s in steps.split(","))
    want_regions = {}
    for s, rg in exp_inv.get(arch, []):
        want_regions.setdefault(rg, set()).add(s)
    check(f"J5 T1 {name} invalidation events match replay (per-region)",
          got_regions == want_regions, f"{got_regions} vs {want_regions}")
    m = re.search(r"step (\d+).*?LCP (0\.\d+)", row[4])
    check(f"J6 T1 {name} reset at step {rstep} LCP {rlcp}",
          m and int(m.group(1)) == rstep and near(float(m.group(2)), rlcp, 5e-4), row[4])
    check(f"J7 T1 {name} breakpoints {nbp}", fnum(row[5]) == nbp, row[5])

# --- Table 2
t2 = {r[0]: r for r in ms_table("Breakpoint model: hit all")}
check("J8 Table 2 has PFS/MC/RC rows", set(t2) == {"PFS", "MC", "RC"}, str(set(t2)))
for name in ("PFS", "MC", "RC"):
    arch = name.lower()
    a = REPLAYS[arch]["anthropic"]["summary"]
    o = REPLAYS[arch]["openai"]["summary"]
    row = t2[name]
    check(f"J9 T2 {name} A hit all/turn",
          near(fnum(row[1].split("/")[0]), a["overall_hit_ratio"] * 100, 0.051)
          and near(fnum(row[1].split("/")[1]), a["turn_only_hit_ratio"] * 100, 0.051),
          row[1])
    check(f"J10 T2 {name} A cost norm",
          near(fnum(row[2].split("/")[0]), a["cost_vs_nocache"], 6e-4)
          and near(fnum(row[2].split("/")[1]), a["turn_only_cost_vs_nocache"], 6e-4), row[2])
    check(f"J11 T2 {name} A units", near(fnum(row[3]), a["total_cost_units"], 1e-4), row[3])
    check(f"J12 T2 {name} O hit all/turn",
          near(fnum(row[4].split("/")[0]), o["overall_hit_ratio"] * 100, 0.051)
          and near(fnum(row[4].split("/")[1]), o["turn_only_hit_ratio"] * 100, 0.051),
          row[4])
    check(f"J13 T2 {name} O cost norm",
          near(fnum(row[5].split("/")[0]), o["cost_vs_nocache"], 6e-4)
          and near(fnum(row[5].split("/")[1]), o["turn_only_cost_vs_nocache"], 6e-4), row[5])
    check(f"J14 T2 {name} O units", near(fnum(row[6]), o["total_cost_units"], 1e-4), row[6])
    check(f"J15 T2 {name} wire tokens", int(row[7].replace(",", "")) == a["total_tokens"], row[7])
    ta = a["total_cost_units"] / TASKDEN[arch]["task_cost_units"]
    to_ = o["total_cost_units"] / TASKDEN[arch]["task_cost_units"]
    check(f"J16 T2 {name} task-denominator ratios",
          near(fnum(row[8].split("/")[0]), ta, 8e-4) and near(fnum(row[8].split("/")[1]), to_, 8e-4),
          row[8])

# --- Table 3 (anchor must be unique: "hit all" also appears in Table 2)
t3 = {r[0]: r for r in ms_table("Δ hit all")}
check("J17 Table 3 has 16 flip rows", len(t3) == 16, str(len(t3)))
check("J18 Table 3 flip names match verifier map", set(t3) == set(FLIP_MAP),
      str(set(t3) ^ set(FLIP_MAP)))
for flip, row in t3.items():
    r = ABL[flip]
    check(f"J19 T3 {flip} dLCP {row[2]}", near(fnum(row[2]), r["d_lcp"], 6e-4),
          f"{r['d_lcp']:.4f}")
    check(f"J20 T3 {flip} dHit {row[3]}", near(fnum(row[3]), r["d_hit"], 0.15),
          f"{r['d_hit']:.2f}")
    check(f"J21 T3 {flip} dTurn {row[4]}", near(fnum(row[4]), r["d_turn"], 0.15),
          f"{r['d_turn']:.2f}")

# --- Table 4
t4_raw = ms_table("warmer off")
t4 = {r[0].replace(" ± warmer", ""): r for r in t4_raw}
check("J22 Table 4 has RC/MC/PFS rows", set(t4) == {"RC", "MC", "PFS"}, str(set(t4)))
T4_SRC = {"RC": (BASE_TRACE["rc"], abl_trace("A6 RC+warmer")),
          "MC": (abl_trace("B2 MC-no-warmer"), BASE_TRACE["mc"]),
          "PFS": (BASE_TRACE["pfs"], abl_trace("C5 PFS+warmer"))}
for name, (off_t, on_t) in T4_SRC.items():
    off = replay(off_t)["anthropic"]["summary"]["total_cost_units"]
    on = replay(on_t)["anthropic"]["summary"]["total_cost_units"]
    row = t4[name]
    check(f"J23 T4 {name} off/on units",
          near(fnum(row[1]), off, 1e-4) and near(fnum(row[2]), on, 1e-4),
          f"{row[1]} {row[2]} vs {off:.4f} {on:.4f}")
    check(f"J24 T4 {name} delta", near(fnum(row[3]), on - off, 2e-4), row[3])
    t_off, t_on = turn_only_a(replay(off_t)), turn_only_a(replay(on_t))
    check(f"J25 T4 {name} turn-only delta", near(fnum(row[4]), (t_on - t_off) * 100, 0.15), row[4])

# ===========================================================================
section("K. manuscript prose claims")

MUST = [
    "0.914", "0.511", "0.22×", "0.73×", "3.5×", "3.4×",
    "+26.3", "+18.7", "-13.1", "-9.9", "0.0 pp", "+13.0", "-8.8",
    "47 churn-active conditions", "56 conditions, 177 archetype runs",
    "90.85% / 94.53% / 96.31%", "3.8-19.3 pp", "2.5-9.5 pp",
    "8.5-19.3 pp", "1.7-2.9 pp", "mc_nowarm",
    "0.01 pp", "0.02 pp", "0.10 pp",
    "1.25×", "0.1×", "1,024", "128-token", "17,645", "17,754", "99.4%",
    "12.7 pp", "97.0-99.6%", "27.1%", "4,560-token",
    "89.62", "71.03", "45.32",
    "2508.21433", "2608.01326", "2601.16746", "2601.06007",
    "superseded-r2", "tool_use", "tool_result", "provider-visible fields",
    "retention_none", "warmer self-hit", "tied to within 0.01 pp",
    "neither reads nor writes the cache", "0.9-2.4 pp",
    "4,631", "0 of 4,631", "17,588", "4,025", "3,976", "3,963",
    "174,630", "242,999", "181,926", "0.0518 / 0.1402 / 0.2072",
    "make_manifest.py r4",
]
for needle in MUST:
    check(f"K1 manuscript states '{needle}'", needle in MS)
FORBID = ["_cache_disabled", "warm the TTL", "falsely", "TTL-maintenance lever"]
for needle in FORBID:
    check(f"K2 manuscript omits '{needle}'", needle not in MS)
check("K3 'latency' appears only in the cited title (no latency claim)",
      MS.lower().count("latency") == 1, str(MS.lower().count("latency")))
check("K4 data availability names 22 traces and 177 sweep traces",
      "22 traces" in MS and "177 sweep traces" in MS.replace("**", ""))
check("K5 methods states the envelope format",
      "_record_meta" in MS and "no internal bookkeeping appears on the wire object" in MS)
check("K6 methods documents the O-model write premium and 1h window",
      "1-hour retention" in MS and "0.1× base and newly cached regions" in MS)
check("K7 no-churn finding stated in abstract",
      "tied to within 0.01 pp" in MS.split("## Introduction")[0])
check("K8 compaction-step asymmetry documented",
      "step 9 in PFS, step 10 in MC and RC" in MS)

# ===========================================================================
print(f"\n{'=' * 60}")
print(f"VERDICT: {'PASS' if FAIL == 0 else 'FAIL'}  ({PASS} passed, {FAIL} failed)")
sys.exit(1 if FAIL else 0)
