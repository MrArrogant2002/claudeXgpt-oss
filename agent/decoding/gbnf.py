"""Grammars and schemas for constrained decoding (build plan, Phase 4).

Two constraint shapes are needed:

* a **tool-name grammar** for CSCD Phase B — a bare alternation over the names in
  the registry. The model has already chosen a tool semantically; this governs
  only its spelling, which is what makes an unknown or misspelled recipient
  unreachable.
* a **per-recipient JSON schema** for CSCD Phase C — the schema of the one tool
  being invoked. This is strictly tighter than any whole-completion schema, which
  must admit the union of every tool's parameters and therefore cannot reject an
  argument object belonging to a different tool.

`union_schema` builds that weaker whole-completion schema on purpose: it is the
`global_schema` experimental arm, included to reproduce the costs reported in the
literature rather than to be used in production.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

#: Characters that must not appear unescaped in a GBNF string literal.
_GBNF_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _gbnf_literal(text: str) -> str:
    out = "".join(_GBNF_ESCAPES.get(ch, ch) for ch in text)
    return f'"{out}"'


def tool_name_grammar(names: Iterable[str]) -> str:
    """GBNF accepting exactly one of `names`.

    Raises ValueError on an empty registry: constraining to nothing would make
    every token illegal and hang the decode, which is a failure mode worth
    refusing loudly rather than discovering on the GPU box.
    """
    ordered = sorted({n for n in names if n})
    if not ordered:
        raise ValueError("cannot build a tool-name grammar from an empty registry")
    alternatives = " | ".join(_gbnf_literal(n) for n in ordered)
    return f"root ::= {alternatives}\n"


def schema_for_tool(tool: Any) -> dict[str, Any]:
    """The JSON schema of one tool, tightened for constrained decoding.

    `additionalProperties: false` is forced. Without it a schema-constrained
    decode still admits invented parameter names, which is one of the failure
    modes the method claims to eliminate.
    """
    params = getattr(tool, "parameters", None) or {"type": "object", "properties": {}}
    schema = json.loads(json.dumps(params))  # deep copy; never mutate the registry
    if schema.get("type") == "object":
        schema.setdefault("properties", {})
        schema["additionalProperties"] = False
    return schema


def union_schema(tools: Iterable[Any]) -> dict[str, Any]:
    """A whole-completion schema admitting any registered tool's arguments.

    This is the `global_schema` arm. It is deliberately weaker than
    `schema_for_tool`: because it must accept every tool's parameters, it cannot
    reject `{"query": ...}` supplied to a tool whose parameter is `pattern`.
    """
    branches = [schema_for_tool(t) for t in tools]
    if not branches:
        raise ValueError("cannot build a union schema from an empty registry")
    if len(branches) == 1:
        return branches[0]
    return {"anyOf": branches}


def validate_against(schema: Mapping[str, Any], obj: Any) -> list[str]:
    """Minimal structural check used to *score* outputs, not to constrain them.

    Deliberately dependency-free and deliberately shallow: it reports the
    violations the paper counts (wrong type, missing required key, unknown key)
    without pulling in a validator whose version would become yet another thing
    the run manifest has to pin. Returns a list of human-readable problems.
    """
    problems: list[str] = []
    if "anyOf" in schema:
        for branch in schema["anyOf"]:
            if not validate_against(branch, obj):
                return []
        return ["matches no branch of anyOf"]

    expected = schema.get("type")
    if expected == "object":
        if not isinstance(obj, dict):
            return [f"expected object, got {type(obj).__name__}"]
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in obj:
                problems.append(f"missing required key {key!r}")
        if schema.get("additionalProperties") is False:
            for key in obj:
                if key not in props:
                    problems.append(f"unknown key {key!r}")
        for key, value in obj.items():
            sub = props.get(key)
            if isinstance(sub, Mapping):
                problems.extend(
                    f"{key}: {p}" for p in validate_against(sub, value)
                )
        return problems

    simple = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
    }
    if expected in simple:
        # bool is a subclass of int in Python; do not let True satisfy "integer".
        if expected in ("integer", "number") and isinstance(obj, bool):
            problems.append(f"expected {expected}, got boolean")
        elif not isinstance(obj, simple[expected]):
            problems.append(f"expected {expected}, got {type(obj).__name__}")
    return problems
