//! Configuration — all values overridable via environment variables (same knobs
//! as the Python agent, so behaviour matches).

use std::env;

fn env_or<T: std::str::FromStr>(key: &str, default: T) -> T {
    env::var(key).ok().and_then(|s| s.parse().ok()).unwrap_or(default)
}

pub fn base_url() -> String {
    env::var("AGENT_BASE_URL")
        .unwrap_or_else(|_| "http://localhost:8081".to_string())
        .trim_end_matches('/')
        .to_string()
}

pub fn completion_url() -> String {
    format!("{}/completion", base_url())
}

pub fn health_url() -> String {
    format!("{}/health", base_url())
}

pub fn reasoning() -> String {
    env::var("AGENT_REASONING").unwrap_or_else(|_| "medium".to_string())
}

pub fn max_tokens() -> u32 {
    env_or("AGENT_MAX_TOKENS", 4096)
}

pub fn temperature() -> f32 {
    env_or("AGENT_TEMPERATURE", 0.7)
}

pub fn max_turns() -> u32 {
    env_or("AGENT_MAX_TURNS", 12)
}

pub fn tool_result_cap() -> usize {
    env_or("AGENT_TOOL_RESULT_CAP", 12_000)
}

pub fn read_default_lines() -> usize {
    env_or("AGENT_READ_DEFAULT_LINES", 300)
}

pub fn request_timeout_secs() -> u64 {
    env_or("AGENT_TIMEOUT", 600)
}
