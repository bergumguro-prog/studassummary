"""
Everything that talks to the language model lives in this file.

The rest of the app only uses these functions and exceptions:
    generate_summary(text)                        -> Summary
    generate_quiz(text, num_questions, difficulty) -> Quiz
    check_status()                                -> dict
    LLMError, LLMUnavailableError, ModelNotFoundError, LLMInvalidResponseError

To switch to another provider (e.g. a paid API), rewrite `_chat_json()` and
`check_status()` here. Nothing else in the app has to change.
"""

import json
import os

import httpx
import ollama
from pydantic import BaseModel, ValidationError

from schemas import Difficulty, Quiz, Summary

# Read settings from environment variables (loaded from .env in main.py).
MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# How many extra attempts we make if the model returns bad JSON.
MAX_RETRIES = 2

# Context window size (in tokens) we ask Ollama for. Ollama's default is
# small, which would silently cut off long documents. 8192 fits our
# ~3000-token input plus the prompt and the answer.
CONTEXT_TOKENS = 8192

# Local models can be slow, so allow a generous timeout (seconds).
_client = ollama.Client(host=HOST, timeout=300)


# ---------------------------------------------------------------------------
# Exceptions. main.py turns these into HTTP error responses.
# ---------------------------------------------------------------------------


class LLMError(Exception):
    """Base class for all LLM-related errors."""


class LLMUnavailableError(LLMError):
    """The model server can't be reached (e.g. Ollama isn't running)."""


class ModelNotFoundError(LLMError):
    """The configured model hasn't been downloaded."""


class LLMInvalidResponseError(LLMError):
    """The model kept returning JSON in the wrong format."""


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def generate_summary(text: str) -> Summary:
    """Ask the model for a summary of `text`."""
    system = (
        "You are a helpful study assistant. You summarize lecture notes for "
        "students. You always answer with valid JSON only, no other text."
    )
    user = f"""Summarize the lecture notes below.

Respond with a JSON object in exactly this format:
{{
  "paragraphs": ["short paragraph 1", "short paragraph 2", "short paragraph 3"],
  "key_concepts": ["concept 1: one-line explanation", "concept 2: one-line explanation"]
}}

Rules:
- "paragraphs": 2 to 4 short paragraphs that cover the main ideas.
- "key_concepts": 4 to 10 of the most important concepts, terms or facts.
- Use only information from the notes. Write in the same language as the notes.

LECTURE NOTES:
\"\"\"
{text}
\"\"\""""
    return _ask_with_retries(system, user, Summary, temperature=0.3, max_tokens=1200)


def generate_quiz(text: str, num_questions: int, difficulty: Difficulty) -> Quiz:
    """Ask the model for a multiple-choice quiz about `text`."""
    difficulty_hint = {
        Difficulty.easy: "Ask about basic facts and definitions stated directly in the notes.",
        Difficulty.medium: "Mix facts with questions that test understanding of the concepts.",
        Difficulty.hard: (
            "Ask questions that require applying, comparing or reasoning about the "
            "concepts. Make the wrong options plausible."
        ),
    }[difficulty]

    system = (
        "You are a helpful study assistant that writes multiple-choice quizzes "
        "for students. You always answer with valid JSON only, no other text."
    )
    user = f"""Write exactly {num_questions} multiple-choice questions about the lecture notes below.
Difficulty: {difficulty.value}. {difficulty_hint}

Respond with a JSON object in exactly this format:
{{
  "questions": [
    {{
      "question": "The question text?",
      "options": ["option A", "option B", "option C", "option D"],
      "correct_index": 2,
      "explanation": "Why the correct option is right."
    }}
  ]
}}

Rules:
- The "questions" list must contain exactly {num_questions} items.
- Each question has exactly 4 options and exactly one correct option.
- "correct_index" is the position (0, 1, 2 or 3) of the correct option in "options".
- Do not always put the correct answer in the same position.
- Use only information from the notes. Write in the same language as the notes.

LECTURE NOTES:
\"\"\"
{text}
\"\"\""""

    def has_enough_questions(quiz: Quiz) -> str | None:
        if len(quiz.questions) < num_questions:
            return (
                f"You returned {len(quiz.questions)} questions, "
                f"but exactly {num_questions} are required."
            )
        return None

    # Roughly 200 tokens per question, plus some room to spare.
    max_tokens = 300 + 250 * num_questions
    quiz = _ask_with_retries(
        system, user, Quiz, temperature=0.5, max_tokens=max_tokens, extra_check=has_enough_questions
    )
    # If the model gave us too many questions, just keep the first ones.
    quiz.questions = quiz.questions[:num_questions]
    return quiz


