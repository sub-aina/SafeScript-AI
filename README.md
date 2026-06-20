# SafeScript-AI

Turns plain-English requests into Python scripts, checks them for safety, runs them in isolation, and retries with self-correction if they fail.

```bash
python cli.py "count how many .py files are in this directory"
```

## How it works

1. **Groq (Llama 3.3 70B)** writes a script for the request
2. **AST check** blocks dangerous code (file deletion, eval, network access, etc.) before it runs
3. **Sandbox** runs the script in an isolated process with a 3s timeout
4. On failure, the error is fed back to the model and it retries (max 3 attempts)

## Stack

Groq · Python `ast` · Python `subprocess` — no agent framework, built from scratch.