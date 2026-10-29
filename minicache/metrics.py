"""E1: wire-level prefix-stability metrics over a recorded request stream.

Computes, for each consecutive request pair, the token-length of the longest
common prefix (LCP) of the canonical serialized request, and reports LCP ratio
(= reusable share of the previous request), invalidation events (LCP collapsing
into the system/tools region), and breakpoint coverage.

Round-3 wire shape: journal lines are envelopes {"_record_meta": {...},
"request": <pure wire request>}; the request object itself carries ONLY
provider-visible fields (model, tools, system, messages, max_tokens) — no
underscore-prefixed bookkeeping. pure()/meta() also accept the legacy flat
records so archived pre-Round-3 traces still replay.
"""

import json

APPROX = 4  # chars per token approximation (sensitivity-tested at 3.5/4.5)


def pure(record: dict) -> dict:
    """The provider-visible request inside a journal envelope (or the record
    itself for legacy flat lines)."""
    return record.get("request", record)


def meta(record: dict) -> dict:
    """Journal metadata (profile/step/kind) from an envelope or legacy record."""
    return record.get("_record_meta") or record.get("_meta") or {}


def strip_markers(obj):
    """Recursively remove cache_control at EVERY depth.

    Real provider traces nest cache_control inside content blocks
    ({"content": [{"type": "text", ..., "cache_control": ...}]}), so a
    top-level-only strip would count marker movement as content mutation.
    cache_control is transport metadata, not cached content: a breakpoint
    marker moving to a new trailing message must NOT count as a prefix change.
    """
    if isinstance(obj, dict):
        return {k: strip_markers(v) for k, v in obj.items() if k != "cache_control"}
    if isinstance(obj, list):
        return [strip_markers(x) for x in obj]
    return obj


def has_marker(obj) -> bool:
    """True if cache_control occurs anywhere inside obj (any depth)."""
    if isinstance(obj, dict):
        if "cache_control" in obj:
            return True
        return any(has_marker(v) for v in obj.values())
    if isinstance(obj, list):
        return any(has_marker(x) for x in obj)
    return False


def canon_json(obj) -> str:
    """Canonical JSON with cache_control stripped recursively."""
    return json.dumps(strip_markers(obj), sort_keys=True)


def _parts(request: dict) -> list:
    """Canonical serialization parts in wire order: tools, system, messages."""
    parts = []
    for t in request.get("tools", []):
        parts.append(canon_json(t))
    for s in request.get("system", []):
        parts.append(canon_json(s))
    for m in request.get("messages", []):
        parts.append(canon_json(m))
    return parts


def _chunk(s: str, c: float):
    """Chunk a string into ~c-char units (fractional c supported for the
    tokenizer-sensitivity analysis); a final partial chunk of a part counts,
    matching the original integer-4 behaviour."""
    k = 0
    n = len(s)
    while True:
        a = int(k * c)
        b = int((k + 1) * c)
        if a >= n:
            return
        yield s[a:b]
        k += 1


def canonical_tokens(request: dict, chars_per_token: float = APPROX) -> list:
    """Canonical serialization in wire order, chunked at chars_per_token."""
    request = pure(request)
    chunks = []
    for p in _parts(request):
        chunks.extend(_chunk(p, chars_per_token))
    return chunks


