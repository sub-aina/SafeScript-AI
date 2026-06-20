"""
core/prompts.py

This file owns everything related to talking to the LLM:
- the system prompt that forces structured JSON output
- building messages for a fresh request
- building messages for a self-healing retry after a failure
- the actual API call to Groq, with JSON parsing + one retry on malformed output
"""

import os
import json
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1",
)

MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = """You are a careful automation assistant that writes Python scripts to fulfill user requests.

You must respond with ONLY valid JSON in this exact shape, nothing else:
{"thought": "short reasoning about your approach", "script": "the raw python code as a single string"}

Rules:
- No markdown formatting, no backticks, no text outside the JSON object.
- The script must be plain Python that can run standalone with `python script.py`.
- Keep scripts simple, readable, and focused only on what was asked.
- Do not include explanations inside the script as print statements unless the user's request requires printed output.

Example:
User: "print the numbers 1 to 5"
Response: {"thought": "A simple loop printing range 1 to 5 satisfies this.", "script": "for i in range(1, 6):\\n    print(i)"}
"""


def format_user_message(user_request: str) -> list:
    """Builds the initial messages array for a brand new request."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_request},
    ]


def format_recovery_message(user_request: str, previous_script: str, error: str, attempt: int) -> list:
    """
    Builds the messages array for a self-healing retry.
    Includes the original request, the script that failed, and the exact error,
    so the model has full context to fix its own mistake.
    """
    recovery_text = (
        f"Original request: {user_request}\n\n"
        f"Your previous script:\n{previous_script}\n\n"
        f"It failed with this error:\n{error}\n\n"
        f"This is attempt {attempt} of 3. Fix the script so it runs successfully. "
        f"Do not repeat the same mistake. Respond with the same JSON format as before."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": recovery_text},
    ]


def _strip_code_fences(text: str) -> str:
    """Defensive cleanup in case the model wraps JSON in markdown fences anyway."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if len(text.split("```")) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def call_llm(messages: list) -> dict:
    """
    Calls Groq's chat completion endpoint and parses the response as JSON.
    If parsing fails once, retries with an explicit instruction to fix the format.
    Returns: {"thought": str, "script": str}
    Raises: ValueError if it still fails after the retry.
    """
    response = client.chat.completions.create(
        model=MODEL,
        temperature=0.1,
        max_tokens=1024,
        messages=messages,
    )
    raw_text = response.choices[0].message.content

    try:
        cleaned = _strip_code_fences(raw_text)
        parsed = json.loads(cleaned)
        if "thought" not in parsed or "script" not in parsed:
            raise ValueError("Missing required keys")
        return parsed
    except (json.JSONDecodeError, ValueError):
        # one automatic retry, telling the model it broke the format
        retry_messages = messages + [
            {"role": "assistant", "content": raw_text},
            {"role": "user", "content": (
                "Your last response was not valid JSON matching the required schema. "
                "Respond again with ONLY the JSON object: "
                '{"thought": "...", "script": "..."}, no markdown, no extra text.'
            )},
        ]
        retry_response = client.chat.completions.create(
            model=MODEL,
            temperature=0.1,
            max_tokens=1024,
            messages=retry_messages,
        )
        retry_text = retry_response.choices[0].message.content
        cleaned_retry = _strip_code_fences(retry_text)
        parsed_retry = json.loads(cleaned_retry)  # let this raise if it still fails
        if "thought" not in parsed_retry or "script" not in parsed_retry:
            raise ValueError("Model failed to return valid structured output after retry")
        return parsed_retry