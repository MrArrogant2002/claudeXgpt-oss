//! Inference client — llama.cpp RAW /completion endpoint. Sends our Harmony
//! token IDs, asks for output token IDs back (return_tokens). No chat template.

use std::time::Duration;

use serde_json::json;

use crate::config;

pub enum InferError {
    /// The rendered prompt exceeded the server's context window (llama.cpp 400).
    Overflow(String),
    Other(String),
}

pub struct Completion {
    pub tokens: Vec<u32>,
    pub truncated: bool, // hit n_predict rather than a natural stop token
}

pub fn health() -> anyhow::Result<()> {
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(10))
        .build()?;
    client.get(config::health_url()).send()?.error_for_status()?;
    Ok(())
}

pub fn complete(prefill: &[u32], max_tokens: u32, temperature: f32) -> Result<Completion, InferError> {
    let body = json!({
        "prompt": prefill,        // array of token IDs — no templating applied
        "n_predict": max_tokens,
        "temperature": temperature,
        "cache_prompt": true,     // reuse KV cache across turns (speed)
        "return_tokens": true,    // include output token IDs in the response
    });

    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(config::request_timeout_secs()))
        .build()
        .map_err(|e| InferError::Other(format!("client build failed: {e}")))?;

    let resp = client
        .post(config::completion_url())
        .json(&body)
        .send()
        .map_err(|e| InferError::Other(format!("POST {} failed: {e}", config::completion_url())))?;

    let status = resp.status();
    if !status.is_success() {
        let text = resp.text().unwrap_or_default();
        let low = text.to_lowercase();
        if status.as_u16() == 400 && (low.contains("context") || low.contains("exceed")) {
            return Err(InferError::Overflow(text));
        }
        let preview: String = text.chars().take(300).collect();
        return Err(InferError::Other(format!("{status}: {preview}")));
    }

    let data: serde_json::Value = resp
        .json()
        .map_err(|e| InferError::Other(format!("non-JSON response: {e}")))?;

    let tokens: Vec<u32> = data
        .get("tokens")
        .and_then(|t| t.as_array())
        .map(|a| a.iter().filter_map(|v| v.as_u64().map(|n| n as u32)).collect())
        .unwrap_or_default();

    if tokens.is_empty() {
        let keys: Vec<String> = data
            .as_object()
            .map(|o| o.keys().cloned().collect())
            .unwrap_or_default();
        return Err(InferError::Other(format!(
            "server returned no output token IDs (return_tokens unsupported?). keys: {keys:?}"
        )));
    }

    let truncated = data
        .get("stopped_limit")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
        || data
            .get("stop_type")
            .and_then(|v| v.as_str())
            .map(|s| s == "limit")
            .unwrap_or(false);

    Ok(Completion { tokens, truncated })
}
