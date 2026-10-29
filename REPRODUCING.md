# Reproducible artifact bundle

This folder is the companion bundle for a study of coding-agent context
assembly. It contains the source code, fixtures, wire traces and result files
behind every number in the manuscript; the manuscript itself (Markdown, LaTeX,
PDF and figures) is not part of this repository. No absolute paths, no external
dependencies beyond Python 3 + matplotlib.

## Layout

```
reproducible/
├── README.md            <- this file
├── minicache/           <- simulator, metrics, wire recording, task fixtures
│   ├── simulator.py     <- two cache-model replayers (Anthropic-style
│   │                       breakpoint pricing, OpenAI-style automatic prefix)
│   ├── metrics.py       <- longest-common-prefix / invalidation analytics
│   ├── wire.py          <- envelope recording (_record_meta side-channel)
│   ├── harness.py       <- deterministic agent-task execution
│   └── tasks.py         <- fixture repositories (embedded as literals —
│                           nothing outside this bundle is read)
├── experiments/         <- drivers
│   ├── run_profiles.py  <- baseline traces for the three archetypes
│   ├── ablate.py        <- 16 single-flip ablation cells
│   ├── sweep.py         <- 56-condition × 3-archetype grid
│   ├── replay.py        <- rebuilds latest_replay_table / latest_sensitivity
│   ├── figures.py       <- regenerates the manuscript figures
│   ├── make_manifest.py <- SHA-256 manifest over all analyzed artifacts
│   └── verify_manuscript.py  <- 439-check end-to-end verifier (the gate)
└── journal/             <- all recorded data
    ├── *.jsonl                <- top-level traces (profiles + ablations)
    ├── *.responses.json       <- provider-side response records, per trace
    ├── results/               <- profiles/ablations/sweep batch results,
    │                             latest_replay_table, latest_sensitivity
    ├── sweep-traces/          <- sweep traces + responses
    ├── MANIFEST.json          <- SHA-256 hashes, round tag r4
    └── INDEX.md               <- batch index
```

## Provenance of the current generation (r4)

The Round-4 review pass fixed two recording defects — the `tool_use` wire
shape (provider-valid `id`/`name`/`input` instead of a flattened `tool`
key) and the tokenizer-sensitivity coupling of breakpoint positions —
then **all traces were regenerated** as `r4` (batches `20260925T210927`
profiles, `20260925T211330` ablations, `20260925T232049` sweep).
`journal/MANIFEST.json` hash-covers exactly that set.

## How to reproduce the paper end-to-end

All commands are run from this folder (`reproducible/`). Python 3.9+ with
`matplotlib` and `numpy` is required; the IEEE PDF additionally needs a
LaTeX distribution with `IEEEtran.cls`.

### 1. Verify the as-shipped numbers (no re-running experiments)

```bash
python3 experiments/verify_manuscript.py
```

Expected output ends with:

```
VERDICT: PASS  (439 passed, 0 failed)
```

This single script re-derives every table, prose claim, margin band, and
sensitivity figure straight from the persisted traces, hash-checks the
manifest, and cross-checks the manuscript text. It is the acceptance gate
for any change to data or paper.

### 2. Regenerate the derived caches

```bash
python3 experiments/replay.py
```

Rebuilds `journal/results/latest_replay_table.json` and
`journal/results/latest_sensitivity.json` over exactly the 22 top-level
traces. Idempotent; run after any trace change.

### 3. Regenerate the figures

```bash
python3 experiments/figures.py
```

Writes `fig1_stability_ablations.{pdf,png}` and
`fig2_economics_sensitivity.{pdf,png}` (300 dpi, colorblind-safe) into the
manuscript's figure directory, which lives outside this bundle.

### 4. Rebuild the manifest after changing any analyzed artifact

```bash
python3 experiments/make_manifest.py r4
```

Recomputes SHA-256 checksums over the 22 top-level traces (+ responses),
the 3 newest batch results, and all 177 sweep traces (+ responses).
Anything not hash-covered is, by contract, not part of the analyzed set.

### 5. Re-record everything from scratch (full pipeline)

```bash
python3 experiments/run_profiles.py    # 3 baseline traces
python3 experiments/ablate.py          # 3 bases + 16 single-flip cells
python3 experiments/sweep.py           # 56 conditions x archetypes
python3 experiments/replay.py          # derived caches
python3 experiments/make_manifest.py r5   # new tag after re-recording
python3 experiments/figures.py
python3 experiments/verify_manuscript.py  # must still PASS before publishing
```

Note: fresh recordings get new batch timestamps; the verifier always
analyzes the newest batch of each kind, and the manifest must be
regenerated to match. Update the manuscript numbers only via values the
verifier recomputes — that is what keeps the paper and the data from
drifting apart (the failure mode that originally forced the Round-3 →
Round-4 resync).

## What is intentionally not here

`mini-journal/review-stage/` (the AI review-round records) stayed at the
old location: it documents the editorial process, not the experiment.
The old `mini-journal/` folder now contains only that plus a pointer to
this bundle.
