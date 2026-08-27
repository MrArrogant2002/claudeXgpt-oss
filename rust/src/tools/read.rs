//! Read tool — pull specific file lines. Accepts param aliases
//! (start_line/line_start/start, end_line/line_end/end) and caps a bare read to
//! a default window with a pagination hint, so it can't pull a whole 900-line file.

use serde_json::{json, Value};

use super::Tool;
use crate::config;
use crate::sandbox::Sandbox;

pub struct ReadTool;

fn pick_usize(args: &Value, names: &[&str]) -> Option<usize> {
    for k in names {
        if let Some(v) = args.get(*k) {
            if let Some(n) = v.as_u64() {
                return Some(n as usize);
            }
            if let Some(s) = v.as_str() {
                if let Ok(n) = s.parse::<usize>() {
                    return Some(n);
                }
            }
        }
    }
    None
}

impl Tool for ReadTool {
    fn name(&self) -> &str {
        "read"
    }

    fn description(&self) -> &str {
        "Read a file's contents, optionally a line range (start_line, end_line, both \
         1-indexed). Returns numbered lines. If you omit end_line it returns a capped \
         window and tells you how to page to the next chunk. Use after glob/grep locate \
         the file worth reading."
    }

    fn parameters(&self) -> Value {
        json!({
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to the project root"},
                "start_line": {"type": "integer", "description": "1-indexed start line (optional, default 1)"},
                "end_line": {"type": "integer", "description": "1-indexed end line (optional)"}
            },
            "required": ["path"]
        })
    }

    fn run(&self, args: &Value, sandbox: &Sandbox) -> String {
        let path = match args.get("path").and_then(|v| v.as_str()) {
            Some(p) => p,
            None => return "ERROR: missing 'path'".to_string(),
        };
        let resolved = match sandbox.resolve(path) {
            Ok(p) => p,
            Err(e) => return format!("ERROR: {e}"),
        };
        let text = match std::fs::read_to_string(&resolved) {
            Ok(t) => t,
            Err(e) => return format!("ERROR: {e}"),
        };

        let lines: Vec<&str> = text.lines().collect();
        let n = lines.len();
        let start = pick_usize(args, &["start_line", "line_start", "start"]).unwrap_or(1).max(1);
        let end = match pick_usize(args, &["end_line", "line_end", "end"]) {
            Some(e) => e.min(n),
            None => (start + config::read_default_lines() - 1).min(n),
        };

        if start > n {
            return format!("(file has {n} lines; start_line {start} is past end of file)");
        }

        let rel = sandbox
            .relativize(&resolved)
            .map(|p| p.display().to_string().replace('\\', "/"))
            .unwrap_or_else(|_| path.to_string());

        let mut out = format!("# {rel}  (lines {start}-{end} of {n})\n");
        for i in start..=end {
            out.push_str(&format!("{:>6}\t{}\n", i, lines[i - 1]));
        }
        if end < n {
            out.push_str(&format!(
                "\n… {} more lines. Call read again with start_line={} to continue.",
                n - end,
                end + 1
            ));
        }
        out
    }
}
