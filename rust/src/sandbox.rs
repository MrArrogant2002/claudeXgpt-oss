//! Path sandbox — every tool routes file access through this so the model can't
//! read or escape outside the project root (blocks `..`, absolute paths, and
//! symlinks that leave the root).

use anyhow::{anyhow, Result};
use std::path::{Component, Path, PathBuf};

pub struct Sandbox {
    pub root: PathBuf,
}

impl Sandbox {
    pub fn new(root: &str) -> Result<Self> {
        let root = std::fs::canonicalize(root)
            .map_err(|e| anyhow!("cannot resolve project root {root:?}: {e}"))?;
        Ok(Self { root })
    }

    /// Resolve a relative path and assert it stays inside the root.
    pub fn resolve(&self, rel: &str) -> Result<PathBuf> {
        let relp = Path::new(rel);
        if relp.is_absolute()
            || relp.components().any(|c| matches!(c, Component::ParentDir))
        {
            return Err(anyhow!("illegal path (absolute or contains ..): {rel}"));
        }
        let joined = self.root.join(relp);
        // If it exists, canonicalize to catch symlink escapes; otherwise the
        // lexical join is already safe (no `..`, not absolute).
        match std::fs::canonicalize(&joined) {
            Ok(c) if c == self.root || c.starts_with(&self.root) => Ok(c),
            Ok(_) => Err(anyhow!("path escapes project root: {rel}")),
            Err(_) => Ok(joined),
        }
    }

    /// Turn an absolute path (e.g. from a walk) back into one relative to root.
    pub fn relativize(&self, p: &Path) -> Result<PathBuf> {
        let c = std::fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf());
        if c == self.root || c.starts_with(&self.root) {
            Ok(c.strip_prefix(&self.root).unwrap_or(&c).to_path_buf())
        } else {
            Err(anyhow!("outside project root: {}", p.display()))
        }
    }
}
