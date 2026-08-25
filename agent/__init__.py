"""Local code agent — gpt-oss brain via llama.cpp, Harmony rendered by hand.

Package layout (built milestone by milestone; see build-plan.md):
  config.py         constants + env overrides
  harmony_codec.py  render conversation -> token IDs; parse tokens -> channels   (M1)
  inference.py      llama.cpp raw /completion client (token IDs in/out)          (M0)
  sandbox.py        path sandbox + permission gate                               (M2)  [added later]
  context.py        result budgeting, drop stale CoT                             (M4)  [added later]
  tools/            glob / grep / read + registry                               (M2-M3)[added later]
  loop.py           orchestration loop                                          (M0-M4)[added later]
"""

__version__ = "0.1.0"
