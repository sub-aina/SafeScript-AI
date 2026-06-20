"""
core/context.py

This file is the orchestrator - it wires together the prompt layer
(core/prompts.py) and the harness layer (core/harness.py) into one
continuous agentic loop:

    LLM generates -> AST checks -> sandbox executes -> on failure, retry

AST rejection is final (no retry - it's a policy violation, not a bug).
Sandbox failure loops back with the error as context (a bug, fixable).
Max 3 total attempts before giving up.
"""

from core.prompts import format_user_message, format_recovery_message, call_llm
from core.harness import check_ast, execute_sandbox

MAX_ATTEMPTS = 3


def run_agent(user_request: str) -> dict:
    """
    Runs the full agentic loop for a single user request.

    Returns a consistent result shape regardless of outcome:
        {
            "success": bool,
            "output": str,
            "thought": str,
            "attempts": int,
            "error": str | None,
        }
    """
    messages = format_user_message(user_request)
    attempt = 1

    while True:
        # --- 1. Ask the LLM for a script ---
        llm_result = call_llm(messages)
        thought = llm_result["thought"]
        script = llm_result["script"]

        # --- 2. Static safety check, BEFORE anything runs ---
        ast_result = check_ast(script)
        if not ast_result["safe"]:
            # Policy violation - final, no retry, no second chance.
            return {
                "success": False,
                "output": "",
                "thought": thought,
                "attempts": attempt,
                "error": f"Blocked by safety check: {ast_result['reason']}",
            }

        # --- 3. Run it in the isolated sandbox ---
        sandbox_result = execute_sandbox(script)

        if sandbox_result["success"]:
            return {
                "success": True,
                "output": sandbox_result["stdout"],
                "thought": thought,
                "attempts": attempt,
                "error": None,
            }

        # --- 4. It failed at runtime. Out of attempts? Give up. ---
        error_message = sandbox_result["stderr"] or sandbox_result.get("error", "Unknown error")

        if attempt >= MAX_ATTEMPTS:
            return {
                "success": False,
                "output": "",
                "thought": thought,
                "attempts": attempt,
                "error": f"Failed after {MAX_ATTEMPTS} attempts. Last error: {error_message}",
            }

        # --- 5. Build the recovery prompt and loop back ---
        messages = format_recovery_message(
            user_request=user_request,
            previous_script=script,
            error=error_message,
            attempt=attempt + 1,
        )
        attempt += 1