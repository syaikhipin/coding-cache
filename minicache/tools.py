"""Tools + tool-list stability modes (design dimension D5).

frozen (PFS): visible tool list cached, fixed reference order (known tools
alphabetized, then locals in registration order) — frozen at session start.

per_step_sorted (RC, MC): tools re-resolved per step from live state and
emitted sorted. We model churn: on specified steps the *resolved* set
legitimately changes (a tool gained/lost permission), which under per-step
mode reorders or mutates the serialized tool list — a prefix event — while
frozen mode keeps the list byte-identical.
"""

import json

TOOLS = {
    "read": {
        "name": "read",
        "description": "Read a file from the repository. Input: {\"path\": str}\n"
                       + ("Usage: returns the file contents with line numbers. "
                          "For long files read in slices; do not re-read a file "
                          "you have already seen unless it changed on disk. "),
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}},
                          "required": ["path"]},
    },
    "run_tests": {
        "name": "run_tests",
        "description": "Run the repository test suite, returns pass/fail lines. No input.\n"
                       + ("Usage: runs the full suite; output lines are FAILED/PASSED per "
                          "test. Run after every edit; fix one module at a time and "
                          "confirm with a re-run before moving on. "),
        "input_schema": {"type": "object", "properties": {}},
    },
    "edit": {
        "name": "edit",
        "description": "Replace exactly one occurrence of old_str with new_str in a file. "
                       'Input: {"path": str, "old_str": str, "new_str": str}\n'
                       + ("Usage: the match must be unique in the file or the call fails. "
                          "Read the file first, pick a minimal unique snippet, and prefer "
                          "the smallest possible replacement that fixes the bug. "),
        "input_schema": {"type": "object",
                         "properties": {"path": {"type": "string"},
                                         "old_str": {"type": "string"},
                                         "new_str": {"type": "string"}},
                         "required": ["path", "old_str", "new_str"]},
    },
    "bash": {
        "name": "bash",
        "description": "Run a short read-only shell command for inspection. Input: {\"cmd\": str}\n"
                       + ("Usage: for listing files, checking git state, grepping sources. "
                          "Commands run in the repository root with a short timeout; "
                          "mutating commands are rejected. "),
        "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}},
    },
    "glob": {
        "name": "glob",
        "description": "List files matching a glob pattern. Input: {\"pattern\": str}\n"
                       + ("Usage: patterns use shell-style wildcards; results are sorted "
                          "paths relative to the repository root. Use to discover test "
                          "files and module layout before reading. "),
        "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}}},
    },
}


def frozen_tool_list() -> list:
    """PFS: deterministic reference order, frozen for the whole session."""
    return [TOOLS["edit"], TOOLS["glob"], TOOLS["read"], TOOLS["run_tests"], TOOLS["bash"]]


def re_resolved_tool_list(step: int, tool_churn: bool = True) -> list:
    """RC/MC-style: re-resolved per step. Sorted for stability, but the
    resolved SET can change with live permission state. We model a small churn:
    'glob' becomes unavailable at step>=3, returns at step>=6 (e.g. an MCP
    server disconnect/reconnect). Sorted output means churn removes/inserts an
    element into the serialized tools array — prefix-affecting when tools are
    serialized before the system prompt (Anthropic wire order).

    tool_churn=False disables the availability change entirely (the sweep's
    true no-churn condition): the resolved list is then stable byte-identical
    across steps, isolating env churn from tool churn.
    """
    names = sorted(TOOLS)
    if tool_churn and 3 <= step < 6:
        names = [n for n in names if n != "glob"]
    return [TOOLS[n] for n in names]


def tool_list_for(mech, step: int, frozen_state: list | None,
                  tool_churn: bool = True) -> list:
    if mech.tool_order == "frozen":
        return frozen_state if frozen_state is not None else frozen_tool_list()
    return re_resolved_tool_list(step, tool_churn)
