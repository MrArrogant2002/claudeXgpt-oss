//! Grep tool — regex content search, in-process, respecting .gitignore.
//! (The `regex` crate has no catastrophic backtracking, so no rg needed.)

use std::io::{BufRead, BufReader};

use regex::RegexBuilder;
use serde_json::{json, Value};

use super::Tool;
use crate::sandbox::Sandbox;

pub struct GrepTool;

impl Tool for GrepTool {
    fn name(&self) -> &str {
        "grep"
    }

    fn description(&self) -> &str {
        "Search file contents with a regular expression. Returns matching lines as \
         'path:line: text'. Use to find where a symbol, function, or string lives."
    }

    fn parameters(&self) -> Value {
        json!({
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex to search for"},
                "path": {"type": "string", "description": "Dir or file to search (default: project root)"},
                "glob": {"type": "string", "description": "Restrict to files matching this glob (optional)"},
                "ignore_case": {"type": "boolean", "description": "Case-insensitive search (optional)"},
                "max_matches": {"type": "integer", "description": "Max matches to return (default 100)"}
            },
            "required": ["pattern"]
        })
    }

    fn run(&self, args: &Value, sandbox: &Sandbox) -> String {
        let pattern = match args.get("pattern").and_then(|v| v.as_str()) {
            Some(p) => p,
            None => return "ERROR: missing 'pattern'".to_string(),
        };
        let ignore_case = args.get("ignore_case").and_then(|v| v.as_bool()).unwrap_or(false);
        let max_matches = args.get("max_matches").and_then(|v| v.as_u64()).unwrap_or(100) as usize;

        let re = match RegexBuilder::new(pattern).case_insensitive(ignore_case).build() {
            Ok(r) => r,
            Err(e) => return format!("ERROR: bad regex: {e}"),
        };

        let glob_matcher = args
            .get("glob")
            .and_then(|v| v.as_str())
            .and_then(|g| globset::Glob::new(g).ok())
            .map(|g| g.compile_matcher());

        let base = match args.get("path").and_then(|v| v.as_str()) {
            Some(p) if !p.is_empty() => match sandbox.resolve(p) {
                Ok(x) => x,
                Err(e) => return format!("ERROR: {e}"),
            },
            _ => sandbox.root.clone(),
        };

        let mut out: Vec<String> = Vec::new();
        for dent in ignore::Walk::new(&base).flatten() {
            if !dent.file_type().map(|t| t.is_file()).unwrap_or(false) {
                continue;
            }
            let path = dent.path();
            if let Some(gm) = &glob_matcher {
                match path.strip_prefix(&sandbox.root) {
                    Ok(rel) if gm.is_match(rel) => {}
                    _ => continue,
                }
            }
            let file = match std::fs::File::open(path) {
                Ok(f) => f,
                Err(_) => continue,
            };
            let rel = sandbox
                .relativize(path)
                .map(|p| p.display().to_string().replace('\\', "/"))
                .unwrap_or_else(|_| path.display().to_string());
            for (i, line) in BufReader::new(file).lines().enumerate() {
                let line = match line {
                    Ok(l) => l,
                    Err(_) => break, // non-UTF8 file — skip the rest
                };
                if re.is_match(&line) {
                    out.push(format!("{}:{}: {}", rel, i + 1, line));
                    if out.len() >= max_matches {
                        return out.join("\n");
                    }
                }
            }
        }
        if out.is_empty() {
            "(no matches)".to_string()
        } else {
            out.join("\n")
        }
    }
}
