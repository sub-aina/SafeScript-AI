"""
cli.py

Terminal entry point for SafeScript-AI. Pure presentation layer - all
the real logic already lives in core/. This file just calls run_agent()
and prints the result in a readable, color-coded way.

Usage:
    python cli.py "find all log files older than 7 days"
"""

import argparse
import sys
from core.context import run_agent

# ANSI color codes - no external library needed
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
RESET = "\033[0m"
BOLD = "\033[1m"


def colored(text: str, color: str) -> str:
    return f"{color}{text}{RESET}"


def print_stage(text: str):
    print(colored(text, DIM))


def main():
    parser = argparse.ArgumentParser(
        description="SafeScript-AI - turns plain English requests into safely-executed scripts."
    )
    parser.add_argument(
        "request",
        type=str,
        help="The task you want done, in plain English. Wrap in quotes.",
    )
    args = parser.parse_args()

    user_request = args.request

    print()
    print(colored(f"> {user_request}", BOLD))
    print()
    print_stage("[1/4] Parsing intent...")
    print_stage("[2/4] Analyzing AST...")
    print_stage("[3/4] Executing in sandbox...")

    result = run_agent(user_request)

    print_stage("[4/4] Done.")
    print()

    # Always show the model's reasoning, dimmed so it doesn't compete with the result
    print(colored(f"Thought: {result['thought']}", DIM))
    print()

    if result["error"] is None:
        # --- Success ---
        print(colored("✓ Success", GREEN), colored(f"(attempt {result['attempts']}/3)", DIM))
        print()
        print(result["output"])

    elif result["error"].startswith("Blocked by safety check:"):
        # --- AST rejection - final, no retries happened ---
        print(colored("✗ Blocked", RED))
        print(colored(result["error"], RED))

    else:
        # --- Exhausted all retries ---
        if result["attempts"] > 1:
            print(colored(f"Attempts 1-{result['attempts'] - 1} failed, retrying with error feedback...", YELLOW))
        print(colored("✗ Failed", RED))
        print(colored(result["error"], RED))
        sys.exit(1)


if __name__ == "__main__":
    main()