def lcp_len(a: list, b: list) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def region_of(request: dict, token_index: int, chars_per_token: float = APPROX) -> str:
    """Map a canonical-token index to its structural region."""
    request = pure(request)
    sizes = []
    for t in request.get("tools", []):
        s = canon_json(t)
        sizes.append(("tools", (len(s) + chars_per_token - 1) // chars_per_token))
    for sm in request.get("system", []):
        s = canon_json(sm)
        sizes.append(("system", (len(s) + chars_per_token - 1) // chars_per_token))
    for m in request.get("messages", []):
        s = canon_json(m)
        sizes.append(("messages", (len(s) + chars_per_token - 1) // chars_per_token))
    acc = 0
    for name, n in sizes:
        if token_index < acc + n:
            return name
        acc += n
    return "tail"


def analyze(requests: list) -> dict:
    """requests: journal records (envelopes or legacy flat lines)."""
    out_pairs = []
    stream = [r for r in requests if meta(r).get("kind") in (None, "turn", "compact", "warmer")]
    # analyze consecutive TURN requests for prefix stability; compact/warmer handled
    # separately since their shapes legitimately differ.
    turns = [r for r in stream if meta(r).get("kind", "turn") == "turn"]
    for i in range(1, len(turns)):
        a = canonical_tokens(turns[i - 1])
        b = canonical_tokens(turns[i])
        l = lcp_len(a, b)
        entry = {
            "from_step": meta(turns[i - 1])["step"],
            "to_step": meta(turns[i])["step"],
            "prev_tokens": len(a),
            "cur_tokens": len(b),
            "lcp_tokens": l,
            "lcp_ratio_prev": round(l / max(len(a), 1), 4),
            "lcp_break_region": region_of(turns[i - 1], l) if l < len(a) else "full",
            # transcript reset: the previous request's message list is NOT an
            # elementwise prefix of the current one (history was rewritten —
            # e.g. compaction), as opposed to benign append growth
            "transcript_reset": not _messages_are_prefix(turns[i - 1], turns[i]),
        }
        out_pairs.append(entry)

    total_prev = sum(p["prev_tokens"] for p in out_pairs) or 1
    total_lcp = sum(p["lcp_tokens"] for p in out_pairs)
    invalidations = [p for p in out_pairs if p["lcp_break_region"] in ("tools", "system")]
    resets = [p for p in out_pairs if p["transcript_reset"]]

    n_bp = _breakpoint_count(turns[-1]) if turns else 0
    compact_reqs = [r for r in stream if meta(r).get("kind") == "compact"]
    return {
        "n_turn_requests": len(turns),
        "n_compact_requests": len(compact_reqs),
        "n_warmer_requests": sum(1 for r in stream if meta(r).get("kind") == "warmer"),
        "aggregate_lcp_ratio": round(total_lcp / total_prev, 4),
        "mean_pairwise_lcp_ratio": round(
            sum(p["lcp_ratio_prev"] for p in out_pairs) / max(len(out_pairs), 1), 4),
        "invalidation_events": [
            {"at_step": p["to_step"], "region": p["lcp_break_region"],
             "lcp_ratio": p["lcp_ratio_prev"]} for p in invalidations],
        "n_invalidation_events": len(invalidations),
        # early-prefix invalidations and transcript resets are reported
        # SEPARATELY: a compaction resets the conversation (message region)
        # without touching the system/tools prefix, and conflating the two
        # hides architecture differences in the early-prefix axis
        "transcript_reset_events": [
            {"at_step": p["to_step"], "lcp_ratio": p["lcp_ratio_prev"]} for p in resets],
        "n_transcript_reset_events": len(resets),
        "breakpoints_last_request": n_bp,
        "pairs": out_pairs,
    }


def _messages_are_prefix(prev: dict, cur: dict) -> bool:
    """True if prev's message list (cache_control-stripped, recursively) is an
    elementwise prefix of cur's."""
    pm = strip_markers(pure(prev).get("messages", []))
    cm = strip_markers(pure(cur).get("messages", []))
    if len(pm) > len(cm):
        return False
    return all(pm[i] == cm[i] for i in range(len(pm)))


def _breakpoint_count(record: dict) -> int:
    request = pure(record)
    n = sum(1 for t in request.get("tools", []) if has_marker(t))
    n += sum(1 for s in request.get("system", []) if has_marker(s))
    n += sum(1 for m in request.get("messages", []) if has_marker(m))
    return n


def analyze_trace(trace_path: str) -> dict:
    reqs = []
    with open(trace_path) as f:
        for line in f:
            line = line.strip()
            if line:
                reqs.append(json.loads(line))
    return analyze(reqs)
