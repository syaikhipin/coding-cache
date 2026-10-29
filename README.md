# reproducible bundle (mini-journal)

A minimal Python coding-agent harness with **switchable context-assembly
design dimensions** and a wire-request journal + cache-economics simulator.
This folder is the reproducible experiment bundle for a study of coding-agent
context assembly: every run appends dated JSONL traces and a summary entry to
`journal/INDEX.md`. It ships the harness, the drivers, the recorded traces and
the result tables. The manuscript and its LaTeX sources are not part of this
repository, so commands that read `paper/` need the manuscript supplied
locally. See `REPRODUCING.md` for the full reproduction recipe.

## Research object

The study compares **context-assembly methods** for coding-agent harnesses —
not products. A code audit of three production harnesses (kept in
`../research/`, referenced only as the origin of the design space) surfaced
six architectural design dimensions that plausibly govern prompt-cache
effectiveness. This harness instantiates the resulting design space as
switches, so archetypes can be mixed and ablated, not only reproduced.

## Design dimensions

| Dim | Question | Values |
|---|---|---|
| D1 | system-prompt structure | `stratified3` (stable strata before dynamic) / `single` |
| D2 | cache-breakpoint placement | `per_stratum` / `system_plus_trailing` / `system_tools_trailing` |
| D3 | environment freshness | `frozen` (session-start snapshot; changes appended as notifications) / `per_step` (recomputed, at front) / `minimal` (no date; git status recomputed) |
| D4 | compaction cache policy | `cache_aware` (prefix-preserving summary request) / `cache_dead` (fresh serialization) / `retention_none` (prefix-preserving shape, NO breakpoint markers — cache-inert by the documented breakpoint rule) |
| D5 | tool-list stability | `frozen` (deterministic, session-frozen) / `per_step_sorted` (re-resolved, sorted) |
| D6 | cache warmer | on / off (exact re-send of the last request every 3rd step) |

Archetypes: **PFS** (prefix-frozen stratified), **RC** (recomputed-context),
**MC** (minimal-context). Post-compaction *retention* (first user turn +
summary + last two turns) is identical under every D4 policy — it is a
constant of the harness, not a dimension.

## Method pipeline

- **E1 wire metrics** — prefix stability of the recorded request stream
  (token-weighted aggregate LCP ratio, early-prefix invalidation events,
  transcript resets, breakpoint coverage), over consecutive turn pairs.
- **E2 simulation** — replaying the journal through two documented provider
  cache models. Anthropic (explicit breakpoints): caching applies only to
  requests carrying at least one breakpoint (an unmarked request neither
  reads nor writes the cache — the documented "prefix up to a specified
  breakpoint" rule); ≤4 breakpoints enforced; ≥1,024-token entries (5-min
  TTL: 1.25× writes, 0.1× reads) or ≥2,048 (1-hour TTL: 2× writes);
  longest-cached-prefix read; exact re-sends count as hits. OpenAI
  (automatic prefix caching, current documented semantics): applies to every
  request, no opt-out; longest common prefix ≥1,024 tokens, rounded down to
  128-token blocks; 0.1× reads, 1.25× writes; 1-hour sliding window from
  last use. Reporting separates all-request vs turn-only aggregates, absolute
  cost units (with raw read/write/uncached token categories) vs normalized
  cost, and a task-level no-cache denominator.
- **E3 ablation** — single-dimension flips between archetype points, holding
  the scripted agent and all schedules identical, so any metric delta is
  attributable to the flipped dimension alone.
- **E4 robustness sweep** — 6 churn schedules (mid/early/late/irr1/irr2/none)
  × 3 session lengths × 3 compaction thresholds + features task + repo2
  fixture; every `none` cell has an `mc_nowarm` composition-control twin
  (MC with its warmer disabled).

The available API proxy strips all cache telemetry (`cached_tokens` always 0),
which motivates E1+E2 as the measurement methodology.

## Layout

