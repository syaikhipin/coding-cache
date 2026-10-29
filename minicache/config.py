"""Architecture presets: audited design patterns as switches.

The study compares METHODS, not products. Three context-assembly archetypes
for coding-agent harnesses, each instantiating five design dimensions
differently:

  PFS  prefix-frozen stratified — env snapshot frozen at session start,
       deterministic tool order frozen at session start, stratified system
       prompt (stable strata before dynamic), prefix-preserving compaction
  RC   recomputed-context       — env block (cwd, date, git) recomputed every
       step and placed at the prompt front, tools re-resolved and sorted per
       step, monolithic system prompt, prefix-breaking compaction
  MC   minimal-context          — minimal env block (no date), tools
       re-resolved per step, monolithic system prompt, cache-disabled
       compaction, background TTL cache warmer
"""

from dataclasses import dataclass, field

# Design-dimension switches ---------------------------------------------------


@dataclass
class Mechanisms:
    # D1: system-prompt structure
    # "stratified3": [cli_prefix, identity, dynamic_body] as separate blocks (PFS)
    # "single": one merged system block (RC, MC)
    system_stratification: str = "single"

    # D2: cache-breakpoint placement
    # "per_stratum": one per system stratum + trailing message, <=4 (PFS)
    # "system_plus_trailing": 1 on system + 1 on trailing messages (RC)
    # "system_tools_trailing": system + tools + trailing (MC)
    breakpoint_style: str = "system_plus_trailing"

    # D3: context freshness
    # "frozen": snapshot once at session start (PFS)
    # "per_step": recompute env block (cwd, date, git) every step, at FRONT (RC)
    # "minimal": cwd only, no date (MC)
    env_mode: str = "per_step"

    # D4: compaction cache policy
    # "cache_aware": summary request reuses the conversation prefix (PFS)
    # "cache_dead": conversation serialized into one fresh prompt (RC)
    # "retention_none": summary requests explicitly cache-disabled (MC)
    compaction: str = "cache_dead"

    # D5: tool-list stability
    # "frozen": deterministic order frozen at session start (PFS)
    # "per_step_sorted": re-resolved and sorted each step (RC, MC)
    tool_order: str = "per_step_sorted"

    # D6: TTL maintenance
    cache_warmer: bool = False

    # measurement control (kept off all presets; used in ablations)
    inject_env_change_at_step: int | None = None  # step index where env changes
    compact_at_tokens: int | None = None  # trigger compaction threshold

    def summary(self) -> dict:
        return {
            "system_stratification": self.system_stratification,
            "breakpoint_style": self.breakpoint_style,
            "env_mode": self.env_mode,
            "compaction": self.compaction,
            "tool_order": self.tool_order,
            "cache_warmer": self.cache_warmer,
            "inject_env_change_at_step": self.inject_env_change_at_step,
            "compact_at_tokens": self.compact_at_tokens,
        }


# Archetype instantiations -----------------------------------------------------


def pfs_mechanisms() -> Mechanisms:
    """Prefix-frozen stratified: maximal prefix stability by construction."""
    return Mechanisms(
        system_stratification="stratified3",
        breakpoint_style="per_stratum",
        env_mode="frozen",
        compaction="cache_aware",
        tool_order="frozen",
        cache_warmer=False,
    )


def rc_mechanisms() -> Mechanisms:
    """Recomputed-context: fresh context every step, at the prompt front."""
    return Mechanisms(
        system_stratification="single",
        breakpoint_style="system_plus_trailing",
        env_mode="per_step",
        compaction="cache_dead",
        tool_order="per_step_sorted",
        cache_warmer=False,
    )


def mc_mechanisms() -> Mechanisms:
    """Minimal-context: smallest mutable surface, plus a TTL cache warmer."""
    return Mechanisms(
        system_stratification="single",
        breakpoint_style="system_tools_trailing",
        env_mode="minimal",
        compaction="retention_none",
        tool_order="per_step_sorted",
        cache_warmer=True,
    )


PROFILES = {
    "pfs": pfs_mechanisms,
    "rc": rc_mechanisms,
    "mc": mc_mechanisms,
}


@dataclass
class RunConfig:
    model: str = "dh/dahono/deepseek-v4.1-flash"
    base_url: str = "http://127.0.0.1:20128/v1"
    max_turns: int = 12
    # env mutation schedule: steps at which the environment legitimately changes
    # (day rollover, git-status change) — identical across archetypes
    env_change_steps: tuple = (4, 8)
    # compaction trigger (approx tokens of transcript): fires mid-session at the
    # SAME step for every archetype because the scripted transcript is identical
    compact_at_tokens: int = 12400
    # tool-availability churn (glob unavailable steps 3-5 under per-step
    # resolution): a sweep-level control, independent of the env schedule
    tool_churn: bool = True
    seed: int = 0
    api_key_env: str = "ANTHROPIC_AUTH_TOKEN"
