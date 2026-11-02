"""
Pydantic models (data shapes) used by the app.

Pydantic checks that data has the shape we expect. We use it for two things:
  1. Validating the JSON the language model sends back (it can make mistakes!).
  2. Describing what our API endpoints return, so FastAPI's /docs page can
     show it.
"""

from enum import Enum

from pydantic import BaseModel, Field


class Difficulty(str, Enum):
    """Allowed quiz difficulty levels. Using an Enum gives a dropdown in /docs."""

    easy = "easy"
    medium = "medium"
    hard = "hard"


# ---------------------------------------------------------------------------
# Models for what the LLM must return
# ---------------------------------------------------------------------------


class Summary(BaseModel):
    """A summary as produced by the language model."""

    # min_length=1 means the list must contain at least one item.
    paragraphs: list[str] = Field(min_length=1, description="A few short summary paragraphs")
    key_concepts: list[str] = Field(min_length=1, description="Bullet list of key concepts")


class QuizQuestion(BaseModel):
    """One multiple-choice question."""

    question: str = Field(min_length=1)
    # Exactly 4 options: at least 4 and at most 4.
    options: list[str] = Field(min_length=4, max_length=4)
    # ge = "greater than or equal", le = "less than or equal".
    correct_index: int = Field(ge=0, le=3)
    explanation: str = Field(min_length=1)


class Quiz(BaseModel):
    """A quiz as produced by the language model."""

    questions: list[QuizQuestion] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Models for what our API endpoints return
# ---------------------------------------------------------------------------


class TextInfo(BaseModel):
    """Information about how much of the document was used."""

    truncated: bool = Field(description="True if only part of the document was sent to the model")
    used_chars: int
    total_chars: int
    notice: str | None = Field(default=None, description="Human-readable message for the user")


class SummaryResponse(Summary):
    text_info: TextInfo


class QuizResponse(Quiz):
    text_info: TextInfo
