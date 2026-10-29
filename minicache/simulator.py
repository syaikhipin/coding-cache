"""E2: trace-driven cache-economics simulator.

Two documented provider models:

Anthropic (explicit breakpoints):
  - cache entries are created at cache_control breakpoints (max 4 per request)
  - caching applies ONLY to requests that carry at least one breakpoint: the
    provider checks whether a prefix "up to a specified cache breakpoint" is
    cached, so an unmarked request neither reads nor writes the cache
    (documented) — this is how the MC compaction policy (retention_none)
    opts out, a real client-side mechanism rather than a synthetic flag
  - on a request, the LONGEST previously-written entry that is a prefix of this
    request is read at 0.1x; the region beyond it up to the deepest reachable
    breakpoint is written at 1.25x (5-min TTL) / 2x (1-hour TTL)
  - entries expire after TTL (5 min default, 1h optional) since LAST use
  - cacheable minimum: 1,024 tokens (5-min TTL) / 2,048 (1-hour TTL)
  - tool results / messages beyond the last breakpoint are billed at 1.0x

OpenAI (automatic prefix caching, current documented semantics for GPT-5.6+):
  - caching applies to every request (no client opt-out)
  - a read walks the request's cache-lookup boundaries longest-first and takes
    the longest matching cached prefix >= the minimum cacheable length
    (1,024 tokens); reported cached tokens are rounded DOWN to a 128-token
    multiple. We approximate the boundary walk by matching the request's own
    full sequence against stored entries (longest common prefix, floored to
    128-token blocks) — conservative and model-agnostic
  - cache writes cost 1.25x uncached input on the newly cached region; reads
    cost 0.1x; retention window 1h measured from last use

Wall-clock: the journal carries no timestamps per request (scripted runs are
faster than real time), so TTL is modeled in REQUEST-AGE units: one request =
`seconds_per_step` simulated seconds (default 30). A compact/warmer request
also advances time. Sensitivity analysis varies seconds_per_step and TTL.
"""

import json

from .metrics import lcp_len, pure, meta

PRICES = {  # per 1M tokens, relative to base input price
    "anthropic": {"read": 0.1, "base": 1.0},
    "openai": {"read": 0.1, "write": 1.25, "base": 1.0},
}
# Anthropic write multiplier + cacheable-minimum are TTL-specific (see
# AnthropicCache): 1.25x / 1,024 tokens for the 5-minute default, 2x / 2,048
# tokens for the 1-hour option.


def canonical_tokens(request: dict) -> list:
    from .metrics import canonical_tokens as ct
    return ct(request)


def breakpoint_indices(request: dict, tokens: list,
                       chars_per_token: float = 0.0) -> list:
    """Indices (in canonical token space) where breakpoints sit, in wire order.

    Positions are computed on cache_control-stripped tokens (canon_json): the
    marker itself is not cached content, so entry lengths here match the
    canonical_tokens() streams the cache is keyed on. chars_per_token must
    match the value used for the token stream (0.0 = the default APPROX),
    otherwise marker positions drift out of sync with the replayed tokens.
    """
    from .metrics import canon_json, has_marker, APPROX
    cpt = chars_per_token if chars_per_token else APPROX
    idx = []
    pos = 0
    def walk(items):
        nonlocal pos
        for item in items:
            s = canon_json(item)
            n = (len(s) + cpt - 1) // cpt
            n = int(n)
            if has_marker(item):
                idx.append(pos + n - 1)
            pos += n
    walk(request.get("tools", []))
    walk(request.get("system", []))
    walk(request.get("messages", []))
    return idx


