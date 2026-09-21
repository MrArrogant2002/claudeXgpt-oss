# Eval harness

Drive the agent over a fixed task set and score it objectively, so you can compare
model/quant/serving configs on the metrics that matter (see
[../../docs/research/evaluation-and-trustworthiness.md](../../docs/research/evaluation-and-trustworthiness.md)).

## Pieces
- **`tasks.jsonl`** — one task per line: a prompt plus an objective `check`.
- **`run_eval.py`** — runs each task in a fresh `cli.py --json` process, scores it, and
  prints a scorecard + writes a JSON report to `reports/`.
- Tasks run against **`demo-project/`** (the Nimbus fixture) by default.

## Run it (on the GPU box, with llama-server up)
```bash
# all tasks, tagged with the config you're testing
python tests/eval/run_eval.py --label mxfp4-kv8-t0.6 --python .venv/Scripts/python.exe

# a subset
python tests/eval/run_eval.py --only locate,explain
python tests/eval/run_eval.py --only F1 --keep-temp     # inspect the agent's edit

# just list what would run
python tests/eval/run_eval.py --list
```

Compare two configs by running twice with different `--label`s (relaunch llama-server
with the new flags in between) and diff the two `reports/*.json`.

## Verify the harness itself (offline, no model)
```bash
python tests/eval/run_eval.py --self-test
```

## Task schema
```json
{
  "id": "F1",
  "category": "locate|explain|cross-lang|run-fix",
  "prompt": "what to ask the agent",
  "flags": ["--allow-exec", "--allow-edit", "--permission-mode", "acceptEdits"],
  "mutates": true,
  "check": {
    "contains_all": ["substr", ...],       // all must appear (case-insensitive)
    "contains_any": ["substr", ...],       // at least one must appear
    "not_contains": ["substr", ...],       // none may appear
    "regex": "pattern",                     // must match the answer
    "verify_cmd": ["python","-m","unittest","..."],  // run after; must exit 0
    "verify_cwd": "services/api",          // where to run verify_cmd (rel. to fixture)
    "forbid_changes": ["path/that/must/stay/identical"]
  }
}
```

- `mutates: true` runs the task against a **throwaway copy** of the fixture, so edits
  never dirty the repo. `forbid_changes` fails a task that "fixes" a bug by editing the
  test instead of the source.
- Metrics captured per task (from `cli.py --json`): agent outcome, turns, output tokens,
  salvaged malformed headers, and recovery count — a config that spins shows up as high
  turns/recoveries even when it eventually passes.

## Adding tasks
Append a line to `tasks.jsonl`. Prefer objective checks: a unique file/symbol name for
locate/explain, a `verify_cmd` (tests) for edit/run-fix. Keep answers checkable without
an LLM judge where you can.
