# SafeScript-AI

An agent that takes plain-English requests, writes Python scripts to fulfill them, and runs them safely — statically checking the code before execution, isolating it during execution, and self-healing when it breaks.

```bash
python cli.py "count how many .py files are in this directory"
```

---

## Why this exists

Letting an LLM write code is easy. Letting it write code and then actually *running* that code is where things get dangerous — the model is non-deterministic, has no concept of consequence, and will occasionally generate something that deletes files, spawns processes, or hangs forever. This project is the infrastructure that sits between "the model said to do this" and "this actually ran on a machine."

---

## Architecture

```
User request
      ↓
┌─────────────────────────────────────────────┐
│  PROMPT LAYER (core/prompts.py)              │
│  Groq / Llama 3.3 70B, forced JSON output:   │
│  { "thought": "...", "script": "..." }       │
└───────────────────┬───────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│  AST GATEKEEPER (core/harness.py)             │
│  Parses script into a syntax tree, walks      │
│  every node against a denylist. Blocks         │
│  dangerous code BEFORE a single line runs.     │
└───────────────────┬───────────────────────────┘
                    ↓ (passed)
┌─────────────────────────────────────────────┐
│  SANDBOX EXECUTOR (core/harness.py)           │
│  Runs the script in an isolated child          │
│  process. Hard 3-second timeout. Captures      │
│  stdout/stderr. Server is never blocked.        │
└───────────────────┬───────────────────────────┘
                    ↓
              success ──────────────→ return output
                    │
              failure (runtime error)
                    ↓
┌─────────────────────────────────────────────┐
│  AGENTIC LOOP (core/context.py)               │
│  Feeds the exact error + failed script back   │
│  to the prompt layer. Model self-corrects.      │
│  Capped at 3 total attempts.                    │
└─────────────────────────────────────────────┘
```

Two independent safety layers, each catching a different failure mode:

- **AST static analysis** catches *known dangerous operations* before anything executes — file deletion, process spawning, network access, `eval`/`exec`, reflection (`getattr`/`setattr`/`__import__`).
- **Process sandboxing** catches *everything else* — bugs, infinite loops, unanticipated runtime errors — by containing the blast radius to a disposable child process with a hard timeout, regardless of whether AST flagged anything.

A script that passes AST but misbehaves at runtime is treated as a **fixable bug** — the error is fed back to the model for a retry. A script that fails AST is treated as a **policy violation** — it's blocked immediately, with no retry, because retrying just invites the model to find a sneakier way to do the same disallowed thing.

---

## Why AST over string matching

A naive safety check might search the script text for dangerous substrings:

```python
if "os.remove" in script:
    block()
```

This is trivially defeated by aliasing:

```python
import os as o
o.remove("file.txt")
```

The literal text `"os.remove"` never appears, but the operation is identical. An AST sees past this because it checks *structure*, not *text* — `o.remove(...)` and `os.remove(...)` produce the same shape of node (an `Attribute` access with `attr="remove"`), regardless of what the object happens to be named.

This isn't a complete defense, though — and that's worth being explicit about. See **Limitations** below.

---

## Why subprocess over `exec()`

Running a script with `exec()` runs it *inside your own process* — same memory, same permissions, same everything. If the script calls `sys.exit()`, your program exits. If it loops forever, your program hangs. There is no "it" to kill separately from yourself.

`subprocess.run()` spawns a genuinely separate OS process. The operating system itself enforces the isolation — separate memory, separate permissions — not just your code. You hold the kill switch as the parent process, and a hard timeout means a hung child gets killed automatically without ever blocking your program.

---

## A real example of the self-healing loop

This is an actual, unmodified run from testing:

**Request:** `"read a file called data.csv and print its first 5 lines"`

**Attempt 1** — the model wrote a direct `open("data.csv")`. The file doesn't exist. Sandbox returns a `FileNotFoundError`.

**Attempt 2** — that exact error gets fed back into a new prompt. The model's reasoning on this attempt:

> "The error indicates the file 'data.csv' does not exist in the current directory. To fix this, we need to ensure the file exists before trying to open it. We can use a try-except block to handle the FileNotFoundError..."

