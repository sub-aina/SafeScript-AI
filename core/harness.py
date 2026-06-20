"""
core/harness.py

This file owns the two safety layers that sit between the LLM's output
and actual execution:

  1. check_ast()        -> static analysis, runs BEFORE any code executes
  2. execute_sandbox()  -> isolated execution, runs the script safely

Two separate, independent layers. AST catches known-dangerous operations
in the code's structure. Sandbox contains whatever AST didn't catch.
"""

import subprocess
import tempfile
import os
import ast


# Attribute-style dangerous operations: module.thing() or obj.thing()
# We match on the attribute name itself, regardless of what it's called on.
DANGEROUS_ATTRIBUTES = {
    "system",       # os.system - runs arbitrary shell commands
    "remove",       # os.remove - deletes a file
    "rmdir",        # os.rmdir - deletes a directory
    "rmtree",       # shutil.rmtree - recursively deletes a directory tree
    "move",         # shutil.move - moves/renames files
    "unlink",       # os.unlink - another way to delete a file
    "exit",         # sys.exit - kills the process
    "kill",         # os.kill - sends a signal to a process
    "popen",        # os.popen - spawns a shell command
    "run",          # subprocess.run - spawns a new process
    "call",         # subprocess.call - spawns a new process
    "Popen",        # subprocess.Popen - spawns a new process
}

# Bare-call dangerous builtins: eval(...), exec(...) etc, called directly by name
DANGEROUS_CALLS = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "getattr",   # reflection - can reach attributes indirectly, bypassing the check above
    "setattr",
}

# Modules that should never be imported - network access, process spawning
DANGEROUS_IMPORTS = {
    "socket",
    "requests",
    "urllib",
    "http",
    "subprocess",
    "ftplib",
    "smtplib",
}


def check_ast(script: str) -> dict:
    """
    Parses the script into an AST and walks every node, checking against
    the denylists above. Returns immediately on the first violation found.

    Returns:
        {"safe": True}
        or
        {"safe": False, "reason": "<specific reason>"}
    """
    try:
        tree = ast.parse(script)
    except SyntaxError as e:
        return {"safe": False, "reason": f"Script has a syntax error: {e}"}

    for node in ast.walk(tree):

        # Case 1: import os / import socket / import subprocess
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level_module = alias.name.split(".")[0]
                if top_level_module in DANGEROUS_IMPORTS:
                    return {
                        "safe": False,
                        "reason": f"Blocked import: '{alias.name}' (network/process access)",
                    }

        # Case 2: from os import remove / from socket import socket
        if isinstance(node, ast.ImportFrom):
            if node.module:
                top_level_module = node.module.split(".")[0]
                if top_level_module in DANGEROUS_IMPORTS:
                    return {
                        "safe": False,
                        "reason": f"Blocked import: 'from {node.module} import ...' (network/process access)",
                    }

        # Case 3: os.remove(...) / shutil.rmtree(...) / sys.exit(...)
        # These show up as an Attribute node - "something.attr"
        if isinstance(node, ast.Attribute):
            if node.attr in DANGEROUS_ATTRIBUTES:
                return {
                    "safe": False,
                    "reason": f"Blocked operation: '.{node.attr}(...)' is not allowed",
                }

        # Case 4: eval(...) / exec(...) / getattr(...)
        # These show up as a Call node where func is a bare Name, not an Attribute
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in DANGEROUS_CALLS:
                return {
                    "safe": False,
                    "reason": f"Blocked call: '{node.func.id}(...)' is not allowed",
                }

        # Case 5: open("file", "w") / open("file", "a") - write access
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "open":
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        mode = arg.value
                        # exact match against known write/append/create modes,
                        # not a substring check - "r" must never trigger this,
                        # but "w", "a", "x", "w+", "rb+" etc should.
                        write_modes = {"w", "a", "x", "w+", "a+", "x+",
                                       "wb", "ab", "xb", "wb+", "ab+", "xb+",
                                       "r+", "rb+"}
                        if mode in write_modes:
                            return {
                                "safe": False,
                                "reason": "Blocked: file write/append access via open()",
                            }


    return {"safe": True}


def execute_sandbox(script: str, timeout_seconds: int = 3) -> dict:
    """
    Runs a script in an isolated child process with a hard timeout.

    Writes the script to a temp file, runs it via subprocess (a separate
    OS process - not exec(), so a crash or infinite loop in the script
    cannot affect this server), captures stdout/stderr, then always
    deletes the temp file afterward.

    Returns:
        {"success": True, "stdout": "...", "stderr": "", "exit_code": 0}
        or
        {"success": False, "error": "timeout"|"runtime_error"|"...", "stdout": "...", "stderr": "..."}
    """
    temp_file_path = None
    try:
        # Write the script to a real file on disk - subprocess needs a
        # file path to run, it can't execute a raw string directly.
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as temp_file:
            temp_file.write(script)
            temp_file_path = temp_file.name

        result = subprocess.run(
            ["python3", temp_file_path],
            capture_output=True,   # capture stdout/stderr as strings instead of printing them
            timeout=timeout_seconds,
            text=True,             # decode output as text, not raw bytes
        )

        if result.returncode == 0:
            return {
                "success": True,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": 0,
            }
        else:
            return {
                "success": False,
                "error": "runtime_error",
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
            }

    except subprocess.TimeoutExpired:
        # The child process has already been killed automatically by
        # subprocess at this point - the server was never blocked.
        return {
            "success": False,
            "error": "timeout",
            "stdout": "",
            "stderr": f"Execution timed out after {timeout_seconds} seconds. Possible infinite loop.",
            "exit_code": None,
        }

    except Exception as e:
        # Catch-all for anything unexpected (e.g. python3 not found, permission issues)
        return {
            "success": False,
            "error": str(e),
            "stdout": "",
            "stderr": "",
            "exit_code": None,
        }

    finally:
        # Always clean up the temp file, whether the script succeeded,
        # crashed, or timed out.
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)