"""Wire-request assembly + breakpoint placement per archetype.

Round-3 wire shape: build_request returns a PURE provider-visible request
(model, tools, system, messages, max_tokens — nothing else). Journal
metadata travels in a separate envelope written by the harness/recorder
({"_record_meta": {...}, "request": {...}}), so the recorded wire object is
exactly what a client would send. Underscore-prefixed fields no longer
exist anywhere in the request.

Anthropic-documented wire shapes:
  - messages carry content as a LIST of content blocks, and cache_control
    markers sit on content blocks (never on the message object itself)
  - assistant tool calls are tool_use blocks; tool results are user
    tool_result blocks (the provider's documented tool-use shape — no
    flattening to text)
  - system blocks are text content blocks; markers sit on the block
  - tool definitions carry cache_control at the definition level
Anthropic wire order: tools -> system -> messages. Breakpoint placement:
  per_stratum          (PFS): every system stratum + latest message (<=4)
  system_plus_trailing (RC): system + last-2 message pair
  system_tools_trailing(MC): tools(-last) + system + last message
"""

import copy


def _bp():
    return {"type": "ephemeral"}


def _as_blocks(content):
    """Normalise a message body to Anthropic content-block form."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return content


def _block_messages(history: list) -> list:
    """Every message in block form, so prefix comparisons and marker
    placement see a uniform shape across the whole stream. Blocks are copied
    fresh per request: a cache_control marker placed on the latest message
    must not leak into the stored history (and from there into every later
    request) through a shared content-list reference."""
    out = []
    for m in history:
        m2 = {k: v for k, v in m.items() if k != "cache_control"}
        blocks = _as_blocks(m2.get("content", ""))
        m2["content"] = [dict(b) if isinstance(b, dict) else b for b in blocks]
        out.append(m2)
    return out


def _mark_latest(ms):
    """Breakpoint on the last content block of the latest message (any block
    type — cache_control sits on individual content blocks, no type is
    excluded by the documented wire shape)."""
    if ms:
        ms[-1].setdefault("content", _as_blocks(""))
        ms[-1]["content"] = _as_blocks(ms[-1]["content"])
        ms[-1]["content"][-1]["cache_control"] = _bp()


def _mark_last2(ms):
    for m in ms[-2:]:
        m["content"] = _as_blocks(m.get("content", ""))
        if m["content"]:
            m["content"][-1]["cache_control"] = _bp()


def build_request(mech, step: int, frozen_env, current_env, history: list,
                  frozen_tools: list | None, tool_churn: bool = True) -> dict:
    """history: list of {"role", "content"} dicts, append-only."""
    # --- tools -------------------------------------------------------------
    tools = copy.deepcopy(tool_list_(mech, step, frozen_tools, tool_churn))
    # --- system ------------------------------------------------------------
    from .prompts import system_sections, EnvView  # local import avoids cycle
    env = frozen_env if mech.env_mode == "frozen" else current_env
    sections = system_sections(mech, env, mech.env_mode)

    system = []
    for role, text, stratum in sections:
        system.append({"type": "text", "text": text})

    # --- breakpoint placement ----------------------------------------------
    style = mech.breakpoint_style
    if style == "per_stratum":
        for block in system:
            block["cache_control"] = _bp()
        msg_bp = "latest"
    elif style == "system_tools_trailing":
        if system:
            system[-1]["cache_control"] = _bp()
        if tools:
            tools[-1]["cache_control"] = _bp()
        msg_bp = "latest"
    elif style == "system_plus_trailing":
        if system:
            system[0]["cache_control"] = _bp()
        msg_bp = "last2"
    else:
        raise ValueError(style)

    messages = _block_messages(history)

    if msg_bp == "latest":
        _mark_latest(messages)
    else:
        _mark_last2(messages)

    return {"model": current_env.model, "tools": tools, "system": system,
            "messages": messages, "max_tokens": 1024}


def tool_list_(mech, step, frozen_tools, tool_churn: bool = True):
    from .tools import tool_list_for
    return tool_list_for(mech, step, frozen_tools, tool_churn)


def build_compact_request(mech, current_env, history: list, summary_prompt: str,
                           frozen_tools: list | None, tool_churn: bool = True) -> dict:
    """Compaction summary requests per archetype (design dimension D4).

    The retained history after compaction is IDENTICAL for every policy
    ([first user turn] + summary + last two turns) — that retention shape is a
    constant of the harness, not part of D4. D4 varies only how the summary
    REQUEST is constructed:

    cache_aware (PFS): the request carries the archetype's REAL tools and
      system blocks and the full pre-compaction conversation, with the summary
      instruction appended as the final message; a breakpoint on the last
      pre-summary message writes a cache entry covering the whole preserved
      prefix, so subsequent turns read it.
    cache_dead (RC): the conversation is re-serialised into ONE fresh user
      prompt with a fresh summarization system prompt and no tools — nothing
      on this request matches the session's cached prefixes.
    retention_none (MC): prefix-preserving shape with NO cache_control
      markers anywhere on the request — the documented mechanism for "this
      request does not interact with the cache" (reads and writes apply only
      up to marked breakpoints; an unmarked request bypasses both).
    """
    import copy
    style = mech.compaction
    if style == "cache_dead":
        ser = "\n\n".join(
            f"[{m['role']}]\n{_blocks_text(m['content'])}" for m in history
        )
        messages = [{"role": "user", "content": _as_blocks(
            summary_prompt + "\n\nConversation:\n" + ser)}]
        system = [{"type": "text", "text": "You are a summarization engine."}]
        system[0]["cache_control"] = _bp()
        messages[-1]["content"][-1]["cache_control"] = _bp()
        return {"model": current_env.model, "tools": [], "system": system,
                "messages": messages, "max_tokens": 1024}
    # cache_aware / retention_none share prefix-preserving shape
    from .prompts import system_sections
    tools = copy.deepcopy(tool_list_(mech, -1, frozen_tools, tool_churn))
    system = [{"type": "text", "text": text}
              for _role, text, _stratum in system_sections(mech, current_env, mech.env_mode)]
    messages = _block_messages(history)
    messages.append({"role": "user",
                     "content": _as_blocks(summary_prompt)})
    if style == "cache_aware":
        # breakpoint on the last pre-summary message: the written entry covers
        # the preserved prefix (tools + system + conversation), so post-
        # compaction turns read it. The summary instruction itself sits beyond
        # the breakpoint (write premium only on that short tail).
        if system:
            system[0]["cache_control"] = _bp()
        if len(messages) > 1:
            messages[-2]["content"][-1]["cache_control"] = _bp()
    # retention_none: no markers — the request is cache-inert by construction
    return {"model": current_env.model, "tools": tools, "system": system,
            "messages": messages, "max_tokens": 1024}


def _blocks_text(content) -> str:
    """Flat text of a (string or content-block) message body, for re-serialisation.
    tool_use blocks serialise as their call JSON; tool_result blocks as their
    content — a re-serialised conversation must carry the full tool traffic."""
    import json as _json
    if isinstance(content, str):
        return content
    out = []
    for b in content:
        if not isinstance(b, dict):
            out.append(str(b))
        elif "text" in b:
            out.append(b["text"])
        elif b.get("type") == "tool_use":
            out.append(f"[tool call {b.get('name', b.get('tool'))}] "
                       + _json.dumps(b.get("input", {}), sort_keys=True))
        elif b.get("type") == "tool_result":
            out.append(f"[tool result] {b.get('content', '')}")
    return "\n".join(out)


def warmer_request(last_request: dict) -> dict:
    """MC cache warmer (D6): re-send the EXACT last request with max_tokens=1."""
    import copy
    r = copy.deepcopy(last_request)
    r["max_tokens"] = 1
    return r