```
minicache/            the harness package
  config.py           dimension switches + archetype presets + run config
  tasks.py            toy-repo + repo2 fixtures (buggy modules + tests, padded READMEs)
  prompts.py          system-prompt sections with stable/dynamic strata
  tools.py            5 tools + tool-list stability modes (D5)
  wire.py             request assembly + breakpoint placement (D2); pure wire objects
  api.py              thin client for the local Anthropic-style endpoint
  recorder.py         journal writer (JSONL + INDEX.md)
  harness.py          agent loop, compaction (D4), warmer (D6)
  metrics.py          LCP ratio, invalidation events, transcript resets (E1)
  simulator.py        Anthropic + OpenAI cache models (E2)
experiments/
  run_profiles.py     run a task through chosen archetypes, journal it
  ablate.py           E3: flip one dimension at a time, compare to baseline
  replay.py           E2 over all journal traces (+ TTL sensitivity sweep)
  sweep.py            E4: 6×3×3 grid + features + repo2 + mc_nowarm controls
  figures.py          Nature-style figures from journal/results/*.json
  make_manifest.py    SHA-256 manifest of traces + results (round-tagged)
  verify_manuscript.py manuscript checker: recomputes every number (exit 1 on mismatch)
journal/              dated JSONL traces + INDEX.md + ENTRY-*.md (the journal)
  sweep-traces/       the 177 current sweep traces (checksummed in MANIFEST.json)
  results/            batch result JSONs + latest_replay_table/latest_sensitivity
```

## Usage

```bash
cd reproducible
python3 experiments/run_profiles.py --profiles pfs,rc,mc --replay
python3 experiments/ablate.py                     # E3 ablation grid
python3 experiments/replay.py [--sensitivity]     # E2 over all journal traces
python3 experiments/sweep.py                      # E4 robustness grid (177 runs)
python3 experiments/verify_manuscript.py          # manuscript checker (439 checks)
```

Environment: `ANTHROPIC_BASE_URL` (default `http://127.0.0.1:20128/v1`),
`ANTHROPIC_AUTH_TOKEN`, `MINICACHE_MODEL` (default `dh/dahono/deepseek-v4.1-flash`).
`--api live` in run_profiles.py calls the real model through the proxy for the
assistant text (tool traffic stays scripted for control); `--api script`
(default) is fully offline and deterministic.

## Current results (Entry 005, 2026-09-24, post-Round-3 rebuild)

Scripted mode, 12 steps, seed 0, compaction threshold 12400, archetype
system prompts volume-equalized (3,956–3,995 stable tokens each so stability
is not confounded with prompt volume; stable front ≈4.4k tokens incl. tools).
Analyzed batches: `20260924T233922` (profiles) + `20260924T233925`
(ablations); sweep: `20260924T234023` (177 runs, 56 conditions). Wire shape:
documented tool-use blocks (assistant `tool_use`, user `tool_result`);
journals are envelopes `{"_record_meta": {...}, "request": <pure
provider-visible request>}` — the request object carries ONLY `model`,
`tools`, `system`, `messages`, `max_tokens`.

| archetype | E1 LCP ratio | E1 invalidations | E2 Anthropic hit (all/turn) | E2 cost vs no-cache (norm/turn) | cost units (BP / auto) |
|---|---|---|---|---|---|
| PFS | 0.914 | 0 | 89.7% / 88.5% | 0.219× / 0.232× | 0.0382 / 0.0391 M |
| MC  | 0.636 | 3 | 71.1% / 68.9% | 0.415× / 0.458× | 0.1006 / 0.0806 M |
| RC  | 0.512 | 4 | 45.3% / 48.8% | 0.729× / 0.689× | 0.1324 / 0.1328 M |

Automatic (OpenAI) model: PFS 89.2%, MC 79.8%, RC 45.2% — same ranking.
RC/PFS absolute cost ratio 3.5× under breakpoint pricing (3.4× under
automatic-prefix pricing). Task-denominator ratios: PFS 0.244×/0.250×,
MC 0.597×/0.478×, RC 0.785×/0.787×. TTL-3600 breakpoint re-pricing:
0.0517 / 0.1399 / 0.2069 M (hits unchanged).

Key findings (see `journal/ENTRY-2026-09-24-005.md`):

- **Stability dimensions dominate bidirectionally**: freezing RC's env
  (+26.3 pp) or tools (+18.7 pp) recovers most of the PFS gap; breaking them
  in PFS costs −13.1 / −9.9 pp (turn-only −14.4 / −11.0).
- **Stratification is pure relocation**: giving RC strata gains +6.9 pp,
  but merging PFS's strata (same content, same ordering) changes exactly
  0.0 pp — D1 has no effect of its own beyond where mutable content sits.
