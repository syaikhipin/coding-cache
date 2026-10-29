"""Agent loop: drives the toy task, applies mechanisms, journals wire requests.

Two api modes:
  live    — real model calls through the local proxy (plausible tool traffic)
  script  — deterministic scripted agent (no network; identical behavior across
            profiles, which is exactly what a controlled comparison wants)

The scripted agent is the default for ablations: mechanism effects on the wire
stream must not be confounded with model nondeterminism.

Round-3 changes:
  - assistant tool calls are emitted as tool_use blocks and tool results as
    user tool_result blocks (the documented Anthropic tool-use shape) instead
    of flattened text+JSON
  - the journal records {"_record_meta": {...}, "request": <pure request>}
    envelopes: the wire object itself carries ONLY provider-visible fields
  - the scripted turn is task-aware ("bugfix" | "features"), removing the
    harness monkey-patching that sweep.py previously did
"""

import json
import random

from . import api, prompts, recorder, tools as tools_mod, wire
from .config import RunConfig


def _scripted_turn(step: int, cfg: RunConfig, task: str = "bugfix") -> dict:
    """Deterministic tool-call script. bugfix: read README (step 0), then
    read -> run -> edit -> run cycling files; features: read -> run -> edit ->
    run with feature-shaped results; repo2: the second fixture's bugfix
    script (REPO2_TURN_FN). Results embed real fixture content so transcript
    growth (and hence the compaction trigger) mirrors a real session."""
    if task == "repo2":
        from .tasks import REPO2_TURN_FN
        return REPO2_TURN_FN(step)
    from .tasks import TOY_REPO_FILES
    files = ["calc.py", "shapes.py", "textutil.py"]
    if task == "features":
        s = step % len(files)
        f = files[s]
        phase = step % 4
        if phase == 0:
            return {"tool": "read", "input": {"path": f},
                    "result": "1-" + str(TOY_REPO_FILES[f].count("\n")) + "\n"
                              + TOY_REPO_FILES[f]}
        if phase == 1:
            return {"tool": "run_tests", "input": {},
                    "result": "PASSED test_calc.py::test_add\n"
                              "PASSED test_shapes.py::test_circle_area\n"
                              "PASSED test_textutil.py::test_word_count"}
        if phase == 2:
            return {"tool": "edit", "input": {"path": f, "old_str": "a - b",
                                              "new_str": "a + b"},
                    "result": f"extended {f}"}
        return {"tool": "run_tests", "input": {}, "result": "3 passed"}
    # default: bugfix
    if step == 0:
        return {"tool": "read", "input": {"path": "README.md"},
                "result": "1-" + str(TOY_REPO_FILES["README.md"].count("\n")) + "\n"
                          + TOY_REPO_FILES["README.md"]}
    s = step - 1
    phase = s % 4
    f = files[min(s // 4, len(files) - 1)]
    if phase == 0:
        return {"tool": "read", "input": {"path": f},
                "result": "1-" + str(TOY_REPO_FILES[f].count("\n")) + "\n"
                          + TOY_REPO_FILES[f]}
    if phase == 1:
        return {"tool": "run_tests", "input": {},
                "result": "FAILED test_calc.py::test_add\n"
                          "FAILED test_shapes.py::test_circle_area\n"
                          "FAILED test_textutil.py::test_word_count"}
    if phase == 2:
        return {"tool": "edit", "input": {"path": f, "old_str": "a - b", "new_str": "a + b"},
                "result": f"edited {f}"}
    return {"tool": "run_tests", "input": {}, "result": "2 failed, 1 passed"}


def _scripted_text(step: int) -> str:
    return f"Step {step}: I'll inspect the failing module and fix it."


def _tool_call_blocks(text: str, tool_use_id: str, tool: str, tool_input: dict) -> list:
    """Assistant message content: text block + tool_use block (documented
    wire schema: id, name, input)."""
    return [
        {"type": "text", "text": text},
        {"type": "tool_use", "id": tool_use_id, "name": tool, "input": tool_input},
    ]


def run_profile(profile_name: str, mech, cfg: RunConfig, api_mode: str = "script",
                task_prompt: str | None = None, agent_files_hint: str | None = None,
                task: str = "bugfix"):
    from .tasks import TASK_PROMPT, AGENT_FILES_HINT
    task_text = task_prompt or TASK_PROMPT
    hint = agent_files_hint or AGENT_FILES_HINT

    rng = random.Random(cfg.seed)

    # session-start env (frozen snapshot for PFS)
    frozen_env = prompts.EnvView(
        cwd="/workspace/toy-repo",
        model=cfg.model,
        date="2026-09-22",
        git_status="M calc.py",
        platform="macOS-25.2",
    )

    # mutable env (changes at scheduled steps: date rollover + git change).
    # env_change_notes records the changes every archetype observes; how each
    # archetype delivers them is the D3 treatment:
    #   per_step -> recomputed into the env block (at prompt front)
    #   minimal  -> date dropped; git status still recomputed into the tail
    #   frozen   -> snapshot stays; changes appended as messages (append-only)
    def current_env(step: int) -> prompts.EnvView:
        date = frozen_env.date
        git = frozen_env.git_status
        if step >= cfg.env_change_steps[0]:
            date = "2026-09-23"  # day rollover mid-session
        if step >= cfg.env_change_steps[1]:
            git = "M calc.py\nM shapes.py"  # repo state changed
        return prompts.EnvView(frozen_env.cwd, cfg.model, date, git, frozen_env.platform)

    env_change_notes: list[str] = []

    def record_env_change(step: int) -> None:
        note = f"[env change] {current_env(step).snapshot()}"
        if not env_change_notes or env_change_notes[-1] != note:
            env_change_notes.append(note)

    seen_env_note_step = [-1]
    frozen_tools = tools_mod.frozen_tool_list() if mech.tool_order == "frozen" else None
    history = [
        {"role": "user", "content": hint + "\n\n" + task_text},
    ]
    requests: list = []
    responses: list = []
    step = 0
    compacted = False
    last_request = None
    tool_use_counter = 0

    while step < cfg.max_turns:
        # periodic env mutation affects per_step env modes only
        env_now = current_env(step)
        if mech.env_mode == "frozen":
            # append-only delivery: mutate nothing in the prefix; new env info
            # arrives as appended user messages (same information as RC sees)
            for cs in cfg.env_change_steps:
                if step >= cs and seen_env_note_step[0] < cs:
                    seen_env_note_step[0] = cs
                    note = (f"[environment update] date is now "
                            f"{current_env(step).date}."
                            if cs == cfg.env_change_steps[0] else
                            f"[environment update] git status changed: "
                            f"{current_env(step).git_status}")
                    history.append({"role": "user", "content": note})
                    record_env_change(step)

        req = wire.build_request(mech, step, frozen_env, env_now, history,
                                 frozen_tools, tool_churn=cfg.tool_churn)
        requests.append({"_record_meta": {"profile": profile_name, "step": step,
                                          "kind": "turn"},
                         "request": req})
        last_request = req

        # --- get the assistant turn -----------------------------------------
        if api_mode == "live":
            text = _live_turn(req, cfg)
            if text is None:
                # fall back to script for this turn; flag it
                text = _scripted_text(step)
                responses.append({"step": step, "text": text, "fallback": True})
            else:
                responses.append({"step": step, "text": text, "fallback": False})
            turn = _scripted_turn(step, cfg, task)  # tool traffic still scripted for control
        else:
            text = _scripted_text(step)
            responses.append({"step": step, "text": text, "fallback": False})
            turn = _scripted_turn(step, cfg, task)

        # append assistant tool_use + user tool_result (append-only transcript)
        tool_use_counter += 1
        tool_use_id = f"toolu_{tool_use_counter:03d}"
        blocks = _tool_call_blocks(text, tool_use_id, turn["tool"], turn["input"])
        history.append({"role": "assistant", "content": blocks})
        history.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tool_use_id,
             "content": turn["result"]}]})
        step += 1

        # --- compaction trigger (identical token threshold for all profiles) --
        if not compacted and cfg.compact_at_tokens and _approx_tokens(history) >= cfg.compact_at_tokens:
            creq = wire.build_compact_request(
                mech,
                frozen_env if mech.env_mode == "frozen" else env_now,
                history,
                "Summarize the conversation so far, preserving the fixes already made.",
                frozen_tools,
                tool_churn=cfg.tool_churn)
            requests.append({"_record_meta": {"profile": profile_name, "step": step,
                                              "kind": "compact"},
                             "request": creq})
            summary = "Summary: fixed add() in calc.py; remaining: shapes.py, textutil.py."
            # retention shape is IDENTICAL for all compaction policies: the
            # first user turn is kept (stabilises the post-compaction prefix),
            # plus the summary and the last two turns. D4 varies how the
            # summary REQUEST is built (see wire.build_compact_request), not
            # what history is retained afterwards.
            history = [history[0]] + [
                {"role": "user", "content": "[context summary] " + summary}
            ] + history[-2:]
            compacted = True

        # --- TTL cache warmer (D6, MC): fires when TTL near expiry ------------
        if mech.cache_warmer and last_request is not None and step % 3 == 0:
            wreq = wire.warmer_request(last_request)
            requests.append({"_record_meta": {"profile": profile_name, "step": step,
                                              "kind": "warmer"},
                             "request": wreq})

    record = {
        "ts": recorder._now(),
        "profile": profile_name,
        "mechanisms": mech.summary(),
        "task": task,
        "steps": cfg.max_turns,
        "seed": cfg.seed,
        "api_mode": api_mode,
        "model": cfg.model,
        "env_change_steps": list(cfg.env_change_steps),
        "compact_at_tokens": cfg.compact_at_tokens,
        "tool_churn": cfg.tool_churn,
        "env_notes_delivered": list(env_change_notes),
    }
    trace = recorder.write_run(record, requests, responses)
    return {"record": record, "trace": trace, "requests": requests}


def _approx_tokens(history: list) -> int:
    """Approximate wire size of the transcript: every block counts — text by
    its characters, tool_use by its serialised call, tool_result by its
    content — so the compaction trigger tracks the real request volume."""
    import json as _json
    total = 0
    for m in history:
        c = m["content"]
        if isinstance(c, str):
            total += len(c)
            continue
        for b in c:
            if not isinstance(b, dict):
                continue
            if "text" in b:
                total += len(b["text"])
            elif b.get("type") == "tool_use":
                total += len(b.get("name", b.get("tool", ""))) + len(b.get("id", "")) \
                    + len(_json.dumps(b.get("input", {}), sort_keys=True))
            elif b.get("type") == "tool_result":
                total += len(b.get("content", "") if isinstance(b.get("content"), str)
                             else _json.dumps(b.get("content", "")))
    return total // 4
