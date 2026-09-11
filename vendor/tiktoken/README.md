# Harmony tokenizer vocab (offline)

The agent's tokenizer (`openai_harmony`) needs the **o200k_base BPE vocab** to map
text ↔ token IDs. By default the library *downloads* it once and caches it in a temp
dir — which breaks offline and vanishes on reboot. To run **fully offline**, download
the file **once** and drop it in **this folder**. After that, no internet is ever needed.

## 1. Download the vocab (do this once, on any machine with internet)

**File:** `o200k_base.tiktoken`
**From:** `https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken`
**Size:** 3,613,922 bytes  **SHA-256:** `446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d`

Save it into this folder as `o200k_base.tiktoken`. Pick one:

```bash
# curl (Git Bash / Linux / macOS) — run from the repo root
curl -L -o vendor/tiktoken/o200k_base.tiktoken \
  https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken
```

```powershell
# PowerShell (Windows)
Invoke-WebRequest `
  -Uri  https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken `
  -OutFile vendor\tiktoken\o200k_base.tiktoken
```

> Download it **as a binary** (the commands above do). Do **not** open/save it through
> an editor or anything that rewrites line endings — a single LF→CRLF change corrupts
> it and the tokenizer will reject it.

## 2. Verify (optional but recommended)

```bash
# should print the SHA-256 above
sha256sum vendor/tiktoken/o200k_base.tiktoken     # Git Bash / Linux
# or PowerShell:  Get-FileHash vendor\tiktoken\o200k_base.tiktoken -Algorithm SHA256
```

## 3. Run

That's it. `agent/harmony_codec.py` finds this file, hands it to the tokenizer under the
name it expects, and loads **entirely offline** — verifying the SHA-256 first, so a bad
copy gives a clear error instead of a cryptic download failure.

---

*This file is git-ignored (it's a 3.6 MB data blob, and Git's autocrlf can corrupt it),
so each machine downloads it once. If you prefer it to travel with the repo, the root
`.gitattributes` already marks `*.tiktoken` as binary — then you can `git add -f` it safely.*