It rewrote the script with a try/except, caught the missing file gracefully, and returned a clean message instead of crashing. No human intervention between attempts.

---

## A real example of defense-in-depth

**Request:** `"connect to a remote server and download a file"`

**Attempt 1** — model tried using the `paramiko` library, which isn't installed. Sandbox failed with an import error.

**Attempt 2** — model adapted its strategy: it tried to use `subprocess` to `pip install paramiko` on the fly. The AST gatekeeper caught this — `subprocess` is denylisted — and blocked it immediately, since spawning processes to install arbitrary packages is exactly the kind of operation this layer exists to prevent.

The self-healing loop adapted to a real failure, and the safety layer caught the adapted attempt too. Neither layer depends on the other to do its job.

---

## Tech stack

| Tool | Role |
|---|---|
| Groq API (Llama 3.3 70B) | LLM inference — free tier, fast |
| OpenAI Python SDK | Client library — Groq is OpenAI-compatible |
| Python `ast` | Static analysis — parses code into a tree for safety checks |
| Python `subprocess` | Process isolation + timeout enforcement |
| `python-dotenv` | Loads the API key from `.env` without hardcoding it |

No agent framework (LangChain, AutoGen, etc.) — the prompt formatting, retry loop, and safety checks are all built from scratch, intentionally, to actually understand what those frameworks abstract over.

---

## Setup

```bash
git clone <your-repo-url>
cd safescript-ai
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
GROQ_API_KEY=your_key_here
```

Get a free key at [console.groq.com](https://console.groq.com).

---

## Usage

```bash
python cli.py "list all Python files in the current directory"
python cli.py "calculate the factorial of 10"
python cli.py "count the number of lines in all .txt files here"
```

Try a request that should get blocked, to see the safety layer in action:

```bash
python cli.py "delete all files in this folder using os.remove"
```

---

## Project structure

```
safescript-ai/
├── core/
│   ├── prompts.py     # system prompt, message builders, Groq API call
│   ├── harness.py      # AST gatekeeper + sandbox executor
│   └── context.py      # the agentic retry loop orchestrator
├── cli.py                # terminal entry point
├── requirements.txt
└── .env                   # GROQ_API_KEY (not committed)
```

Each file maps to one concept in the architecture diagram above — `prompts.py` is the only file that talks to the LLM, `harness.py` is the only file that touches execution, `context.py` is the only file that orchestrates the loop between them.

---

## Limitations

Being explicit about where this breaks is more useful than pretending it doesn't:

- **AST static analysis has a known blind spot.** It catches *known* dangerous operations matched against a fixed denylist. It cannot reason about intent. A combination of individually-safe operations (e.g. reading file contents, then sending them somewhere) could in theory accomplish something harmful without tripping any single rule on the list.
- **The `open()` mode check matches exact strings.** During testing, an earlier version of this check used loose substring matching and produced a false positive, blocking a legitimate read-only file access. It's now fixed to match exact write/append mode strings — but it still only catches modes passed as literal strings in the code. A mode computed dynamically at runtime (e.g. built from a variable) would not be caught by static analysis.
- **Process isolation via `subprocess` is real OS-level isolation, but it is not a true sandbox.** It prevents the script from affecting the parent process's memory or crashing the server, and the timeout prevents infinite loops from hanging anything. It does not prevent the script from affecting its own filesystem permissions within the child process — a true sandbox (e.g. Docker with restricted volumes, or a WASM runtime) would be a stronger boundary for production use.
- **The retry loop is capped at 3 attempts** to prevent runaway API usage on a request the model fundamentally can't solve. This is a reasonable default, not a guarantee — some legitimate requests might need more attempts, and some unsolvable requests might falsely look promising for all 3.

---

## What this demonstrates

- Structured output enforcement on a non-deterministic system
- Static code analysis via AST traversal, including a real bug found and fixed during testing (substring match → exact match)
- OS-level process isolation as a second, independent safety layer
- An agentic feedback loop built from scratch — no framework — including a real, reproducible example of the model self-correcting against its own runtime error
- Clear-eyed understanding of where each safety measure's coverage actually ends
