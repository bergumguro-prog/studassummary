"""
The FastAPI web server: defines the API endpoints and serves the frontend.

Run it with:
    uvicorn main:app --reload
Then open http://localhost:8000 (the app) or http://localhost:8000/docs (the API).
"""

import random
from pathlib import Path

# Load settings from .env BEFORE importing our own modules, because they read
# environment variables (like OLLAMA_MODEL) when they are imported.
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, File, Form, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

import llm  # noqa: E402
from schemas import Difficulty, Quiz, QuizResponse, SummaryResponse, TextInfo  # noqa: E402
from text_extraction import ExtractionError, extract_text, truncate_text  # noqa: E402

# Reject very large uploads so we don't read huge files into memory.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="Study Assistant",
    description="Upload lecture notes (PDF or .txt) and get a summary or a quiz.",
)


# ---------------------------------------------------------------------------
# API endpoints
#
# These are normal `def` functions (not `async def`). FastAPI runs them in a
# background thread, so a slow model call doesn't block other requests.
# ---------------------------------------------------------------------------


@app.post("/summary", response_model=SummaryResponse)
def create_summary(file: UploadFile = File(..., description="A .pdf or .txt file")):
    """Summarize the uploaded lecture notes."""
    text, text_info = _read_upload(file)
    summary = _call_llm(llm.generate_summary, text)
    return SummaryResponse(**summary.model_dump(), text_info=text_info)


@app.post("/quiz", response_model=QuizResponse)
def create_quiz(
    file: UploadFile = File(..., description="A .pdf or .txt file"),
    num_questions: int = Form(5, ge=1, le=15, description="How many questions (1-15)"),
    difficulty: Difficulty = Form(Difficulty.medium),
):
    """Generate a multiple-choice quiz from the uploaded lecture notes."""
    text, text_info = _read_upload(file)
    quiz = _call_llm(llm.generate_quiz, text, num_questions, difficulty)
    _shuffle_options(quiz)
    return QuizResponse(**quiz.model_dump(), text_info=text_info)


@app.get("/health")
def health():
    """Check whether the model is available. The frontend uses this on page load."""
    return llm.check_status()


# ---------------------------------------------------------------------------
# Frontend: serve static/index.html at "/" and other files under "/static".
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_upload(file: UploadFile) -> tuple[str, TextInfo]:
    """Read an uploaded file, extract its text and truncate it if needed."""
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "The file is too large (max 20 MB).")

    try:
        full_text = extract_text(file.filename or "", data)
    except ExtractionError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc

    text, truncated = truncate_text(full_text)
    notice = None
    if truncated:
        percent = round(100 * len(text) / len(full_text))
        notice = (
            f"The document is long, so only the first part (about {percent}%) "
            "was used. Upload a shorter section to cover the rest."
        )

    info = TextInfo(
        truncated=truncated,
        used_chars=len(text),
        total_chars=len(full_text),
        notice=notice,
    )
    return text, info


def _call_llm(function, *args):
    """Call an llm.py function and turn its errors into HTTP error responses."""
    try:
        return function(*args)
    except llm.ModelNotFoundError as exc:
        raise HTTPException(503, str(exc)) from exc  # 503 = Service Unavailable
    except llm.LLMUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    except llm.LLMInvalidResponseError as exc:
        raise HTTPException(502, str(exc)) from exc  # 502 = bad answer from upstream
    except llm.LLMError as exc:
        raise HTTPException(500, str(exc)) from exc


def _shuffle_options(quiz: Quiz) -> None:
    """Shuffle each question's options, keeping track of the correct one.

    Small models often put the correct answer first. Shuffling makes the
    quiz fairer without asking more of the model.
    """
    for q in quiz.questions:
        # Shuffle the positions [0, 1, 2, 3] rather than the texts, so it
        # still works if the model wrote two identical options.
        order = list(range(len(q.options)))
        random.shuffle(order)
        q.options = [q.options[i] for i in order]
        q.correct_index = order.index(q.correct_index)