class AnthropicCache:
    def __init__(self, ttl_seconds: int = 300, max_bps: int = 4):
        self.entries = {}  # token tuple -> last-use time
        self.ttl = ttl_seconds
        # documented per-TTL terms: 5-minute cache — writes at 1.25x, cacheable
        # prefix >= 1,024 tokens; 1-hour cache — writes at 2x, minimum 2,048
        # tokens. (Floors vary by model; these are the smaller documented
        # values, used so toy-scale sessions can exercise the cache at all.)
        if ttl_seconds <= 300:
            self.write_mult = 1.25
            self.min_entry = 1024
        else:
            self.write_mult = 2.0
            self.min_entry = 2048
        self.max_bps = max_bps

    def _prune(self, now: float):
        for k in [k for k, t in self.entries.items() if now - t > self.ttl]:
            del self.entries[k]

    def request(self, tokens: list, bps: list, now: float) -> dict:
        self._prune(now)  # prune BEFORE reading: expired entries cannot serve reads
        t = tuple(tokens)
        bps = sorted(bps)[: self.max_bps]  # documented API limit: <=4 breakpoints
        # longest cached entry that is a prefix of this request (an entry equal
        # to the whole request counts: exact re-sends are cache hits)
        best_len = 0
        for entry in self.entries:
            if (len(entry) >= self.min_entry and len(entry) > best_len
                    and len(entry) <= len(t) and t[:len(entry)] == entry):
                best_len = len(entry)
        read = best_len
        # real semantics: an entry is written at EVERY eligible breakpoint
        # beyond the read point (up to 4 per request); the deepest one is the
        # useful read next time. Entries below this TTL's minimum length are
        # not cacheable and are dropped.
        write_to = 0
        for bp in bps:
            if bp + 1 >= self.min_entry and bp > read:
                self.entries[t[:bp + 1]] = now
                write_to = max(write_to, bp + 1)
        write = max(0, write_to - read)
        uncached = len(t) - max(read, write_to)
        if read:
            self.entries[t[:read]] = now  # refresh TTL on read
        p = PRICES["anthropic"]
        cost = (read * p["read"] + write * self.write_mult
                + uncached * p["base"]) / 1e6
        return {"tokens": len(t), "read": read, "write": write, "uncached": uncached,
                "cost_units": cost, "hit_ratio": round(read / max(len(t), 1), 4)}

    def inert(self, tokens: list) -> dict:
        """Unmarked request: no cache interaction at all (documented: caching
        applies to prefixes 'up to a specified cache breakpoint')."""
        t = tuple(tokens)
        return {"tokens": len(t), "read": 0, "write": 0, "uncached": len(t),
                "cost_units": len(t) * 1.0 / 1e6, "hit_ratio": 0.0}


