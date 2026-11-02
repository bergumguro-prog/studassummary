// Frontend logic for the Study Assistant.
// Plain JavaScript, no framework: we find elements by id and update them.

// A small helper so we don't have to type document.getElementById everywhere.
const $ = (id) => document.getElementById(id);

const fileInput = $("file-input");
const summaryBtn = $("summary-btn");
const quizBtn = $("quiz-btn");

// Quiz state: the questions, where we are, and how many were right.
let questions = [];
let currentIndex = 0;
let score = 0;

// ---------------------------------------------------------------------------
// Talking to the backend
// ---------------------------------------------------------------------------

// Send the chosen file (and any extra fields) to an endpoint, return the JSON.
async function postFile(url, extraFields = {}) {
  const file = fileInput.files[0];
  if (!file) {
    throw new Error("Please choose a file first.");
  }

  // FormData builds a "multipart/form-data" request, the format for uploads.
  const formData = new FormData();
  formData.append("file", file);
  for (const [key, value] of Object.entries(extraFields)) {
    formData.append(key, value);
  }

  const response = await fetch(url, { method: "POST", body: formData });
  const data = await response.json().catch(() => null);

  if (!response.ok) {
    throw new Error(errorMessage(data, response.status));
  }
  return data;
}

// FastAPI errors look like {"detail": "..."}, or a list for validation errors.
function errorMessage(data, status) {
  if (data && typeof data.detail === "string") return data.detail;
  if (data && Array.isArray(data.detail)) {
    return data.detail.map((err) => err.msg).join(" ");
  }
  return `Something went wrong (error ${status}).`;
}

// Check on page load whether Ollama is running and the model is available.
async function checkHealth() {
  try {
    const response = await fetch("/health");
    const status = await response.json();
    if (!status.ok) showBanner(status.error);
  } catch {
    showBanner("Could not reach the Study Assistant server.");
  }
}

// ---------------------------------------------------------------------------
// Showing and hiding things
// ---------------------------------------------------------------------------

function showBanner(text) {
  $("status-banner").textContent = text;
  $("status-banner").hidden = false;
}

function showError(text) {
  $("error").textContent = text;
  $("error").hidden = false;
}

function showNotice(textInfo) {
  // The backend tells us if only part of the document was used.
  if (textInfo && textInfo.notice) {
    $("notice").textContent = textInfo.notice;
    $("notice").hidden = false;
  }
}

// Hide old results and messages before a new request.
function resetOutput() {
  for (const id of ["error", "notice", "summary-section", "quiz-section"]) {
    $(id).hidden = true;
  }
}

let timerId = null;

// Show or hide the loading spinner, and disable buttons while waiting.
function setLoading(isLoading, text = "") {
  $("loading").hidden = !isLoading;
  summaryBtn.disabled = isLoading;
  quizBtn.disabled = isLoading;

  clearInterval(timerId);
  if (isLoading) {
    $("loading-text").textContent = text;
    const start = Date.now();
    $("loading-timer").textContent = "";
    // Show elapsed seconds so the user can see it's still working.
    timerId = setInterval(() => {
      const seconds = Math.round((Date.now() - start) / 1000);
      $("loading-timer").textContent = `(${seconds}s)`;
    }, 1000);
  }
}

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------

async function handleSummary() {
  resetOutput();
  setLoading(true, "Summarizing your notes…");
  try {
    const data = await postFile("/summary");
    renderSummary(data);
    showNotice(data.text_info);
  } catch (err) {
    showError(err.message);
  } finally {
    setLoading(false); // "finally" runs whether it succeeded or failed
  }
}

function renderSummary(data) {
  const paragraphs = $("summary-paragraphs");
  const concepts = $("key-concepts");
  paragraphs.replaceChildren(); // remove old content
  concepts.replaceChildren();

  // We use textContent (not innerHTML) so text from the model can never be
  // interpreted as HTML. This is a good habit for safety.
  for (const text of data.paragraphs) {
    const p = document.createElement("p");
    p.textContent = text;
    paragraphs.appendChild(p);
  }
  for (const text of data.key_concepts) {
    const li = document.createElement("li");
    li.textContent = text;
    concepts.appendChild(li);
  }
  $("summary-section").hidden = false;
}

// ---------------------------------------------------------------------------
// Quiz
// ---------------------------------------------------------------------------

async function handleQuiz() {
  resetOutput();
  setLoading(true, "Writing quiz questions…");
  try {
    const data = await postFile("/quiz", {
      num_questions: $("num-questions").value,
      difficulty: $("difficulty").value,
    });
    questions = data.questions;
    startQuiz();
    showNotice(data.text_info);
  } catch (err) {
    showError(err.message);
  } finally {
    setLoading(false);
  }
}

function startQuiz() {
  currentIndex = 0;
  score = 0;
  $("quiz-result-view").hidden = true;
  $("quiz-question-view").hidden = false;
  $("quiz-section").hidden = false;
  showQuestion();
}

function showQuestion() {
  const q = questions[currentIndex];
  $("quiz-progress").textContent = `Question ${currentIndex + 1} of ${questions.length}`;
  $("quiz-question").textContent = q.question;
  $("quiz-feedback").hidden = true;
  $("next-btn").hidden = true;

  const options = $("quiz-options");
  options.replaceChildren();
  q.options.forEach((text, index) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "option";
    btn.textContent = text;
    btn.addEventListener("click", () => answer(index));
    options.appendChild(btn);
  });
}

function answer(chosenIndex) {
  const q = questions[currentIndex];
  const isCorrect = chosenIndex === q.correct_index;
  if (isCorrect) score++;

  // Lock the options and color the correct one (and the wrong pick, if any).
  const buttons = $("quiz-options").children;
  for (let i = 0; i < buttons.length; i++) {
    buttons[i].disabled = true;
    if (i === q.correct_index) buttons[i].classList.add("correct");
    else if (i === chosenIndex) buttons[i].classList.add("wrong");
  }

  // Feedback: "Correct!" / "Not quite." plus the explanation.
  const feedback = $("quiz-feedback");
  const verdict = document.createElement("strong");
  verdict.className = isCorrect ? "correct" : "wrong";
  verdict.textContent = isCorrect ? "Correct! " : "Not quite. ";
  feedback.replaceChildren(verdict, document.createTextNode(q.explanation));
  feedback.hidden = false;

  const isLast = currentIndex === questions.length - 1;
  $("next-btn").textContent = isLast ? "See results" : "Next";
  $("next-btn").hidden = false;
  $("next-btn").focus();
}

function nextQuestion() {
  currentIndex++;
  if (currentIndex < questions.length) {
    showQuestion();
  } else {
    showResult();
  }
}

function showResult() {
  $("quiz-question-view").hidden = true;
  $("quiz-result-view").hidden = false;
  const percent = Math.round((100 * score) / questions.length);
  $("quiz-score").textContent = `You got ${score} of ${questions.length} correct (${percent}%).`;
}

// ---------------------------------------------------------------------------
// Wire up the buttons
// ---------------------------------------------------------------------------

summaryBtn.addEventListener("click", handleSummary);
quizBtn.addEventListener("click", handleQuiz);
$("next-btn").addEventListener("click", nextQuestion);
$("retake-btn").addEventListener("click", startQuiz);

checkHealth();
