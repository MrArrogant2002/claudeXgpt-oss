//! Glob tool — locate files by path pattern, in-process, respecting .gitignore.

use globset::Glob;
use serde_json::{json, Value};

use super::Tool;
use crate::sandbox::Sandbox;

pub struct GlobTool;

impl Tool for GlobTool {
    fn name(&self) -> &str {
        "glob"
    }

    fn description(&self) -> &str {
        "Find files by glob pattern relative to the project root (e.g. '**/*.py', \
         'src/**/*config*'). Returns matching file paths only — does not read them."
    }

    fn parameters(&self) -> Value {
        json!({
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern, e.g. **/*.py"},
                "limit": {"type": "integer", "description": "Max paths to return (default 200)"}
            },
            "required": ["pattern"]
        })
    }

    fn run(&self, args: &Value, sandbox: &Sandbox) -> String {
        let pattern = match args.get("pattern").and_then(|v| v.as_str()) {
            Some(p) => p,
            None => return "ERROR: missing 'pattern'".to_string(),
        };
        let limit = args.get("limit").and_then(|v| v.as_u64()).unwrap_or(200).min(1000) as usize;

        let matcher = match Glob::new(pattern) {
            Ok(g) => g.compile_matcher(),
            Err(e) => return format!("ERROR: bad glob pattern {pattern:?}: {e}"),
        };

        let mut hits: Vec<String> = Vec::new();
        for dent in ignore::Walk::new(&sandbox.root).flatten() {
            if !dent.file_type().map(|t| t.is_file()).unwrap_or(false) {
                continue;
            }
            if let Ok(rel) = dent.path().strip_prefix(&sandbox.root) {
                if matcher.is_match(rel) {
                    hits.push(rel.to_string_lossy().replace('\\', "/"));
                    if hits.len() >= limit {
                        break;
                    }
                }
            }
        }
        hits.sort();
        if hits.is_empty() {
            "(no matches)".to_string()
        } else {
            hits.join("\n")
        }
    }
}