class OpenAICache:
    """Automatic prefix caching with block quantisation and PARTIAL matching.

    Documented provider semantics (current OpenAI docs): cache lookup walks
    boundaries longest-first; a cached prefix >= 1,024 tokens may serve a hit;
    reported cached tokens are rounded down to a 128-token multiple. The read
    is therefore the LONGEST COMMON BLOCK-ALIGNED PREFIX of this request
    against stored entries — two requests that share their first N blocks
    reuse those blocks even if they diverge later. Writes of newly cached
    regions are billed at 1.25x uncached input; reads at 0.1x.

    Divergence cost modelling: when a request diverges from its best match at
    block k, blocks from k onward are uncached input at 1.0x and the divergent
    tail is re-cached at the 1.25x write rate.
    """

    BLOCK = 128

    def __init__(self, min_prefix: int = 1024, window: float = 3600):
        self.prefixes = []  # (token tuple, last-use)
        self.min_prefix = min_prefix
        self.window = window

    def request(self, tokens: list, now: float) -> dict:
        t = tuple(tokens)
        self._prune(now)  # prune BEFORE reading: expired entries cannot serve reads
        best = 0
        best_entry = None
        for entry, _ in self.prefixes:
            if len(entry) < self.min_prefix:
                continue
            # longest common prefix, rounded DOWN to a block boundary: only
            # whole 128-token blocks are reusable; the sub-block tail of the
            # match (and everything after divergence) is uncached
            n = lcp_len(entry, t)
            if n >= self.min_prefix and (n // self.BLOCK) * self.BLOCK > best:
                best = (n // self.BLOCK) * self.BLOCK
                best_entry = entry
        read = best
        # model automatic caching: store this request's prefix for future
        # matches; refresh the best-matching entry's last-use so the sliding
        # window measures time since last USE, not first appearance
        self.prefixes.append((t, now))
        if best_entry is not None and read > 0:
            for i, (e, u) in enumerate(self.prefixes):
                if e is best_entry:
                    self.prefixes[i] = (e, now)
        p = PRICES["openai"]
        # newly cached tail (beyond the read, which the provider re-uses)
        # is written at the 1.25x cache-write rate
        write = len(t) - read
        cost = (read * p["read"] + write * p["write"]) / 1e6
        return {"tokens": len(t), "read": read, "write": write,
                "uncached": 0, "cost_units": cost,
                "hit_ratio": round(read / max(len(t), 1), 4)}

    def _prune(self, now: float, window: float | None = None):
        w = self.window if window is None else window
        self.prefixes = [(e, u) for e, u in self.prefixes if now - u <= w]


def replay_trace(trace_path: str, seconds_per_step: float = 30.0,
                 anthropic_ttl: int = 300, openai_min_prefix: int = 1024) -> dict:
    reqs = []
    with open(trace_path) as f:
        for line in f:
            if line.strip():
                reqs.append(json.loads(line))
    return replay_requests(reqs, seconds_per_step, anthropic_ttl, openai_min_prefix)


def replay_requests(reqs: list, seconds_per_step: float = 30.0,
                    anthropic_ttl: int = 300, openai_min_prefix: int = 1024,
                    openai_window: float = 3600.0, drop_warmers: bool = False,
                    chars_per_token: float = 0.0) -> dict:
    """Replay journal records (envelopes or legacy flat lines).

    drop_warmers=True removes warmer requests before replay — the
    composition-control view that separates warmer self-hit effects from
    turn-level caching (the Round-3 finding).
    """
    from .metrics import canonical_tokens as ct
    acache = AnthropicCache(ttl_seconds=anthropic_ttl)
    ocache = OpenAICache(min_prefix=openai_min_prefix, window=openai_window)
    now = 0.0
    a_rows, o_rows = [], []
    for r in reqs:
        kind = meta(r).get("kind")
        if drop_warmers and kind == "warmer":
            continue
        req = pure(r)
        t = ct(req, chars_per_token) if chars_per_token else ct(req)
        bps = breakpoint_indices(req, t, chars_per_token=chars_per_token)
        if bps:
            a_rows.append({"kind": kind, **acache.request(t, bps, now)})
        else:
            # no breakpoint on this request: it neither reads nor writes the
            # cache (documented Anthropic semantics; the retention_none
            # compaction policy's opt-out mechanism)
            a_rows.append({"kind": kind, **acache.inert(t)})
        o_rows.append({"kind": kind, **ocache.request(t, now)})
        now += seconds_per_step

    def summarize(rows):
        tot = sum(r["tokens"] for r in rows) or 1
        rd = sum(r["read"] for r in rows)
        billed = sum(r["cost_units"] for r in rows)
        # base-price cost of the identical stream with no caching at all
        nocache = tot / 1e6
        # per-request-class splits so probe/warmer composition cannot hide in
        # an aggregate: turn rows only, and the probe's absolute cost
        turn_rows = [r for r in rows if (r.get("kind") or "turn") == "turn"]
        turn_tot = sum(r["tokens"] for r in turn_rows) or 1
        return {
            "requests": len(rows),
            "total_tokens": tot,
            # raw input-token categories (a provider bill reports categories,
            # not one weighted number — "cost_units" is the weighted equivalent)
            "read_tokens": rd,
            "write_tokens": sum(r.get("write", 0) for r in rows),
            "uncached_tokens": sum(r["uncached"] for r in rows),
            "cached_read_tokens": rd,
            "overall_hit_ratio": round(rd / tot, 4),
            "turn_only_hit_ratio": round(
                sum(r["read"] for r in turn_rows) / turn_tot, 4),
            "total_cost_units": round(billed, 6),
            "nocache_cost_units": round(nocache, 6),
            "cost_vs_nocache": round(billed / nocache, 4),
            "turn_only_cost_vs_nocache": round(
                sum(r["cost_units"] for r in turn_rows) / (turn_tot / 1e6), 4),
        }

    return {"anthropic": {"summary": summarize(a_rows), "rows": a_rows},
            "openai": {"summary": summarize(o_rows), "rows": o_rows}}


def replay_task_denominator(reqs: list) -> dict:
    """Task-level no-cache denominator: the cost of the TASK's context — the
    turn requests only, every token at base price — identical across
    archetypes in spirit but computed from each stream's own turns, and
    reported per stream. This is the 'same work' denominator the Round-3
    review asked for: it excludes compaction/warmer bookkeeping traffic from
    the denominator, so cross-archetype ratios are not volume-dependent.

    Returns {"task_tokens": n, "task_cost_units": x} for the stream."""
    tot = 0
    for r in reqs:
        if (meta(r).get("kind") or "turn") == "turn":
            tot += len(canonical_tokens(pure(r)))
    return {"task_tokens": tot, "task_cost_units": round(tot / 1e6, 6)}
