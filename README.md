# SafeScript-AI

An agent that turns plain-English requests into Python scripts — writes the code, checks it for safety, runs it in isolation, and self-heals if it fails.

```bash
python cli.py "count how many .py files are in this directory"
```

---

## How it works

```
User request
      ↓
Groq (Llama 3.3 70B) writes a script + explains its reasoning
      ↓
AST check — static analysis blocks dangerous code BEFORE it runs
      ↓
Sandbox — runs the script in an isolated process, 3s timeout
      ↓
   success → return output
   failure → error fed back to the model → retry (max 3x)
```

Two independent safety layers:

- **AST static analysis** parses the script's structure (not just its text) and blocks known-dangerous operations — file deletion, process spawning, network access, `eval`/`exec`, reflection. Catches things string-matching would miss, like `import os as o; o.remove(...)`.
- **Process sandboxing** runs the script in a separate OS process instead of `exec()`, so a crash or infinite loop can't affect the server. A hard timeout kills anything that hangs.

A runtime failure is treated as a fixable bug — the error gets fed back to the model for a retry. An AST rejection is treated as final — no retry, since the model tried something disallowed.

---

## A real self-healing example

**Request:** `"read a file called data.csv and print its first 5 lines"`

- **Attempt 1** — script tries to open `data.csv`. File doesn't exist. Sandbox returns `FileNotFoundError`.
- **Attempt 2** — that error gets fed back. The model wraps the read in a try/except and returns a clean message instead of crashing.

No human in the loop between attempts.

---

## Stack

Groq (Llama 3.3 70B) · Python `ast` · Python `subprocess` · no agent framework — the prompt formatting, safety checks, and retry loop are all built from scratch.

---

## Setup

```bash
git clone <your-repo-url>
cd safescript-ai
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Add a `.env` file:
```
GROQ_API_KEY=your_key_here
```

Get a free key at [console.groq.com](https://console.groq.com).

---

## Usage

```bash
python cli.py "list all Python files in the current directory"
python cli.py "delete all files in this folder using os.remove"   # gets blocked
```

---

## Limitations

- AST checking matches a fixed denylist — it can't reason about intent, so a combination of individually-safe operations could in theory slip through.
- Process isolation prevents crashes/hangs from affecting the server, but it isn't a full sandbox (no Docker, no filesystem restriction) — fine for a portfolio project, not for production with untrusted users.
- Retries are capped at 3 to avoid burning API calls on unsolvable requests.