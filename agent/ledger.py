"""Evidence ledger (build plan, Phase 5).

Grounding has to be verified mechanically here. A judge model is not available:
a hosted one contradicts the offline premise, and the model under test judging
itself is circular. So instead of scoring the answer's plausibility, the agent
records what it actually observed and the answer is required to cite it.

Each observation is an immutable row:

    (obs_id, tool, path, line range, sha256 of the observed bytes, turn)

Two properties matter.

**Compaction immunity.** The ledger is not part of the summarisable history; it
is re-rendered into the prompt each turn from this structure. Compaction is known
to erase in-context constraints silently, and an agent that loses the observation
of a failing test can go on to assert that the test passed. Keeping the evidence
outside the compactable region is what prevents that.

**Verifiability.** `verify` re-reads every cited range and compares hashes, so a
citation that no longer holds is detected rather than believed.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

#: How a final answer refers to an observation, e.g. "[E3]".
CITATION_RE = re.compile(r"\[E(\d+)\]")


def _normalize(text: str) -> str:
    """Canonical form for hashing an observation.

    Record time sees whatever the tool returned; verification time sees a slice
    reconstructed from the file. Those differ by trailing-newline handling, so
    both sides normalise to newline-joined lines before hashing. Without this
    every citation reads as stale and the grounding metric reports zero.
    """
    return "\n".join(text.splitlines())


def _sha256(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode("utf-8", "replace")).hexdigest()


@dataclass(frozen=True)
class Observation:
    obs_id: str
    tool: str
    path: str | None
    start_line: int | None
    end_line: int | None
    content_sha256: str
    turn: int
    chars: int

    @property
    def span(self) -> str:
        if self.start_line is None:
            return ""
        if self.end_line is None or self.end_line == self.start_line:
            return f":{self.start_line}"
        return f":{self.start_line}-{self.end_line}"

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class VerificationReport:
    """Outcome of checking an answer's citations against the filesystem."""

    cited: list[str] = field(default_factory=list)
    verified: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)      # file changed since observed
    unknown: list[str] = field(default_factory=list)    # cites a non-existent obs_id
    uncited_claims: int = 0

    @property
    def citation_count(self) -> int:
        return len(self.cited)

    @property
    def verified_rate(self) -> float:
        return len(self.verified) / len(self.cited) if self.cited else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "cited": self.cited,
            "verified": self.verified,
            "stale": self.stale,
            "unknown": self.unknown,
            "uncited_claims": self.uncited_claims,
            "verified_rate": round(self.verified_rate, 4),
        }


class EvidenceLedger:
    """Append-only record of what the agent observed."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._rows: list[Observation] = []
        self._by_id: dict[str, Observation] = {}

    def __len__(self) -> int:
        return len(self._rows)

    def __iter__(self) -> Iterator[Observation]:
        return iter(self._rows)

    @property
    def rows(self) -> list[Observation]:
        return list(self._rows)

    def get(self, obs_id: str) -> Observation | None:
        return self._by_id.get(obs_id)

    # --- recording ---------------------------------------------------------
    def record(
        self,
        *,
        tool: str,
        content: str,
        path: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        turn: int = 0,
    ) -> Observation:
        """Append one observation and return it. The id is positional (`E1`,
        `E2`, …) because the model has to be able to cite it cheaply in prose."""
        obs = Observation(
            obs_id=f"E{len(self._rows) + 1}",
            tool=tool,
            path=path,
            start_line=start_line,
            end_line=end_line,
            content_sha256=_sha256(content),
            turn=turn,
            chars=len(content),
        )
        self._rows.append(obs)
        self._by_id[obs.obs_id] = obs
        return obs

    # --- prompt rendering --------------------------------------------------
    def render(self, *, limit: int = 40, max_chars: int = 2000) -> str:
        """A compact table for the prompt.

        Rendered into a developer-role message, not a user-role one: fabricating
        user turns pollutes the transcript and confounds any later attribution of
        what the user actually asked. Newest rows win when truncating, because
        those are the ones a current answer is most likely to cite.
        """
        if not self._rows:
            return ""
        lines = ["[evidence] cite these ids in your answer, e.g. [E1]"]
        for obs in self._rows[-limit:]:
            where = f"{obs.path}{obs.span}" if obs.path else "(no path)"
            lines.append(f"{obs.obs_id}  {obs.tool:<9} {where}")
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n… (older evidence elided)"
        return text

    # --- verification ------------------------------------------------------
    def verify(self, answer: str) -> VerificationReport:
        """Check an answer's citations against the filesystem.

        A citation verifies when the referenced range still hashes to what was
        observed. Note what this does and does not establish: that the agent read
        what it says it read, not that its conclusion follows. The paper reports
        it as grounding, never as correctness.
        """
        report = VerificationReport()
        seen: set[str] = set()
        for match in CITATION_RE.finditer(answer):
            obs_id = f"E{match.group(1)}"
            if obs_id in seen:
                continue
            seen.add(obs_id)
            report.cited.append(obs_id)
            obs = self._by_id.get(obs_id)
            if obs is None:
                report.unknown.append(obs_id)
                continue
            if self._still_matches(obs):
                report.verified.append(obs_id)
            else:
                report.stale.append(obs_id)
        report.uncited_claims = self._count_uncited_claims(answer)
        return report

    def _still_matches(self, obs: Observation) -> bool:
        if obs.path is None:
            return False
        target = self.root / obs.path
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        if obs.start_line is not None:
            lines = text.splitlines()
            end = obs.end_line or obs.start_line
            text = "\n".join(lines[obs.start_line - 1 : end])
        return _sha256(text) == obs.content_sha256

    @staticmethod
    def _count_uncited_claims(answer: str) -> int:
        """Sentences that assert something about the code but cite nothing.

        A blunt heuristic, and reported as such: it counts sentences mentioning a
        path-like or identifier-like token with no citation. It is a screening
        signal for manual review, not a metric to report on its own.
        """
        uncited = 0
        for sentence in re.split(r"(?<=[.!?])\s+", answer):
            if not sentence.strip() or CITATION_RE.search(sentence):
                continue
            if re.search(r"[\w/]+\.(py|js|ts|go|rs|java|c|h|md)\b", sentence) or re.search(
                r"\b\w+\(\)|\b[a-z_]+\.[a-z_]+\b", sentence
            ):
                uncited += 1
        return uncited

    # --- serialisation -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {"root": str(self.root), "rows": [o.to_dict() for o in self._rows]}


def observation_from_read(
    ledger: EvidenceLedger,
    *,
    path: str,
    content: str,
    start_line: int | None,
    end_line: int | None,
    turn: int,
) -> Observation:
    """Convenience wrapper for the `read` tool, whose observations are the ones
    a grounded answer most often cites."""
    return ledger.record(
        tool="read",
        content=content,
        path=path,
        start_line=start_line,
        end_line=end_line,
        turn=turn,
    )


def summarise(reports: Iterable[VerificationReport]) -> dict[str, Any]:
    """Aggregate per-task reports into the grounding row of a results table."""
    reports = list(reports)
    if not reports:
        return {"tasks": 0}
    cited = sum(r.citation_count for r in reports)
    verified = sum(len(r.verified) for r in reports)
    return {
        "tasks": len(reports),
        "citations": cited,
        "verified": verified,
        "verified_rate": round(verified / cited, 4) if cited else 0.0,
        "stale": sum(len(r.stale) for r in reports),
        "unknown": sum(len(r.unknown) for r in reports),
        "uncited_claims": sum(r.uncited_claims for r in reports),
        "tasks_with_no_citation": sum(1 for r in reports if r.citation_count == 0),
    }
