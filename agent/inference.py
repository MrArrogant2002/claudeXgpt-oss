"""Inference client (M0) — talks to llama.cpp's RAW /completion endpoint.

We send our own Harmony-rendered token IDs and ask for the output token IDs
back (return_tokens), so the Harmony codec can parse channels itself. No chat
template, no OpenAI schema in the way.
"""

import requests

from . import config


class InferenceError(RuntimeError):
    pass


class ContextOverflowError(InferenceError):
    """The rendered prompt exceeded the server's context window (llama.cpp 400)."""


# Cumulative token usage across every /completion call in this process.
#   prompt      = total prompt tokens SENT (the growing conversation, re-sent each call)
#   prompt_new  = prompt tokens actually EVALUATED (after KV-cache reuse) — the real compute
#   output      = tokens the model GENERATED (reasoning + tool calls + final)
USAGE = {"prompt": 0, "prompt_new": 0, "output": 0, "calls": 0}


def usage_snapshot() -> dict:
    return dict(USAGE)


def reset_usage() -> None:
    for k in USAGE:
        USAGE[k] = 0


def health() -> dict:
    r = requests.get(config.HEALTH_URL, timeout=10)
    r.raise_for_status()
    try:
        return r.json()
    except ValueError:
        return {"status": r.text.strip()}


def context_size():
    """Best-effort read of the server's context window (n_ctx) from /props.
    Returns an int, or None if it can't be determined (caller falls back to config)."""
    try:
        r = requests.get(config.PROPS_URL, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None
    candidates = [
        (data.get("default_generation_settings") or {}).get("n_ctx"),
        data.get("n_ctx"),
        (
            (data.get("model") or {}).get("n_ctx")
            if isinstance(data.get("model"), dict)
            else None
        ),
    ]
    for v in candidates:
        if isinstance(v, int) and v > 0:
            return v
    return None


def hit_output_limit(data) -> bool:
    """True if generation stopped because it hit n_predict (was cut off), rather
    than reaching a natural stop token. Field names vary across llama.cpp builds."""
    if data.get("stopped_limit") is True:
        return True
    if data.get("stop_type") == "limit":
        return True
    return False


def complete(
    prefill_ids, stop_ids=None, max_tokens=None, temperature=None, cache_prompt=True
):
    """Send token IDs, get (output_token_ids, raw_response_dict).

    Relies on llama.cpp's `return_tokens` to include output token IDs in the
    response. If your build doesn't support it, this raises a clear error so we
    know to adapt (that's exactly what the M0 smoke test is checking).
    """
    body = {
        "prompt": prefill_ids,  # array of token IDs — no templating applied
        "n_predict": config.MAX_TOKENS if max_tokens is None else max_tokens,
        "temperature": config.TEMPERATURE if temperature is None else temperature,
        "cache_prompt": cache_prompt,  # reuse KV cache across turns (speed)
        "return_tokens": True,  # <-- include output token IDs in the response
    }
    try:
        r = requests.post(
            config.COMPLETION_URL, json=body, timeout=config.REQUEST_TIMEOUT
        )
        r.raise_for_status()
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        body_text = ""
        try:
            body_text = e.response.text if e.response is not None else ""
        except Exception:
            body_text = ""
        low = body_text.lower()
        if status == 400 and ("context" in low or "exceed" in low):
            raise ContextOverflowError(body_text.strip() or str(e)) from e
        raise InferenceError(
            f"POST {config.COMPLETION_URL} failed: {status} {body_text[:300]}"
        ) from e
    except requests.RequestException as e:
        raise InferenceError(f"POST {config.COMPLETION_URL} failed: {e}") from e

    try:
        data = r.json()
    except ValueError as e:
        raise InferenceError(f"non-JSON response from server: {e}") from e

    tokens = data.get("tokens")
    if not tokens:
        raise InferenceError(
            "Server returned no output token IDs. Your llama.cpp build may not "
            "support 'return_tokens' on /completion. Response keys: "
            + ", ".join(sorted(data.keys()))
            + ". (If it only exposes text, we'll adapt the codec to tokenize the "
            "returned text instead.)"
        )

    # Record token usage. prompt = what we sent; prompt_new = what the server
    # actually evaluated (KV cache means most of a warm prompt isn't recomputed).
    evaluated = data.get("tokens_evaluated")
    if not isinstance(evaluated, int):
        evaluated = (data.get("timings") or {}).get("prompt_n")
    if not isinstance(evaluated, int):
        evaluated = len(prefill_ids)
    USAGE["prompt"] += len(prefill_ids)
    USAGE["prompt_new"] += evaluated
    USAGE["output"] += len(tokens)
    USAGE["calls"] += 1

    return tokens, data