- **Breakpoint placement (D2) moves nothing** from any tested base.
- **Compaction policy (D4) acts on the summary request only** (all-request
  deltas +8.3 / +7.3 / −7.8 pp; turn-only 0.0 pp in this 12-step schedule).
  PFS's cache-aware summary request reads 99.4% of its own tokens.
- **The warmer (D6) is probe composition, and it can hurt**: turn-only delta
  0.0 pp from RC/MC bases (−2.9 pp from PFS, where probes age entries);
  all-request deltas (+12.9 / −8.7 / +0.6 pp) are self-hit composition;
  absolute cost strictly increases (+0.0056/+0.0056/+0.0109 M).
- **Robustness is churn-conditional**: PFS ranks first in all 47
  churn-active conditions under both models (a_hit margins 3.8–18.6 pp;
  o_hit 2.5–9.4 pp; 8.4–18.6 pp wherever mid-session compaction fires).
- **Under true no-churn (env AND tool churn disabled) the ranking dissolves
  into measurement composition**: with no compaction, turn-only hits are
  EXACTLY tied across archetypes (90.86/94.54/96.33% at 12/20/30 steps;
  LCP exactly 1.000 everywhere). MC's raw all-request lead (+0.9–2.4 pp) is
  warmer self-hits — the `mc_nowarm` control collapses it to an exact tie.
  (The Round-2 "TTL maintenance" reading of this pattern was wrong and is
  retracted.) Where mid-session compaction fires, MC trails by 1.7–2.7 pp
  turn-only — traced to the warmer's probes advancing the simulated clock
  across the compaction boundary, not to its unmarked compaction request
  (removing the probes, not the request, removes the deficit;
  mc_nowarm ties PFS within 0.02 pp).

`experiments/verify_manuscript.py` recomputes every number in the
manuscript (not bundled; supply it locally) from the traces (423 checks, exit 1 on mismatch):
wire fidelity (envelope purity, tool-block shapes, no synthetic switches),
Tables 1–4 replayed from traces, the full sweep composition matrix,
tokenizer sensitivity (3.5/4.0/4.5 chars/token, ranking stable),
MANIFEST hash verification (22 top-level traces + responses, 177 sweep
traces, 3 batch results), figure freshness, and manuscript-table parsing.
`journal/MANIFEST.json` (round tag `r3`) carries SHA-256 of all analyzed
artifacts (regenerate with `experiments/make_manifest.py r3`). Figures
regenerate without any re-experiment: `python3 experiments/figures.py`
reads only `journal/results/*.json`.

## Documented assumptions

- **Tokenizer**: ~4 chars/token approximation (`chars_per_token` parameter;
  sensitivity-tested at 3.5 and 4.5 — ranking stable), applied identically to
  all archetypes. Absolute numbers are approximate; between-archetype deltas
  are the measurement.
- **Anthropic model**: content-addressed prefix entries created at
  `cache_control` breakpoints (max 4), longest matching prefix is read,
  suffix written at 1.25× (5-min TTL) / 2× (1-hour TTL), reads at 0.1×,
  sliding TTL refreshed on read, cacheable floors 1,024/2,048 tokens;
  **unmarked requests neither read nor write the cache** (documented
  breakpoint rule) (public documentation semantics).
- **OpenAI model**: automatic prefix caching on every request (no opt-out),
  ≥1,024-token prefixes matched as longest common prefix rounded down to
  128-token blocks, reads 0.1×, cache writes 1.25×, 1-hour window from last
  use (current documented semantics).
- Post-compaction retention is a harness constant (first user turn + summary
  + last 2 turns), identical for all D4 policies; `retention_none` differs
  only in that its summary REQUEST carries no breakpoint markers.
- This harness **instantiates the design space**; it does not execute the
  real harnesses. Real-harness wire traces (E1 on the audited systems) are a
  separate external-validation step.
- The 22 top-level traces in `journal/` are exactly the analyzed profiles +
  ablation batches (checksummed in `journal/MANIFEST.json`). The 177 current
  sweep traces live in `journal/sweep-traces/` (also checksummed). All
  superseded Round-2-and-earlier runs (flattened tool-call wire shape, old
  simulator pricing, pre-fix batches) are excluded from this repository and
  are explicitly OUTSIDE the manifest.
- `experiments/sweep.py` (E4 robustness grid) and `experiments/
  verify_manuscript.py` (manuscript checker) are also part of the driver set.
