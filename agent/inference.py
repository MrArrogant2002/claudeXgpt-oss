"""Inference client (M0) — talks to llama.cpp's RAW /completion endpoint.

We send our own Harmony-rendered token IDs and ask for the output token IDs
back (return_tokens), so the Harmony codec can parse channels itself. No chat
template, no OpenAI schema in the way.
"""

import requests

from . import config


class InferenceError(RuntimeError):
    pass


def health() -> dict:
    r = requests.get(config.HEALTH_URL, timeout=10)
    r.raise_for_status()
    try:
        return r.json()
    except ValueError:
        return {"status": r.text.strip()}


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
    return tokens, data
