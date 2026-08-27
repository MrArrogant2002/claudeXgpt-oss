//! Local code agent (Rust / Stack A) — gpt-oss brain via llama.cpp, Harmony
//! rendered here. Same behaviour as the Python agent, as a single binary.
//!
//!   codeagent --project ../requests "where is hooks.py and what does it do?"
//!   codeagent --project ../requests            # interactive REPL
//!
//! Final answer -> stdout; tool-call trace + reasoning -> stderr.

mod agent_loop;
mod config;
mod context;
mod harmony;
mod inference;
mod sandbox;
mod tools;

use std::io::{self, Write};

use openai_harmony::chat::Message;

use harmony::Codec;
use sandbox::Sandbox;

fn run_and_print(
    codec: &Codec,
    registry: &tools::Registry,
    sandbox: &Sandbox,
    reasoning: &str,
    history: &mut Vec<Message>,
    quiet: bool,
    show_reasoning: bool,
    question: &str,
) {
    let mut on_event = |kind: &str, recipient: Option<&str>, content: &str| {
        let preview: String = content.chars().take(160).collect();
        match kind {
            "tool" => {
                if !quiet {
                    eprintln!("  [tool result] {}: {:?}", recipient.unwrap_or(""), preview);
                }
            }
            "commentary" => {
                if !quiet {
                    if let Some(r) = recipient {
                        eprintln!("  [tool call]   {r} {preview}");
                    }
                }
            }
            "analysis" => {
                if show_reasoning {
                    eprintln!("  [reasoning]   {content}");
                }
            }
            "system" => {
                if !quiet {
                    eprintln!("  {content}");
                }
            }
            _ => {}
        }
    };

    let out = agent_loop::run_turn(codec, question, history, registry, sandbox, reasoning, &mut on_event);
    match out.reason.as_str() {
        "completed" => {
            if out.answer.is_empty() {
                println!("(model returned an empty final answer)");
            } else {
                println!("{}", out.answer);
            }
        }
        "no_answer" => println!(
            "[no answer] The model kept returning an empty final even after being nudged. \
             Try --reasoning high, raise AGENT_MAX_TOKENS, or ask a more specific question."
        ),
        "max_turns" => println!(
            "[stopped: hit max turns] The model kept calling tools without concluding. \
             Raise AGENT_MAX_TURNS or narrow the question."
        ),
        other => println!("[stopped: {other}] {}", out.answer),
    }
}

fn main() {
    // --- parse args ---
    let mut project = ".".to_string();
    let mut reasoning = config::reasoning();
    let mut show_reasoning = false;
    let mut quiet = false;
    let mut question_parts: Vec<String> = Vec::new();

    let mut it = std::env::args().skip(1);
    while let Some(a) = it.next() {
        match a.as_str() {
            "--project" => {
                if let Some(v) = it.next() {
                    project = v;
                }
            }
            "--reasoning" => {
                if let Some(v) = it.next() {
                    reasoning = v;
                }
            }
            "--show-reasoning" => show_reasoning = true,
            "--quiet" => quiet = true,
            "-h" | "--help" => {
                eprintln!("usage: codeagent [--project DIR] [--reasoning low|medium|high] [--show-reasoning] [--quiet] [QUESTION...]");
                return;
            }
            _ => question_parts.push(a),
        }
    }

    // --- preflight ---
    if let Err(e) = inference::health() {
        eprintln!("[error] cannot reach llama.cpp at {}: {e}", config::base_url());
        eprintln!("Start: llama-server -m gpt-oss-20b.gguf -c 32768 --port 8081 -ngl 999");
        std::process::exit(1);
    }

    let sandbox = match Sandbox::new(&project) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("[error] bad --project: {e}");
            std::process::exit(1);
        }
    };
    let codec = match Codec::new() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[error] harmony init failed: {e}");
            std::process::exit(1);
        }
    };
    let registry = tools::default_registry();
    eprintln!("[project] {}", sandbox.root.display());

    let mut history: Vec<Message> = Vec::new();

    // --- one-shot ---
    if !question_parts.is_empty() {
        let q = question_parts.join(" ");
        run_and_print(&codec, &registry, &sandbox, &reasoning, &mut history, quiet, show_reasoning, &q);
        return;
    }

    // --- interactive REPL ---
    eprintln!("Local code agent — type a question ('exit' to quit).");
    loop {
        eprint!("\n> ");
        let _ = io::stderr().flush();
        let mut line = String::new();
        if io::stdin().read_line(&mut line).unwrap_or(0) == 0 {
            break;
        }
        let q = line.trim();
        if q == "exit" || q == "quit" {
            break;
        }
        if !q.is_empty() {
            run_and_print(&codec, &registry, &sandbox, &reasoning, &mut history, quiet, show_reasoning, q);
        }
    }
}
