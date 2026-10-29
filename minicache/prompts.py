"""System-prompt sections with stable/dynamic strata, per archetype.

PFS: three system blocks — cli_prefix (stable), identity (stable), dynamic
body (env/date/git) — each stamped with a cache breakpoint, stable-before-
dynamic ordering enforced; the env text is a snapshot labeled as frozen.

RC: ONE system string; env block (cwd, date, model id, platform) recomputed
per step and placed at the FRONT, before instructions — any env change
mutates the head of the prompt.

MC: one system block; sections include cwd but NO date (smallest mutable
surface short of freezing).
"""

CLI_PREFIX = (
    "You are MiniAgent, a terminal coding assistant.\n"
    "Respond concisely. Use the provided tools to inspect and fix code.\n\n"
    ## General operating guidelines (stable prefix volume, as in real CLI
    ## harnesses whose system+tool prompt runs 10-20k tokens).
    + ("Guideline: state the goal of the step before acting, then verify the "
       "effect of the action afterwards. Prefer the least invasive change that "
       "achieves the step's goal. When two approaches tie, pick the one that "
       "leaves the repository in the more readable state. Announce test runs "
       "before launching them and summarise their outcome afterwards. Keep "
       "track of which modules have already been fixed and which remain. "
       "Never revert an earlier fix while working on a later module. If a fix "
       "turns out to break an unrelated test, undo it immediately and record "
       "what you learned. Communicate progress in one short sentence per step. "
       "Treat the repository's README as authoritative for conventions.\n\n" * 22)
)

IDENTITY = (
    "## Identity\n\n"
    "You are a careful software engineer. Prefer minimal, targeted fixes.\n"
    "Run the tests after each fix to confirm behavior.\n"
    "Never invent file contents you have not read.\n"
)

INSTRUCTIONS = (
    "## Workflow\n\n"
    "1. Run the failing tests to see failures.\n"
    "2. Read the module under test.\n"
    "3. Apply the minimal fix.\n"
    "4. Re-run the tests to confirm.\n"
    "5. Proceed to the next module.\n\n"
    "Stop when all tests pass and summarize the fixes.\n"
)


class EnvView:
    """Mutable environment state the harness may or may not freeze."""

    def __init__(self, cwd: str, model: str, date: str, git_status: str, platform: str):
        self.cwd = cwd
        self.model = model
        self.date = date
        self.git_status = git_status
        self.platform = platform

    def snapshot(self) -> dict:
        return dict(cwd=self.cwd, model=self.model, date=self.date,
                    git_status=self.git_status, platform=self.platform)


def env_block_rc(env: EnvView) -> str:
    """RC-style: env at the FRONT of the single system string, incl. date."""
    return (
        "## Environment\n\n"
        f"- Working directory: {env.cwd}\n"
        f"- Platform: {env.platform}\n"
        f"- Model: {env.model}\n"
        f"- Today's date: {env.date}\n"
        f"- Git status: {env.git_status}\n"
        "\n"
    )


def env_block_pfs(env: EnvView) -> str:
    """PFS-style: env snapshot inside the DYNAMIC stratum (after stable ones);
    frozen at session start and labeled as such."""
    return (
        "## Environment (snapshot at session start; will not update during the "
        "conversation)\n\n"
        f"- Working directory: {env.cwd}\n"
        f"- Platform: {env.platform}\n"
        f"- Model: {env.model}\n"
        f"- Date: {env.date}\n"
        f"- Git status: {env.git_status}\n"
        "\n"
    )


def env_block_mc(env: EnvView) -> str:
    """MC-style: cwd present, NO date (date is the highest-frequency churn)."""
    return (
        f"Working directory: {env.cwd}\n"
        f"Platform: {env.platform}\n"
        f"Model: {env.model}\n"
        f"Git status: {env.git_status}\n"
        "\n"
    )


def system_sections(mech, env: EnvView, env_mode: str) -> list:
    """Return list of (role='system', text, stratum) sections for a step.

    env_mode at call time:
      frozen       -> use the session-start env view (passed in already frozen)
      per_step     -> recompute env text from the CURRENT env each step
      minimal      -> minimal env block from current env (no date)
    stratification decides packing: 3 blocks vs 1 merged string.
    """
    if env_mode == "frozen":
        env_text = env_block_pfs(env)
    elif env_mode == "minimal":
        env_text = env_block_mc(env)
    else:
        env_text = env_block_rc(env)

    if mech.system_stratification == "stratified3":
        # PFS: cli_prefix (stable) / identity+instructions (stable) / dynamic body
        return [
            ("system", CLI_PREFIX, "stable"),
            ("system", IDENTITY + "\n" + INSTRUCTIONS, "stable"),
            ("system", env_text, "dynamic"),
        ]
    # single-block archetypes (RC, MC): the SAME stable instruction volume as
    # PFS's cli_prefix+identity strata — stability and stratification must not
    # be confounded with prompt volume (a sub-1024-token stable front would be
    # uncacheable on its own, penalising the archetype rather than testing it)
    body = CLI_PREFIX + "\n" + IDENTITY + "\n" + INSTRUCTIONS
    if env_mode == "per_step":
        # RC: env FIRST, then instructions — env churn poisons the head
        return [("system", env_text + body, "single")]
    # MC / others: instructions first, env tail
    return [("system", body + "\n" + env_text, "single")]
