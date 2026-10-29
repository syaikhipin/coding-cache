"""minicache: a miniature coding-agent harness for prompt-cache journaling.

Instantiates three context-assembly archetypes (PFS prefix-frozen stratified,
RC recomputed-context, MC minimal-context) as independently toggleable design
dimensions, replays them through provider cache models (Anthropic explicit
breakpoints / OpenAI automatic prefix), and journals every wire request so
runs are comparable, reproducible, and auditable.
"""

__version__ = "0.1.0"