def check_status() -> dict:
    """Check that the model server is running and the model is downloaded."""
    try:
        installed = [m.model for m in _client.list().models]
    except (ConnectionError, httpx.ConnectError, httpx.TimeoutException):
        return {"ok": False, "model": MODEL, "error": _unavailable_message()}

    # Ollama names models like "llama3.2:latest"; accept "llama3.2" too.
    wanted = MODEL if ":" in MODEL else f"{MODEL}:latest"
    if wanted not in installed and MODEL not in installed:
        return {"ok": False, "model": MODEL, "error": _not_found_message()}
    return {"ok": True, "model": MODEL, "error": None}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ask_with_retries(system, user, model_class, temperature, max_tokens, extra_check=None):
    """Send a prompt, parse + validate the JSON answer, and retry on failure.

    Small models sometimes return broken JSON or leave out fields. When that
    happens we show the model its mistake and ask again (up to MAX_RETRIES
    extra times). That usually fixes it.
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    last_problem = ""

    for attempt in range(1 + MAX_RETRIES):
        raw = _chat_json(messages, temperature, max_tokens)

        try:
            data = json.loads(raw)
            result = model_class.model_validate(data)
            problem = extra_check(result) if extra_check else None
            if problem is None:
                return result  # Success!
        except json.JSONDecodeError as exc:
            problem = f"Your answer was not valid JSON ({exc})."
        except ValidationError as exc:
            problem = f"Your JSON did not match the required format:\n{_short_errors(exc)}"

        last_problem = problem
        print(f"[llm] Attempt {attempt + 1} failed: {problem}")

        # Add the bad answer + a correction request to the conversation, so
        # the model can see what it did wrong on the next attempt.
        messages += [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": f"{problem}\nPlease answer again with only the corrected JSON, "
                "following the format exactly.",
            },
        ]

    raise LLMInvalidResponseError(
        f"The model did not return a valid answer after {1 + MAX_RETRIES} attempts "
        f"(last problem: {last_problem}). Please try again, or try a larger model."
    )


def _chat_json(messages: list[dict], temperature: float, max_tokens: int) -> str:
    """Send messages to Ollama in JSON mode and return the raw text answer.

    This is the only function that actually calls Ollama for generation.

    `max_tokens` limits the length of the answer. This matters: in JSON mode
    small models sometimes get "stuck" and keep printing whitespace forever.
    With a limit, such an answer is cut off, fails JSON parsing, and gets
    retried instead of hanging the app.
    """
    try:
        response = _client.chat(
            model=MODEL,
            messages=messages,
            format="json",  # Forces the model to output syntactically valid JSON
            options={
                "temperature": temperature,
                "num_ctx": CONTEXT_TOKENS,
                "num_predict": max_tokens,  # Ollama's name for "max output tokens"
            },
        )
    except (ConnectionError, httpx.ConnectError) as exc:
        raise LLMUnavailableError(_unavailable_message()) from exc
    except httpx.TimeoutException as exc:
        raise LLMUnavailableError("The model took too long to answer. Please try again.") from exc
    except ollama.ResponseError as exc:
        if exc.status_code == 404:
            raise ModelNotFoundError(_not_found_message()) from exc
        raise LLMError(f"Ollama returned an error: {exc.error}") from exc

    return response.message.content or ""


def _short_errors(exc: ValidationError) -> str:
    """Turn Pydantic's error list into a few short readable lines."""
    lines = []
    for err in exc.errors()[:5]:
        location = ".".join(str(part) for part in err["loc"]) or "(top level)"
        lines.append(f"- {location}: {err['msg']}")
    return "\n".join(lines)


def _unavailable_message() -> str:
    return (
        f"Could not connect to Ollama at {HOST}. Is it running? "
        "Start the Ollama app, or run `ollama serve` in a terminal."
    )


def _not_found_message() -> str:
    return (
        f"The model '{MODEL}' is not downloaded. "
        f"Run `ollama pull {MODEL}` in a terminal and try again."
    )
