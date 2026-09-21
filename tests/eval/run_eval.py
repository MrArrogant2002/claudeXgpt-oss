#!/usr/bin/env python3
"""Eval harness — drive the agent (via cli.py --json) over a task set and score it.

Each task in tasks.jsonl is a self-contained instruction with an objective check.
The harness runs one fresh `cli.py` process per task (clean state, no history bleed),
reads the JSON summary it prints, scores the answer, and — for tasks that edit code —
runs a verify command (e.g. the test suite) against a throwaway copy of the fixture.

Runs on the GPU box (needs llama-server up). The scoring/diff logic is offline-testable:
    python tests/eval/run_eval.py --self-test        # no model needed

Typical use (on the box):
    python tests/eval/run_eval.py --label mxfp4-t0.6
    python tests/eval/run_eval.py --only locate,explain --python .venv/Scripts/python.exe
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = REPO_ROOT / "cli.py"
DEFAULT_TASKS = Path(__file__).resolve().parent / "tasks.jsonl"
DEFAULT_PROJECT = REPO_ROOT / "demo-project"
REPORTS_DIR = Path(__file__).resolve().parent / "reports"

# Files/dirs that don't count as "source" for copy + change detection.
_IGNORE_DIRS = {"__pycache__", "node_modules", "dist", "build", ".agent-backups", ".git"}
_IGNORE_SUFFIX = (".pyc", ".db")


# ---------------------------------------------------------------------------- #
# task loading + scoring (pure, offline-testable)
# ---------------------------------------------------------------------------- #
def load_tasks(path):
    tasks = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            tasks.append(json.loads(line))
    return tasks


def score(task, answer, ctx):
    """Return (ok: bool, reasons: list[str]) for a task given the agent's answer and
    a context dict {verify_ok, changed_files}. Pure — the core of the harness."""
    checks = task.get("check", {})
    a = (answer or "").lower()
    reasons = []

    for sub in checks.get("contains_all", []):
        if sub.lower() not in a:
            reasons.append(f"missing required text: {sub!r}")
    if "contains_any" in checks:
        if not any(s.lower() in a for s in checks["contains_any"]):
            reasons.append(f"none of the accepted phrases present: {checks['contains_any']}")
    for sub in checks.get("not_contains", []):
        if sub.lower() in a:
            reasons.append(f"forbidden text present: {sub!r}")
    if "regex" in checks:
        if not re.search(checks["regex"], answer or "", re.IGNORECASE):
            reasons.append(f"regex did not match: {checks['regex']!r}")

    if "verify_cmd" in checks:
        if ctx.get("verify_ok") is not True:
            reasons.append("verify command did not pass (suite still failing)")
    for f in checks.get("forbid_changes", []):
        if f in ctx.get("changed_files", set()):
            reasons.append(f"changed a file it must not touch: {f}")

    return (len(reasons) == 0), reasons


# ---------------------------------------------------------------------------- #
# fixture copy + change detection (for mutating tasks)
# ---------------------------------------------------------------------------- #
def _iter_source_files(root):
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS]
        for fn in filenames:
            if fn.endswith(_IGNORE_SUFFIX):
                continue
            p = Path(dirpath) / fn
            yield str(p.relative_to(root)).replace("\\", "/")


def copy_fixture(src, dst):
    def _ignore(_dir, names):
        return [n for n in names if n in _IGNORE_DIRS or n.endswith(_IGNORE_SUFFIX)]

    shutil.copytree(src, dst, ignore=_ignore)


def diff_tree(pristine, work):
    """Set of rel paths that differ between two trees (added / removed / modified)."""
    changed = set()
    pset = set(_iter_source_files(pristine))
    wset = set(_iter_source_files(work))
    changed |= pset ^ wset  # added or removed
    for rel in pset & wset:
        try:
            a = (Path(pristine) / rel).read_bytes()
            b = (Path(work) / rel).read_bytes()
        except OSError:
            changed.add(rel)
            continue
        if a != b:
            changed.add(rel)
    return changed


# ---------------------------------------------------------------------------- #
# running one task
# ---------------------------------------------------------------------------- #
def build_cmd(task, project_dir, python):
    return (
        [python, str(CLI), "--project", str(project_dir), "--json"]
        + list(task.get("flags", []))
        + [task["prompt"]]
    )


def run_agent(task, project_dir, python, timeout):
    """Run cli.py --json and return the parsed summary dict (or an error dict)."""
    cmd = build_cmd(task, project_dir, python)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        proc = subprocess.run(
            cmd, cwd=str(REPO_ROOT), capture_output=True, text=True,
            timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"timeout after {timeout}s", "reason": "timeout", "answer": ""}
    except OSError as e:  # bad --python, missing interpreter, etc.
        return {"error": f"could not launch cli.py: {e}", "reason": "launch_failed", "answer": ""}
    out = (proc.stdout or "").strip()
    for line in reversed(out.splitlines()):  # last JSON line wins
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {
        "error": "no JSON summary on stdout",
        "reason": "no_output",
        "answer": "",
        "stderr_tail": (proc.stderr or "")[-400:],
    }


def run_verify(check, project_dir):
    cmd = check["verify_cmd"]
    cwd = Path(project_dir) / check.get("verify_cwd", ".")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                              timeout=300, env=env)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def run_task(task, project, python, timeout, keep_temp=False):
    """Run + score one task. Returns a result dict."""
    mutates = bool(task.get("mutates"))
    work_dir = None
    tmp_parent = None
    if mutates:
        tmp_parent = Path(__file__).resolve().parent / ".work"
        tmp_parent.mkdir(exist_ok=True)
        work_dir = tmp_parent / f"{task['id']}-{int(time.time())}"
        copy_fixture(project, work_dir)
        target = work_dir
    else:
        target = project

    t0 = time.time()
    summary = run_agent(task, target, python, timeout)
    elapsed = round(time.time() - t0, 1)

    ctx = {"verify_ok": None, "changed_files": set()}
    if mutates:
        ctx["changed_files"] = diff_tree(project, work_dir)
        if "verify_cmd" in task.get("check", {}):
            ctx["verify_ok"] = run_verify(task["check"], work_dir)

    ok, reasons = score(task, summary.get("answer", ""), ctx)
    # A run that errored out (no answer / timeout) can never pass.
    if summary.get("error"):
        ok = False
        reasons = [summary["error"]] + reasons

    if work_dir and not keep_temp:
        shutil.rmtree(work_dir, ignore_errors=True)

    return {
        "id": task["id"],
        "category": task.get("category", "?"),
        "passed": ok,
        "reasons": reasons,
        "elapsed_s": elapsed,
        "agent_reason": summary.get("reason"),
        "turns": summary.get("turns"),
        "output_tokens": summary.get("output_tokens"),
        "calls": summary.get("calls"),
        "salvaged": summary.get("salvaged"),
        "recoveries": summary.get("recoveries"),
        "changed_files": sorted(ctx["changed_files"]),
        "answer_preview": (summary.get("answer") or "")[:200],
        "error": summary.get("error"),
    }


# ---------------------------------------------------------------------------- #
# reporting
# ---------------------------------------------------------------------------- #
def print_scorecard(results, label):
    print()
    print(f"  eval scorecard{f'  [{label}]' if label else ''}")
    print("  " + "-" * 78)
    print(f"  {'id':<5} {'category':<10} {'result':<6} {'turns':>5} {'out':>6} "
          f"{'salv':>4} {'rec':>4} {'time':>6}  reason")
    print("  " + "-" * 78)
    for r in results:
        res = "PASS" if r["passed"] else "FAIL"
        print(f"  {r['id']:<5} {r['category']:<10} {res:<6} "
              f"{str(r['turns'] or '-'):>5} {str(r['output_tokens'] or '-'):>6} "
              f"{str(r['salvaged'] or 0):>4} {str(r['recoveries'] or 0):>4} "
              f"{str(r['elapsed_s'])+'s':>6}  {r['agent_reason'] or ''}")
        if not r["passed"] and r["reasons"]:
            print(f"        -> {'; '.join(r['reasons'])}")
    print("  " + "-" * 78)
    passed = sum(1 for r in results if r["passed"])
    by_cat = {}
    for r in results:
        c = r["category"]
        by_cat.setdefault(c, [0, 0])
        by_cat[c][1] += 1
        by_cat[c][0] += 1 if r["passed"] else 0
    cats = "  ".join(f"{c} {v[0]}/{v[1]}" for c, v in sorted(by_cat.items()))
    print(f"  TOTAL: {passed}/{len(results)} passed   ({cats})")
    print()


def write_report(results, label, path):
    REPORTS_DIR.mkdir(exist_ok=True)
    payload = {
        "label": label,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "passed": sum(1 for r in results if r["passed"]),
        "total": len(results),
        "results": results,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------- #
# self-test (offline: no model, no cli.py) — verifies the harness logic itself
# ---------------------------------------------------------------------------- #
def self_test():
    import tempfile

    # score(): contains_all / contains_any / not_contains / regex
    t = {"check": {"contains_all": ["shortener.py"]}}
    assert score(t, "It's in services/api/nimbus_api/shortener.py", {})[0] is True
    assert score(t, "no idea", {})[0] is False
    t = {"check": {"contains_any": ["shorten", "url"]}}
    assert score(t, "A URL shortener", {})[0] is True
    assert score(t, "a database", {})[0] is False
    t = {"check": {"contains_all": ["0"], "not_contains": ["error"]}}
    assert score(t, "encode(0) returns empty", {})[0] is True
    assert score(t, "0 but there was an error", {})[0] is False

    # verify_cmd + forbid_changes
    t = {"check": {"verify_cmd": ["true"], "forbid_changes": ["tests/x.py"]}}
    assert score(t, "", {"verify_ok": True, "changed_files": {"src/a.py"}})[0] is True
    assert score(t, "", {"verify_ok": False, "changed_files": set()})[0] is False
    assert score(t, "", {"verify_ok": True, "changed_files": {"tests/x.py"}})[0] is False
    print("score(): all cases OK")

    # diff_tree(): detects modified / added / removed, ignores junk
    a = Path(tempfile.mkdtemp())
    b = Path(tempfile.mkdtemp())
    (a / "keep.py").write_text("x = 1\n")
    (a / "edit.py").write_text("y = 1\n")
    (a / "gone.py").write_text("z = 1\n")
    (a / "__pycache__").mkdir()
    (a / "__pycache__" / "j.pyc").write_text("junk")
    copy_fixture(a, b / "copy")
    (b / "copy" / "edit.py").write_text("y = 2\n")   # modify
    (b / "copy" / "new.py").write_text("n = 1\n")    # add
    (b / "copy" / "gone.py").unlink()                # remove
    changed = diff_tree(a, b / "copy")
    assert changed == {"edit.py", "new.py", "gone.py"}, changed
    assert not (b / "copy" / "__pycache__").exists(), "junk should not be copied"
    print("diff_tree(): modified/added/removed detected, junk ignored OK")

    # build_cmd(): shape
    cmd = build_cmd({"prompt": "hi", "flags": ["--allow-exec"]}, "/proj", "py")
    assert cmd[:5] == ["py", str(CLI), "--project", "/proj", "--json"]
    assert cmd[-1] == "hi" and "--allow-exec" in cmd
    print("build_cmd(): shape OK")

    # tasks.jsonl parses and is well-formed
    tasks = load_tasks(DEFAULT_TASKS)
    assert len(tasks) >= 5
    ids = [t["id"] for t in tasks]
    assert len(ids) == len(set(ids)), "duplicate task ids"
    for t in tasks:
        assert "prompt" in t and "check" in t, t
        if t.get("mutates"):
            assert "verify_cmd" in t["check"], f"{t['id']}: mutating task needs verify_cmd"
    print(f"tasks.jsonl: {len(tasks)} tasks, well-formed OK")

    print("\nALL SELF-TESTS PASSED")


# ---------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Eval harness for the local code agent")
    ap.add_argument("--tasks", default=str(DEFAULT_TASKS))
    ap.add_argument("--project", default=str(DEFAULT_PROJECT), help="fixture repo to run against")
    ap.add_argument("--python", default=sys.executable, help="python used to launch cli.py")
    ap.add_argument("--only", default="", help="comma list of task ids or categories to run")
    ap.add_argument("--timeout", type=int, default=600, help="per-task seconds")
    ap.add_argument("--label", default="", help="tag for this run (e.g. the model/quant config)")
    ap.add_argument("--list", action="store_true", help="list tasks and exit")
    ap.add_argument("--keep-temp", action="store_true", help="keep temp copies of mutated fixtures")
    ap.add_argument("--self-test", action="store_true", help="test the harness logic offline (no model)")
    args = ap.parse_args()

    if args.self_test:
        self_test()
        return

    # Resolve a relative --python (e.g. ".venv/Scripts/python.exe") to an absolute
    # path so the child process launches regardless of the harness's own cwd.
    py_path = Path(args.python)
    python = str(py_path.resolve()) if py_path.exists() else args.python

    tasks = load_tasks(args.tasks)
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        tasks = [t for t in tasks if t["id"] in wanted or t.get("category") in wanted]
    if args.list:
        for t in tasks:
            print(f"  {t['id']:<5} {t.get('category',''):<10} {t['prompt']}")
        return
    if not tasks:
        print("no tasks selected", file=sys.stderr)
        sys.exit(1)

    print(f"running {len(tasks)} task(s) against {args.project}")
    results = []
    for t in tasks:
        print(f"  · {t['id']} ({t.get('category')}) …", flush=True)
        results.append(run_task(t, args.project, python, args.timeout, args.keep_temp))

    print_scorecard(results, args.label)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{(args.label + '-') if args.label else ''}{stamp}.json"
    path = write_report(results, args.label, REPORTS_DIR / name)
    print(f"  report: {path}")


if __name__ == "__main__":
    main()
