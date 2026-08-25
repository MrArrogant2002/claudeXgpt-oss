"""Path sandbox (M2/§7) — every tool routes filesystem access through this so the
model can never read or escape outside the project root (blocks ../, absolute
paths, and symlinks that leave the root)."""

from pathlib import Path

from . import config


class Sandbox:
    def __init__(self, root=None):
        self.root = Path(root or config.PROJECT_ROOT).resolve()

    def _check(self, p: Path) -> Path:
        rp = (
            p.resolve()
        )  # resolves symlinks and .. ; strict=False so missing files are ok
        if rp != self.root and self.root not in rp.parents:
            raise PermissionError(f"path escapes project root: {p}")
        return rp

    def resolve(self, rel) -> Path:
        """Resolve a (possibly relative) path and assert it stays in the root."""
        return self._check(self.root / rel)

    def relativize(self, p) -> Path:
        """Turn an absolute path back into one relative to the root (asserts containment)."""
        rp = self._check(Path(p))
        return rp.relative_to(self.root)
