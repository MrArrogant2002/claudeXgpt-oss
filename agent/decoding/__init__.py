"""Decoding strategies (build plan, Phase 4).

The experimental arms of the paper's first contribution live here. A strategy
owns the question of *where a grammar applies* during a completion:

  unconstrained   no constraint at any point (the baseline)
  global_schema   one schema over the whole completion (the reported-costly arm)
  cscd_i          channel-scoped, canonical header injected by the orchestrator
  cscd_g          channel-scoped, header produced under a grammar (ablation)
"""

from .tokens import KNOWN_CHANNELS, SpecialTokens
from .recognizer import Event, HarmonyRecognizer, Region
from .client import DecodeResult, LlamaCppClient, PhaseRecord, ReplayClient
from .decoder import Decoder, build_decoder
from .strategies import DecodingStrategy, build

__all__ = [
    "KNOWN_CHANNELS",
    "SpecialTokens",
    "Event",
    "HarmonyRecognizer",
    "Region",
    "DecodeResult",
    "PhaseRecord",
    "LlamaCppClient",
    "ReplayClient",
    "Decoder",
    "build_decoder",
    "DecodingStrategy",
    "build",
]
