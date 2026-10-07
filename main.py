import json
import keyword
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from streamlit.errors import StreamlitSecretNotFoundError

# ---------------------------------------------------------------------------
# Groq AI tutor — configuration (loaded from the .env file)
# ---------------------------------------------------------------------------
load_dotenv()

# ---------------------------------------------------------------------------
# Groq AI tutor — configuration with VALIDATION at every step
# ---------------------------------------------------------------------------
load_dotenv()  # Load .env file if it exists (local dev only)

GROQ_MODEL = "openai/gpt-oss-120b"
AI_NOT_CONFIGURED = "AI tutor not configured"


def _load_groq_key() -> str | None:
    """Load and VALIDATE the Groq API key from multiple sources.
    
    Priority order:
    1. Environment variable GROQ_API_KEY (local dev via .env)
    2. Streamlit Cloud secrets (production deployment)
    
    Returns:
        Valid API key string, or None if unavailable/invalid.
    
    Validation steps:
    - Key must exist
    - Key must be a non-empty string
    - Key must be stripped of whitespace
    - Key must start with 'gsk_' (Groq key format)
    """
    # --- STEP 1: Try environment variable ---
    key = os.getenv("GROQ_API_KEY")
    
    # --- STEP 2: Fall back to Streamlit secrets ---
    if not key:
        try:
            key = st.secrets.get("GROQ_API_KEY")
        except (KeyError, StreamlitSecretNotFoundError):
            # st.secrets not available (local dev without Streamlit context)
            key = None
    
    # --- STEP 3: Validate the key ---
    if key is None:
        return None  # No key found anywhere
    
    if not isinstance(key, str):
        return None  # Key must be a string
    
    # Strip whitespace (common mistake in secrets.toml)
    key = key.strip()
    
    if not key:
        return None  # Empty string after strip
    
    # Groq keys always start with 'gsk_'
    if not key.startswith("gsk_"):
        return None  # Invalid format — likely a copy-paste error
    
    return key


# Load the validated key (or None if unavailable)
GROQ_API_KEY = _load_groq_key()


# --- Import Groq SDK with validation ---
try:
    import groq
    from groq import Groq
    _GROQ_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - groq SDK not installed
    groq = None
    Groq = None
    _GROQ_SDK_AVAILABLE = False


def is_ai_available() -> bool:
    """Check if AI features are fully operational.
    
    Returns True only when:
    - Groq SDK is installed
    - Valid API key is loaded
    """
    return _GROQ_SDK_AVAILABLE and GROQ_API_KEY is not None

TEACHING_STYLE = (
    "You are a very patient teacher for a complete beginner who learns slowly. "
    "Follow these rules strictly: "
    "(1) Use very simple words and short sentences. "
    "(2) One idea per sentence. "
    "(3) Explain every technical word in brackets, e.g. 'loop (repeat the same steps again and again)'. "
    "(4) Use everyday examples like counting apples, buying things at a shop, or sorting socks. "
    "(5) NEVER say 'this is easy' or 'obviously' or 'simply' — it makes the learner feel bad. "
    "(6) If the learner is wrong, first say one kind thing like 'Good try!' or 'You are thinking well!', then gently explain what to fix. "
    "(7) Keep answers short: 3 to 6 bullet points maximum. "
    "(8) End with one small encouraging sentence like 'You are doing great!' or 'Keep going, you will get it!'."
)

# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Logic Builder",
    page_icon="🧩",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Wizard steps (based on the PRD)
# ---------------------------------------------------------------------------
STEPS = [
    "Problem Statement",
    "Requirements Analysis",
    "Algorithm Design",
    "Code Writing",
    "Testing",
    "Optimization",
]

# Options used in Step 2 (Requirements Analysis)
DATA_TYPES = ["int", "float", "str", "bool", "list", "dict", "tuple", "set"]
CONCEPTS = [
    "Variables & Assignment",
    "Conditionals (if/elif/else)",
    "Loops (for/while)",
    "Functions",
    "String Methods",
    "List Methods",
    "Slicing",
    "Comprehensions",
    "Exception Handling",
    "Recursion",
]

# Named step indices — used instead of magic numbers in comparisons
IDX_PROBLEM = 0
IDX_REQUIREMENTS = 1
IDX_ALGORITHM = 2
IDX_CODE = 3
IDX_TESTING = 4
IDX_OPTIMIZATION = 5


# ---------------------------------------------------------------------------
# Session state — one key per wizard step
# ---------------------------------------------------------------------------
WIZARD_DEFAULTS: dict = {
    # Navigation
    "current_step": 0,
    "finished": False,
    # Step 1 — Problem Statement
    "problem_title": "",
    "problem_description": "",
    "title_suggestions": [],
    "problem_inputs": "",
    "problem_outputs": "",
    "problem_rules": "",
    "inputs_quiz": None,
    "outputs_quiz": None,
    "rules_quiz": None,
    "test_quiz": None,
    "quiz_serial": 0,
    "requirements_recommendation": None,
    "optimization_recommendation": None,
    # Step 2 — Requirements Analysis
    **{f"datatype_{dtype}": False for dtype in DATA_TYPES},
    "concept_selection": [],
    # Step 3 — Algorithm Design
    "pseudocode": "",
    "algorithm_line_review": None,
    # Step 4 — Code Writing
    "python_code": "",
    "code_line_suggestion": None,
    "code_line_review": None,
    "expected_test_output": "",
    # Step 5 — Testing
    "test_input": "",
    "test_output": "",
    "test_error": "",
    "test_returncode": None,
    "test_ran": False,
    # Step 6 — Optimization
    "opt_time_complexity": "",
    "opt_space_complexity": "",
    "opt_notes": "",
    # AI tutor
    "messages": [],
    "urdu_messages": [],
    "ai_validation": None,
    "ai_suggestion": None,
    "suggested_filename": "",
}


def _fresh(value):
    """Return a copy of mutable defaults so state never leaks between resets."""
    return list(value) if isinstance(value, list) else value


def init_session_state() -> None:
    """Seed st.session_state with every wizard key (safe to call repeatedly)."""
    for key, value in WIZARD_DEFAULTS.items():
        st.session_state.setdefault(key, _fresh(value))


def reset_wizard() -> None:
    """Restore every wizard key to its default value."""
    for key, value in WIZARD_DEFAULTS.items():
        st.session_state[key] = _fresh(value)


# ---------------------------------------------------------------------------
# Groq AI tutor
# ---------------------------------------------------------------------------
def ask_groq(
    system_prompt: str, user_message: str, *, temperature: float = 0.7
) -> str | None:
    """Send one chat completion to Groq. Returns None on any failure."""
    if groq is None or Groq is None or not GROQ_API_KEY:
        return None
    try:
        client = Groq(api_key=GROQ_API_KEY)
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
    except (groq.GroqError, OSError):
        # Auth/rate-limit/API errors, connection failures, timeouts, ...
        return None
    choices = response.choices
    return choices[0].message.content if choices else None


def wizard_user_data(step_index: int) -> str:
    """Compact snapshot of the wizard's data, focused on the given step."""
    state = st.session_state
    if step_index == IDX_PROBLEM:
        parts = [
            f"Problem description: {state.problem_description}",
            f"Title: {state.problem_title}",
            f"Inputs: {state.problem_inputs}",
            f"Outputs: {state.problem_outputs}",
            f"Rules: {state.problem_rules}",
        ]
    elif step_index == IDX_REQUIREMENTS:
        selected = [d for d in DATA_TYPES if state[f"datatype_{d}"]]
        parts = [
            f"Problem statement:\n{_problem_statement_text()}",
            f"Data types: {', '.join(selected) or 'none'}",
            f"Concepts: {', '.join(state.concept_selection) or 'none'}",
        ]
    elif step_index == IDX_ALGORITHM:
        parts = [
            f"Problem statement:\n{_problem_statement_text()}",
            f"Pseudocode:\n{state.pseudocode}",
        ]
    elif step_index == IDX_CODE:
        parts = [f"Python code:\n{state.python_code}"]
    elif step_index == IDX_TESTING:
        if state.test_ran:
            parts = [
                f"Exit code: {state.test_returncode}",
                f"stdout: {state.test_output}",
                f"stderr: {state.test_error}",
            ]
        else:
            parts = ["The code has not been run yet."]
    else:
        parts = [
            f"Time complexity: {state.opt_time_complexity or 'not set'}",
            f"Space complexity: {state.opt_space_complexity or 'not set'}",
            f"Optimization notes: {state.opt_notes}",
        ]
    data = "\n".join(parts).strip()
    return data if data else "No data entered yet."


def tutor_system_prompt(step_index: int | None = None) -> str:
    """Socratic tutor system prompt, aware of the current wizard step."""
    index = st.session_state.current_step if step_index is None else step_index
    step = f"{index + 1}. {STEPS[index]}"
    data = wizard_user_data(index)
    return (
        "You are a patient beginner programming tutor in Logic Builder. "
        "Always explain in easy Roman Urdu (Urdu written using English letters). "
        "Guide the learner spoon-feed style in short numbered key points: tell "
        "them exactly which box to fill first, what to write next, and why each "
        "step is needed. If a step is wrong, politely say what is wrong in "
        "Roman Urdu, show the exact corrected wording/code in English, then "
        "explain the reason in Roman Urdu. Accept any correct equivalent; do "
        "not call a valid answer wrong just because it differs from your "
        "preferred wording. Do not repeat a point already correct. If needed "
        "information cannot be inferred, ask one short question instead of "
        "guessing. Keep the answer concise, with at most 4 key points. "
        f"Current step: {step}. User data: {data}"
    )


VALIDATION_SYSTEM_PROMPT = TEACHING_STYLE + " " + (
    "You are a meticulous requirements analyst inside Logic Builder. "
    "Understand the whole problem before reviewing any field. Find logical "
    "flaws, contradictions, ambiguities and missing required information in "
    "the material for this step. Reply with ONLY a JSON object (no markdown "
    "fences) in this shape: "
    '{"feedback":[{"field":"exact box name","mistake_roman_urdu":"short '
    'description of the mistake","solution_english":"exact corrected text or '
    'step to enter in that box","reason_roman_urdu":"why this step is needed"}]}. '
    "Explain mistakes and reasons in simple Roman Urdu (English letters only). "
    "Write only the exact replacement text/code in English in solution_english. "
    "Review each box against the entire problem and identify the required "
    "inputs and rules from the task; never assume a fixed number of inputs. "
    "Only report a missing item when it is truly required by this problem. "
    "Accept correct equivalent wording and approaches. Never mark a valid "
    "field wrong because optional detail or your preferred phrasing is absent. "
    "Do not repeat issues in fields that are already correct. Order feedback "
    "as small steps the learner can apply, and give the reason for each. If a "
    "requirement cannot be inferred, add clarification_roman_urdu with one "
    "brief question and omit solution_english instead of inventing an answer. "
    "If there are no real issues, return "
    "{\"feedback\":[]}. Be concise and do not give full solution code."
)


def _parse_feedback_items(
    value: object, *, require_field: bool = False
) -> list[dict[str, str]] | None:
    """Validate structured mistake/solution/reason feedback from the AI."""
    if not isinstance(value, list):
        return None

    feedback = []
    required_keys = ["mistake_roman_urdu", "reason_roman_urdu"]
    if require_field:
        required_keys.append("field")

    for item in value:
        if not isinstance(item, dict):
            return None
        if any(
            not isinstance(item.get(key), str) or not item[key].strip()
            for key in required_keys
        ):
            return None
        has_solution = isinstance(item.get("solution_english"), str) and bool(
            item["solution_english"].strip()
        )
        has_question = isinstance(
            item.get("clarification_roman_urdu"), str
        ) and bool(item["clarification_roman_urdu"].strip())
        if has_solution == has_question:
            return None
        parsed_item = {key: item[key].strip() for key in required_keys}
        response_key = (
            "solution_english" if has_solution else "clarification_roman_urdu"
        )
        parsed_item[response_key] = item[response_key].strip()
        feedback.append(parsed_item)
    return feedback


def _render_feedback_items(feedback: list[dict[str, str]]) -> None:
    """Render each finding in the requested mistake, solution, reason order."""
    for index, item in enumerate(feedback, start=1):
        st.markdown(f"**Step {index}**")
        if "field" in item:
            st.markdown(f"**Box:** {item['field']}")
        st.markdown(f"**Masla:** {item['mistake_roman_urdu']}")
        if "solution_english" in item:
            st.markdown("**Is tarah likhein (English):**")
            st.code(item["solution_english"], language="text")
        else:
            st.markdown(
                f"**Pehle yeh batayein:** {item['clarification_roman_urdu']}"
            )
        st.markdown(f"**Yeh kyun zaroori hai:** {item['reason_roman_urdu']}")


def _parse_step_validation(raw: str | None) -> list[dict] | None:
    """Parse a structured whole-step validation reply."""
    data = _extract_json_object(raw)
    if data is None or "feedback" not in data:
        return None
    return _parse_feedback_items(data["feedback"], require_field=True)


def render_ai_validation(step_index: int) -> None:
    """'🔍 Validate with AI' button + result (Steps 1-3)."""
    if st.button("🔍 Validate with AI", key=f"validate_ai_{step_index}"):
        with st.spinner("Asking the AI reviewer…"):
            reply = ask_groq(
                VALIDATION_SYSTEM_PROMPT,
                wizard_user_data(step_index),
                temperature=0.2,
            )
        st.session_state.ai_validation = {
            "step": step_index,
            "reply": (
                _parse_step_validation(reply)
                if reply
                else AI_NOT_CONFIGURED
            ),
        }

    validation = st.session_state.ai_validation
    if validation and validation["step"] == step_index:
        reply = validation["reply"]
        if isinstance(reply, str):
            st.info(reply)
        elif reply is None:
            st.error("AI ka review samajh nahi aya. Dobara koshish karein.")
        elif reply:
            _render_feedback_items(reply)
        else:
            st.success("Koi zaroori ghalti nahi mili. Yeh fields theek lag rahe hain.")


# ---------------------------------------------------------------------------
# Field-level AI checks (Step 1 fields + Step 3 pseudocode)
# ---------------------------------------------------------------------------
FIELD_CHECK_SYSTEM_PROMPT = TEACHING_STYLE + " " + (
    "You are a reviewer inside Logic Builder, an app where learners write out a "
    "programming problem before coding it. Validate whether the user's '{field}' "
    "is appropriate for a programming problem: clear, specific, unambiguous and "
    "complete enough for a beginner to implement.\n"
    "Reply with ONLY a JSON object (no markdown fences) with these top-level keys:\n"
    '{{"is_valid": true/false, '
    '"issues": [{{"mistake_roman_urdu": "what is wrong, in easy Roman Urdu", '
    '"solution_english": "the exact corrected text to enter, in English", '
    '"reason_roman_urdu": "why this step is needed, in easy Roman Urdu"}}], '
    '"suggestions": ["Optional short tip in Roman Urdu"]}}\n'
    "Use the complete problem context to understand what this exact task needs. "
    "For an Inputs box, derive the required input values from the problem, "
    "state how many distinct values are needed only when the task makes that "
    "clear, and identify which ones are missing. Never assume every problem "
    "has the same inputs. Check only the requested box; do not blame this box "
    "for content that belongs in another box. Preserve parts that are already "
    "correct and give ordered, small corrections for actual mistakes only. "
    "Treat equivalent correct wording or valid approaches as correct; do not "
    "demand optional detail. If the task does not provide enough information "
    "to decide, add clarification_roman_urdu with one short question in Roman "
    "Urdu and omit solution_english rather than guessing. Every issue must "
    "have mistake_roman_urdu and reason_roman_urdu plus exactly one of "
    "solution_english or clarification_roman_urdu. Explain the mistake and "
    "reason in easy Roman Urdu. Put only copy-ready corrected text in English "
    "in solution_english. Keep feedback "
    "short and specific. is_valid must be true exactly when issues is empty. "
    "Write optional suggestions in Roman Urdu and omit generic or unnecessary "
    "suggestions. "
    "Do not add greetings or closing encouragement; the app shows those."
)


def _extract_json_object(raw: str | None) -> dict | None:
    """Strip markdown fences, extract the JSON object and parse it; None on failure."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = "\n".join(
            line for line in text.splitlines() if not line.strip().startswith("```")
        ).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


TITLE_SUGGESTION_SYSTEM_PROMPT = (
    "You suggest concise, clear titles for programming problems. Read the "
    "problem statement and return exactly three distinct, specific titles "
    "that describe the task. Keep each title under 80 characters. Reply with "
    'ONLY a JSON object in this format: {"titles": ["Title 1", "Title 2", '
    '"Title 3"]}. Do not include markdown or explanations.'
)


def _parse_title_suggestions(raw: str | None) -> list[str] | None:
    """Parse up to three usable titles from the AI's JSON response."""
    data = _extract_json_object(raw)
    if data is None or not isinstance(data.get("titles"), list):
        return None
    titles = [
        title.strip()[:120]
        for title in data["titles"]
        if isinstance(title, str) and title.strip()
    ][:3]
    return titles or None


def _parse_field_quiz(
    raw: str | None, *, require_expected_output: bool = False
) -> dict | None:
    """Validate generated multiple-choice questions before showing them."""
    data = _extract_json_object(raw)
    if data is None or not isinstance(data.get("questions"), list):
        return None

    questions = []
    for item in data["questions"]:
        if not isinstance(item, dict):
            return None
        question = item.get("question")
        options = item.get("options")
        correct_option = item.get("correct_option")
        reason = item.get("reason_roman_urdu")
        expected_output = item.get("expected_output")
        if (
            not isinstance(question, str)
            or not question.strip()
            or not isinstance(options, list)
            or not 2 <= len(options) <= 4
            or any(
                not isinstance(option, str)
                or not option.strip()
                or len(option) > 240
                or "\n" in option
                or "\r" in option
                for option in options
            )
            or len({option.strip() for option in options}) != len(options)
            or not isinstance(correct_option, str)
            or correct_option.strip() not in {option.strip() for option in options}
            or not isinstance(reason, str)
            or not reason.strip()
            or (
                require_expected_output
                and (
                    not isinstance(expected_output, str)
                    or len(expected_output) > 1000
                )
            )
            or (
                expected_output is not None
                and (
                    not isinstance(expected_output, str)
                    or len(expected_output) > 1000
                )
            )
        ):
            return None
        questions.append(
            {
                "question": question.strip()[:500],
                "options": [option.strip()[:240] for option in options],
                "correct_option": correct_option.strip()[:240],
                "reason_roman_urdu": reason.strip()[:500],
                "expected_output": expected_output,
            }
        )

    clarification = data.get("clarification_roman_urdu", "")
    if not isinstance(clarification, str):
        return None
    return {
        "questions": questions,
        "clarification_roman_urdu": clarification.strip()[:500],
    }


def _field_quiz_prompt(field_name: str) -> str:
    """Create a field-specific prompt for questions derived from the task."""
    field_guidance = {
        "Inputs": (
            "Test which values a user must provide, their type, and any relevant "
            "constraints. Do not include values the program calculates itself."
        ),
        "Outputs": (
            "Test what the program must display or return, including its format "
            "when specified. Do not confuse output with input."
        ),
        "Rules": (
            "Test the task's actual conditions, transformations, and edge cases. "
            "Do not invent rules not stated or implied by the problem."
        ),
        "Test cases": (
            "Create only the distinct test inputs essential to verify the "
            "specified behavior, including a boundary only when the statement "
            "requires it. Each correct option must be one valid stdin input "
            "line for the current code. Do not invent a required sample or "
            "combine separate executions into one test input."
        ),
    }
    return (
        "You are creating a beginner multiple-choice quiz for the "
        f"{field_name} box in a programming problem-solving app. "
        f"{field_guidance[field_name]} Read the complete problem context and "
        "the box's current content. Determine how many distinct facts or "
        "cases are strictly required by this specific problem and not already "
        "correctly present. Return exactly one question per such missing item: "
        "do not use a fixed question count, do not cap the list at an arbitrary "
        "number, and do not add optional or speculative questions. If all "
        "required items are already covered or the problem gives no "
        "reliable answer, return an empty questions list and put a brief "
        "clarifying question in clarification_roman_urdu when appropriate. "
        "Each question must have 2 to 4 distinct, plausible options, with "
        "exactly one correct_option that exactly matches one option. The "
        "correct option must be a concise, copy-ready line suitable for adding "
        "to the box. For Test cases, include expected_output as the exact expected "
        "stdout for the correct stdin option. Give a brief reason in easy Roman "
        "Urdu. Never guess missing "
        "requirements or repeat facts already in the box. Reply only with JSON "
        'in this shape: {"questions":[{"question":"...","options":["...","...",'
        '"...","..."],"correct_option":"...","reason_roman_urdu":"...",'
        '"expected_output":"..."}],'
        '"clarification_roman_urdu":""}.'
    )


def _start_field_quiz(field_name: str, field_key: str, quiz_key: str) -> None:
    """Generate a quiz from the pasted problem and current field contents."""
    description = st.session_state.problem_description.strip()
    if not description:
        st.session_state[quiz_key] = {
            "questions": [],
            "clarification_roman_urdu": "Pehle Problem Statement box mein sawal paste karein.",
            "index": 0,
            "result": None,
            "id": None,
        }
        return

    context = (
        f"Problem statement:\n{description}\n\n"
        f"Problem title: {st.session_state.problem_title.strip() or '(not provided)'}\n"
        f"Inputs: {st.session_state.problem_inputs.strip() or '(empty)'}\n"
        f"Outputs: {st.session_state.problem_outputs.strip() or '(empty)'}\n"
        f"Rules: {st.session_state.problem_rules.strip() or '(empty)'}"
    )
    if field_name == "Test cases":
        context += (
            f"\n\nPseudocode:\n{st.session_state.pseudocode.strip() or '(empty)'}"
            f"\n\nPython code:\n{st.session_state.python_code.strip() or '(empty)'}"
            f"\n\nExisting test input:\n"
            f"{st.session_state.test_input.strip() or '(empty)'}"
        )
    with st.spinner(f"Preparing the {field_name.lower()} quiz…"):
        reply = ask_groq(
            _field_quiz_prompt(field_name),
            context,
            temperature=0.2,
        )

    quiz = (
        _parse_field_quiz(reply, require_expected_output=field_name == "Test cases")
        if reply
        else None
    )
    if quiz is None:
        st.session_state[quiz_key] = {
            "questions": [],
            "clarification_roman_urdu": "",
            "index": 0,
            "result": None,
            "id": None,
            "error": "AI tutor not configured" if not reply else "invalid_reply",
        }
        return

    st.session_state.quiz_serial += 1
    quiz.update({"index": 0, "result": None, "id": st.session_state.quiz_serial})
    st.session_state[quiz_key] = quiz


def _append_quiz_answer(field_key: str, answer: str) -> bool:
    """Append a correct answer to its box without duplicating an existing line."""
    current = st.session_state[field_key].strip()
    if answer.casefold() not in {line.strip().casefold() for line in current.splitlines()}:
        st.session_state[field_key] = f"{current}\n{answer}".strip()
        return True
    return False


def _submit_field_quiz_answer(
    field_key: str, quiz_key: str, expected_quiz_id: int, expected_index: int
) -> None:
    """Grade an answer only if the callback still matches its current question."""
    quiz = st.session_state[quiz_key]
    if not isinstance(quiz, dict):
        return
    index = quiz.get("index")
    questions = quiz.get("questions")
    if (
        quiz.get("id") != expected_quiz_id
        or index != expected_index
        or not isinstance(index, int)
        or isinstance(index, bool)
        or not isinstance(questions, list)
        or index < 0
        or index >= len(questions)
        or not isinstance(questions[index], dict)
    ):
        return
    question = questions[index]
    radio_key = f"field_quiz_answer_{expected_quiz_id}_{index}"
    selected = st.session_state.get(radio_key)
    is_correct = selected == question["correct_option"]
    updated_quiz = dict(quiz)
    updated_quiz["result"] = {"is_correct": is_correct}
    st.session_state[quiz_key] = updated_quiz
    if is_correct:
        added = _append_quiz_answer(field_key, question["correct_option"])
        if added and question.get("expected_output") is not None:
            current_output = st.session_state.expected_test_output
            expected_output = question["expected_output"]
            if current_output:
                st.session_state.expected_test_output = (
                    f"{current_output.rstrip(chr(10))}\n{expected_output}"
                )
            else:
                st.session_state.expected_test_output = expected_output


def _advance_field_quiz(
    quiz_key: str, expected_quiz_id: int, expected_index: int
) -> None:
    """Move forward only if the callback still refers to the active quiz question."""
    quiz = st.session_state[quiz_key]
    if not isinstance(quiz, dict):
        return
    questions = quiz.get("questions")
    index = quiz.get("index")
    if (
        quiz.get("id") != expected_quiz_id
        or index != expected_index
        or not isinstance(index, int)
        or isinstance(index, bool)
        or not isinstance(questions, list)
        or index < 0
        or index + 1 >= len(questions)
        or not isinstance(quiz.get("result"), dict)
    ):
        return

    next_quiz = dict(quiz)
    next_quiz["index"] = index + 1
    next_quiz["result"] = None
    st.session_state[quiz_key] = next_quiz


def _clear_field_quiz(quiz_key: str) -> None:
    """Discard quiz answers when the learner manually edits the related box."""
    st.session_state[quiz_key] = None


def render_field_quiz(field_name: str, field_key: str, quiz_key: str) -> None:
    """Render a multiple-choice quiz that adds only correct answers to its box."""
    if st.button(
        f"🧠 Start {field_name.lower()} quiz",
        key=f"start_{field_key}_quiz",
    ):
        _start_field_quiz(field_name, field_key, quiz_key)

    quiz = st.session_state[quiz_key]
    if not isinstance(quiz, dict):
        return

    if quiz.get("error"):
        if quiz["error"] == "AI tutor not configured":
            st.warning(AI_NOT_CONFIGURED)
        else:
            st.error("Quiz tayar nahi ho saka. Dobara koshish karein.")
        return

    questions = quiz["questions"]
    if not questions:
        clarification = quiz.get("clarification_roman_urdu")
        if clarification:
            st.info(clarification)
        else:
            st.success("Is box ke liye koi nayi maloomat baqi nahi.")
        return

    index = quiz.get("index")
    if (
        not isinstance(index, int)
        or isinstance(index, bool)
        or index < 0
        or index >= len(questions)
        or not isinstance(quiz.get("id"), int)
    ):
        st.session_state[quiz_key] = None
        st.warning("Quiz state reset ho gaya. Dobara quiz shuru karein.")
        return
    question = questions[index]
    st.markdown(f"**Quiz {index + 1}/{len(questions)}:** {question['question']}")
    if quiz["result"] is None:
        st.radio(
            "Apna jawab chunein",
            question["options"],
            key=f"field_quiz_answer_{quiz['id']}_{index}",
        )
        st.button(
            "Check answer",
            key=f"submit_field_quiz_{quiz['id']}_{index}",
            on_click=_submit_field_quiz_answer,
            args=(field_key, quiz_key, quiz["id"], index),
        )
        return

    if quiz["result"]["is_correct"]:
        st.success("Sahi jawab! Isay box mein add kar diya hai.")
    else:
        st.error("Yeh jawab sahi nahi hai.")
        st.markdown(f"**Sahi jawab:** {question['correct_option']}")
    st.markdown(f"**Wajah:** {question['reason_roman_urdu']}")
    if index + 1 < len(questions):
        st.button(
            "Agla sawal",
            key=f"next_field_quiz_{quiz['id']}_{index}",
            on_click=_advance_field_quiz,
            args=(quiz_key, quiz["id"], index),
        )
    else:
        st.success("Quiz mukammal ho gaya.")


def _clear_title_suggestions() -> None:
    """Discard title suggestions when their source statement changes."""
    st.session_state.title_suggestions = []
    for quiz_key in ("inputs_quiz", "outputs_quiz", "rules_quiz", "test_quiz"):
        st.session_state[quiz_key] = None
    st.session_state.ai_suggestion = None
    st.session_state.algorithm_line_review = None
    st.session_state.code_line_suggestion = None
    st.session_state.code_line_review = None
    st.session_state.requirements_recommendation = None
    st.session_state.optimization_recommendation = None


def _use_title_suggestion(title: str) -> None:
    """Copy a clicked suggestion into the title widget before it is rendered."""
    st.session_state.problem_title = title


def _requirements_prompt() -> str:
    """Ask AI for only the data types and concepts this problem actually needs."""
    allowed_types = ", ".join(DATA_TYPES)
    allowed_concepts = ", ".join(CONCEPTS)
    return (
        "Analyze this programming problem for a beginner requirements profile. "
        "Return exactly the necessary data types and programming concepts, "
        "without optional or merely possible choices. Use only the exact "
        f"allowed data type names: {allowed_types}. Use only the exact "
        f"allowed concept names: {allowed_concepts}. If a detail is not "
        "required by the problem, omit it. Give a short reason in Roman Urdu. "
        "Return only JSON with keys data_types, concepts, reason_roman_urdu. "
        "The first two values must be arrays of exact allowed names."
    )


def _parse_requirements(raw: str | None) -> dict | None:
    data = _extract_json_object(raw)
    if (
        data is None
        or not isinstance(data.get("data_types"), list)
        or not isinstance(data.get("concepts"), list)
        or not isinstance(data.get("reason_roman_urdu"), str)
        or not data["reason_roman_urdu"].strip()
        or any(value not in DATA_TYPES for value in data["data_types"])
        or any(value not in CONCEPTS for value in data["concepts"])
        or len(set(data["data_types"])) != len(data["data_types"])
        or len(set(data["concepts"])) != len(data["concepts"])
    ):
        return None
    return {
        "data_types": data["data_types"],
        "concepts": data["concepts"],
        "reason_roman_urdu": data["reason_roman_urdu"].strip()[:500],
    }


def _suggest_requirements() -> None:
    """Apply only the AI-selected requirements before their widgets render."""
    description = _problem_statement_text()
    if not st.session_state.problem_description.strip():
        st.session_state.requirements_recommendation = {
            "error": "Pehle Step 1 mein problem statement paste karein."
        }
        return

    with st.spinner("Selecting only the required concepts and data types…"):
        reply = ask_groq(_requirements_prompt(), description, temperature=0.2)
    recommendation = _parse_requirements(reply) if reply else None
    if not recommendation:
        st.session_state.requirements_recommendation = {
            "error": AI_NOT_CONFIGURED if not reply else "invalid_reply"
        }
        return

    selected_types = set(recommendation["data_types"])
    for data_type in DATA_TYPES:
        st.session_state[f"datatype_{data_type}"] = data_type in selected_types
    st.session_state.concept_selection = recommendation["concepts"]
    st.session_state.requirements_recommendation = recommendation


def _render_requirements_recommendation() -> None:
    recommendation = st.session_state.requirements_recommendation
    if not recommendation:
        return
    if "error" in recommendation:
        if recommendation["error"] == AI_NOT_CONFIGURED:
            st.warning(AI_NOT_CONFIGURED)
        elif recommendation["error"] == "invalid_reply":
            st.error("Requirements ka jawab samajh nahi aya. Dobara koshish karein.")
        else:
            st.info(recommendation["error"])
        return
    st.success("Sirf problem ke liye zaroori selections apply kiye gaye.")
    st.caption(recommendation["reason_roman_urdu"])


def _clear_requirements_recommendation() -> None:
    st.session_state.requirements_recommendation = None


def _optimization_prompt() -> str:
    return (
        "Assess the supplied solution only for this exact problem. Choose the "
        "best justified time and auxiliary-space complexity from the allowed "
        "values. Do not claim optimization is needed unless there is a concrete "
        "improvement. Return an empty note if no specific improvement is "
        "necessary. Allowed values: "
        f"{', '.join(value for value in COMPLEXITY_OPTIONS if value)}. "
        "Return only JSON with keys time_complexity, space_complexity, "
        "reason_roman_urdu, optimization_note. Use exact allowed values and "
        "write the reason in easy Roman Urdu."
    )


def _parse_optimization(raw: str | None) -> dict | None:
    data = _extract_json_object(raw)
    if (
        data is None
        or data.get("time_complexity") not in COMPLEXITY_OPTIONS[1:]
        or data.get("space_complexity") not in COMPLEXITY_OPTIONS[1:]
        or not isinstance(data.get("reason_roman_urdu"), str)
        or not data["reason_roman_urdu"].strip()
        or not isinstance(data.get("optimization_note"), str)
    ):
        return None
    return {
        "time_complexity": data["time_complexity"],
        "space_complexity": data["space_complexity"],
        "reason_roman_urdu": data["reason_roman_urdu"].strip()[:500],
        "optimization_note": data["optimization_note"].strip()[:1000],
    }


def _suggest_optimization() -> None:
    """Fill complexity choices and a problem-specific note from current code."""
    if not st.session_state.python_code.strip():
        st.session_state.optimization_recommendation = {
            "error": "Pehle Step 4 mein apna Python code likhein."
        }
        return
    message = (
        f"Problem:\n{_problem_statement_text()}\n\n"
        f"Pseudocode:\n{st.session_state.pseudocode.strip() or '(empty)'}\n\n"
        f"Python code:\n{st.session_state.python_code.strip()}"
    )
    with st.spinner("Reviewing only the complexity and useful improvements…"):
        reply = ask_groq(_optimization_prompt(), message, temperature=0.2)
    recommendation = _parse_optimization(reply) if reply else None
    if not recommendation:
        st.session_state.optimization_recommendation = {
            "error": AI_NOT_CONFIGURED if not reply else "invalid_reply"
        }
        return

    st.session_state.opt_time_complexity = recommendation["time_complexity"]
    st.session_state.opt_space_complexity = recommendation["space_complexity"]
    st.session_state.opt_notes = recommendation["optimization_note"]
    st.session_state.optimization_recommendation = recommendation


def _render_optimization_recommendation() -> None:
    recommendation = st.session_state.optimization_recommendation
    if not recommendation:
        return
    if "error" in recommendation:
        if recommendation["error"] == AI_NOT_CONFIGURED:
            st.warning(AI_NOT_CONFIGURED)
        elif recommendation["error"] == "invalid_reply":
            st.error("Optimization review samajh nahi aya. Dobara koshish karein.")
        else:
            st.info(recommendation["error"])
        return
    st.success("Problem aur code ke mutabiq assessment apply kar di.")
    st.caption(recommendation["reason_roman_urdu"])


def _clear_optimization_recommendation() -> None:
    st.session_state.optimization_recommendation = None


def _as_list(value) -> list:
    """Coerce a JSON value into a list of strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _parse_field_check(raw: str | None) -> dict | None:
    """Parse the field-check reply into the documented shape; None on failure."""
    data = _extract_json_object(raw)
    if data is None or not isinstance(data.get("is_valid"), bool):
        return None
    issues = _parse_feedback_items(data.get("issues"))
    if issues is None or (data["is_valid"] and issues) or (
        not data["is_valid"] and not issues
    ):
        return None
    return {
        "is_valid": data["is_valid"],
        "issues": issues,
        "suggestions": _as_list(data.get("suggestions")),
    }


def _parse_algorithm_validation(raw: str | None) -> dict | None:
    """Parse the validate-algorithm reply into the documented shape; None on failure."""
    data = _extract_json_object(raw)
    if data is None or not isinstance(data.get("is_correct"), bool):
        return None
    feedback = _parse_feedback_items(data.get("feedback"))
    if feedback is None or (data["is_correct"] and feedback) or (
        not data["is_correct"] and not feedback
    ):
        return None
    return {
        "is_correct": data["is_correct"],
        "feedback": feedback,
        "explanation": str(data.get("explanation") or ""),
    }


def check_field_with_ai(field_name: str, field_value: str, context_data) -> dict | None:
    """Validate one field with Groq.

    Returns {'is_valid': bool, 'issues': [...], 'suggestions': [...]} or None
    when the API is unavailable or the reply cannot be parsed.
    """
    if isinstance(context_data, dict):
        context = "\n".join(
            f"- {name}: {str(value).strip() or '(empty)'}"
            for name, value in context_data.items()
        )
    else:
        context = str(context_data).strip()

    user_message = (
        f"Field to check: {field_name}\n"
        f"Value:\n{field_value.strip() or '(empty)'}\n"
        f"Context from other steps:\n{context or '(none)'}"
    )
    raw = ask_groq(
        FIELD_CHECK_SYSTEM_PROMPT.format(field=field_name),
        user_message,
        temperature=0.2,
    )
    return _parse_field_check(raw)


def _problem_statement() -> dict:
    """Step 1 data as context for other steps."""
    state = st.session_state
    return {
        "Problem description": state.problem_description,
        "Title": state.problem_title,
        "Inputs": state.problem_inputs,
        "Outputs": state.problem_outputs,
        "Rules": state.problem_rules,
    }


def _problem_statement_without(field_name: str) -> dict:
    """Step 1 data except the field currently being checked."""
    return {
        name: value
        for name, value in _problem_statement().items()
        if name != field_name
    }


@st.dialog("🤖 AI field check", width="medium")
def show_field_check(field_name: str, result: dict | None) -> None:
    """Modal showing the result of a field check."""
    if result is None:
        st.warning(AI_NOT_CONFIGURED)
    elif result["is_valid"]:
        st.success(f"🌟 Shabash! Is problem ke liye {field_name} theek hai.")
        if result["suggestions"]:
            st.markdown("**Chhoti si optional tip**")
            for suggestion in result["suggestions"]:
                st.markdown(f"- 💡 {suggestion}")
    else:
        st.error(f"🌱 Achhi koshish! {field_name} ko mil kar theek karte hain.")
        if result["issues"]:
            _render_feedback_items(result["issues"])
        if result["suggestions"]:
            st.markdown("**Agla chhota mashwara**")
            for suggestion in result["suggestions"]:
                st.markdown(f"- {suggestion}")
        st.caption("Har koshish se aap behtar seekh rahe hain! 💪")
    if st.button("Close", use_container_width=True, key="field_check_close"):
        st.rerun()


def run_field_check(field_name: str, field_value: str, context_data) -> None:
    """Ask the AI about one field and show the result in the modal."""
    with st.spinner(f"Checking {field_name} with AI…"):
        result = check_field_with_ai(field_name, field_value, context_data)
    show_field_check(field_name, result)


# ---------------------------------------------------------------------------
# Step 3 Copilot-style AI assistant (suggestion + validation)
# ---------------------------------------------------------------------------
SUGGESTION_SYSTEM_PROMPT = (
    "You are a patient pseudocode tutor. Based on the exact problem and the "
    "learner's existing pseudocode, suggest only the single next logical line. "
    "Do not write multiple steps or repeat existing lines. Use clear English "
    "and pseudocode keywords such as START, READ, SET, IF, FOR, PRINT, END. "
    "Write a short reason in easy Roman Urdu. If the algorithm is complete, "
    "set is_complete to true and next_line to an empty string. Reply only with "
    'JSON: {"next_line":"one line","reason_roman_urdu":"short reason",'
    '"is_complete":false}.'
)

ALGORITHM_LINE_CHECK_SYSTEM_PROMPT = (
    "You are a careful pseudocode tutor. Judge only the learner's latest line "
    "against the exact problem and the preceding lines. Accept equivalent "
    "valid pseudocode. A line is correct only if it is relevant and follows "
    "the preceding algorithm in a logically valid order. Return exactly one "
    "JSON object with boolean is_correct, feedback_roman_urdu, correct_line, "
    "and reason_roman_urdu. Write feedback and reason in easy Roman Urdu. "
    "When the submitted line is correct, set correct_line to an empty string. "
    "When incorrect, put one corrected pseudocode line in English in "
    "correct_line. Do not check or rewrite the whole algorithm. Do not give "
    "the next line when the submitted line is already correct."
)

LINE_REVIEW_SYSTEM_PROMPT = ALGORITHM_LINE_CHECK_SYSTEM_PROMPT

ALGORITHM_VALIDATION_SYSTEM_PROMPT = TEACHING_STYLE + " " + (
    "Understand the problem statement and required inputs before checking the "
    "pseudocode. Reply with ONLY a JSON object (no markdown fences): "
    "{\"is_correct\": true/false, \"feedback\": [{\"mistake_roman_urdu\": "
    "\"what is wrong or missing, in easy Roman Urdu\", \"solution_english\": "
    "\"the exact corrected pseudocode step in English\", \"reason_roman_urdu\": "
    "\"why this step is needed, in easy Roman Urdu\"}], \"explanation\": "
    "\"short overall explanation in Roman Urdu\"}. "
    "Check each pseudocode step against the actual task; do not assume a fixed "
    "number of inputs. Accept valid equivalent steps, and do not report correct "
    "steps again. For each real issue, say what is wrong and give the exact "
    "replacement/additional pseudocode step in English plus its reason in "
    "Roman Urdu. Put the most important correction first. If a needed detail "
    "cannot be inferred, add clarification_roman_urdu with one short question "
    "and omit solution_english instead of guessing. Each issue must have "
    "mistake_roman_urdu and reason_roman_urdu plus exactly one of "
    "solution_english or clarification_roman_urdu. If the pseudocode is "
    "correct, return an empty feedback list."
)


def _problem_statement_text() -> str:
    """Step 1 problem statement as plain text for AI prompts."""
    return "\n".join(
        f"{name}: {str(value).strip() or '(empty)'}"
        for name, value in _problem_statement().items()
    )


def _algorithm_validation_message() -> str:
    """Problem statement + the user's current pseudocode for the validate prompt."""
    return (
        f"Problem statement:\n{_problem_statement_text()}\n\n"
        f"Pseudocode to check:\n{st.session_state.pseudocode.strip() or '(empty)'}"
    )


def _parse_next_algorithm_line(raw: str | None) -> dict | None:
    """Validate a single-line next-step suggestion."""
    data = _extract_json_object(raw)
    if (
        data is None
        or not isinstance(data.get("next_line"), str)
        or not isinstance(data.get("reason_roman_urdu"), str)
        or not data["reason_roman_urdu"].strip()
        or not isinstance(data.get("is_complete"), bool)
    ):
        return None
    line = data["next_line"].strip()
    if (data["is_complete"] and line) or (not data["is_complete"] and not line):
        return None
    if "\n" in line or "\r" in line or len(line) > 240:
        return None
    return {
        "next_line": line[:240],
        "reason_roman_urdu": data["reason_roman_urdu"].strip()[:500],
        "is_complete": data["is_complete"],
    }


def _parse_algorithm_line_review(raw: str | None) -> dict | None:
    """Validate feedback for one submitted pseudocode line."""
    data = _extract_json_object(raw)
    if (
        data is None
        or not isinstance(data.get("is_correct"), bool)
        or not isinstance(data.get("feedback_roman_urdu"), str)
        or not isinstance(data.get("correct_line"), str)
        or not isinstance(data.get("reason_roman_urdu"), str)
    ):
        return None
    feedback = data["feedback_roman_urdu"].strip()
    correct_line = data["correct_line"].strip()
    reason = data["reason_roman_urdu"].strip()
    if not feedback or not reason:
        return None
    if data["is_correct"] and correct_line:
        return None
    if not data["is_correct"] and (
        not correct_line
        or "\n" in correct_line
        or "\r" in correct_line
        or len(correct_line) > 240
    ):
        return None
    return {
        "is_correct": data["is_correct"],
        "feedback_roman_urdu": feedback[:500],
        "correct_line": correct_line[:240],
        "reason_roman_urdu": reason[:500],
    }


def _check_latest_algorithm_line() -> None:
    """Ask AI to check only the most recently entered non-empty line."""
    lines = st.session_state.pseudocode.splitlines()
    nonempty_indices = [index for index, line in enumerate(lines) if line.strip()]
    if not nonempty_indices:
        st.session_state.algorithm_line_review = {
            "error": "Pehle algorithm ki ek line likhein."
        }
        return

    latest_index = nonempty_indices[-1]
    latest_line = lines[latest_index].strip()
    previous_lines = "\n".join(lines[:latest_index]).strip() or "(no previous lines)"
    message = (
        f"Problem statement:\n{_problem_statement_text()}\n\n"
        f"Previous pseudocode lines:\n{previous_lines}\n\n"
        f"Latest line to check:\n{latest_line}"
    )
    with st.spinner("Checking your latest line…"):
        reply = ask_groq(
            ALGORITHM_LINE_CHECK_SYSTEM_PROMPT,
            message,
            temperature=0.2,
        )
    if not reply:
        st.session_state.algorithm_line_review = {"error": AI_NOT_CONFIGURED}
    else:
        result = _parse_algorithm_line_review(reply)
        st.session_state.algorithm_line_review = (
            {"line": latest_line, "result": result}
            if result
            else {"error": "AI ka line review samajh nahi aya. Dobara koshish karein."}
        )


CODE_LINE_SUGGESTION_PROMPT = (
    "You are a Python tutor. From the exact task, pseudocode, and existing "
    "Python code, suggest only the next necessary Python source line. Do not "
    "repeat existing lines or write multiple lines. Preserve correct Python "
    "indentation. If code is complete, return is_complete true and an empty "
    "next_line. Give a brief Roman Urdu reason. Return only JSON with "
    "next_line, reason_roman_urdu, is_complete."
)

CODE_LINE_CHECK_PROMPT = (
    "Check only the latest non-empty Python source line against the exact "
    "problem, pseudocode, and preceding code. Accept valid equivalent Python. "
    "Reply only with JSON fields is_correct (boolean), feedback_roman_urdu, "
    "correct_line, and reason_roman_urdu. Explain in easy Roman Urdu. If correct, "
    "correct_line must be empty. If incorrect, provide exactly one corrected "
    "Python line, preserving indentation. Do not rewrite other lines."
)


def _suggest_next_code_line() -> None:
    """Ask for just the next Python line required by the problem."""
    message = (
        f"Problem:\n{_problem_statement_text()}\n\n"
        f"Pseudocode:\n{st.session_state.pseudocode.strip() or '(empty)'}\n\n"
        f"Current Python code:\n{st.session_state.python_code.rstrip() or '(no lines yet)'}"
    )
    with st.spinner("Suggesting the next required Python line…"):
        reply = ask_groq(CODE_LINE_SUGGESTION_PROMPT, message, temperature=0.2)
    suggestion = _parse_next_algorithm_line(reply) if reply else None
    if suggestion:
        st.session_state.code_line_suggestion = suggestion
    else:
        st.session_state.code_line_suggestion = {
            "error": AI_NOT_CONFIGURED if not reply else "invalid_reply"
        }


def _append_code_suggestion() -> None:
    """Append one suggested Python line before the code editor is rendered."""
    suggestion = st.session_state.code_line_suggestion
    if not isinstance(suggestion, dict) or not suggestion.get("next_line"):
        return
    current = st.session_state.python_code.rstrip()
    st.session_state.python_code = (
        f"{current}\n{suggestion['next_line']}".strip()
    )
    st.session_state.code_line_suggestion = None
    st.session_state.code_line_review = None


def _check_latest_code_line() -> None:
    """Ask AI to check only the last entered Python line."""
    lines = st.session_state.python_code.splitlines()
    nonempty_indices = [index for index, line in enumerate(lines) if line.strip()]
    if not nonempty_indices:
        st.session_state.code_line_review = {
            "error": "Pehle Python code ki ek line likhein."
        }
        return

    latest_index = nonempty_indices[-1]
    latest_line = lines[latest_index]
    message = (
        f"Problem:\n{_problem_statement_text()}\n\n"
        f"Pseudocode:\n{st.session_state.pseudocode.strip() or '(empty)'}\n\n"
        f"Previous Python lines:\n"
        f"{chr(10).join(lines[:latest_index]).rstrip() or '(no previous lines)'}\n\n"
        f"Latest Python line to check:\n{latest_line}"
    )
    with st.spinner("Checking your latest Python line…"):
        reply = ask_groq(CODE_LINE_CHECK_PROMPT, message, temperature=0.2)
    if not reply:
        st.session_state.code_line_review = {"error": AI_NOT_CONFIGURED}
        return
    result = _parse_algorithm_line_review(reply)
    st.session_state.code_line_review = (
        {"line": latest_line, "result": result}
        if result
        else {"error": "AI ka code-line review samajh nahi aya. Dobara koshish karein."}
    )


def _clear_code_line_review() -> None:
    """Clear feedback and suggestions after the code editor changes."""
    st.session_state.code_line_suggestion = None
    st.session_state.code_line_review = None
    st.session_state.optimization_recommendation = None


def _clear_algorithm_line_review() -> None:
    """Clear stale feedback and suggestions as the learner edits their pseudocode."""
    st.session_state.algorithm_line_review = None
    st.session_state.ai_suggestion = None


def _copy_ai_suggestion_to_editor() -> None:
    """Append the suggested single line before the pseudocode widget is rendered."""
    suggestion = st.session_state.ai_suggestion
    if isinstance(suggestion, dict) and suggestion.get("next_line"):
        current = st.session_state.pseudocode.rstrip()
        st.session_state.pseudocode = (
            f"{current}\n{suggestion['next_line']}".strip()
        )
        st.session_state.ai_suggestion = None
        st.session_state.algorithm_line_review = None


@st.dialog("✓ Validate algorithm", width="medium")
def show_algorithm_validation(result: dict | None) -> None:
    """Modal showing the result of '✓ Validate Algorithm'."""
    if result is None:
        st.warning(AI_NOT_CONFIGURED)
    elif result["is_correct"]:
        st.success("✅ Aapka pseudocode problem ko sahi tarah solve karta hai.")
        if result["explanation"]:
            st.markdown(result["explanation"])
        st.markdown("_Shabash — ab ek example ke saath check karein, phir Code Writing par jayein._")
    else:
        st.error("❌ Yeh pseudocode abhi problem ko poori tarah solve nahi karta.")
        if result["feedback"]:
            _render_feedback_items(result["feedback"])
        if result["explanation"]:
            st.markdown(f"**Mukhtasar wajah:** {result['explanation']}")
    if st.button("Close", use_container_width=True, key="algo_validation_close"):
        st.rerun()


# ---------------------------------------------------------------------------
# Flowchart generator (Graphviz DOT) — --- NEW ---
# ---------------------------------------------------------------------------
def build_dot(pseudocode_text: str) -> str:
    """Convert simple pseudocode to a Graphviz DOT string for flowchart rendering."""
    lines = [ln.strip() for ln in pseudocode_text.splitlines() if ln.strip()]
    if not lines:
        return ""

    nodes = []
    edges = []
    prev_id = None

    for i, line in enumerate(lines):
        current_id = f"n{i}"
        upper = line.upper()

        # Pick shape based on leading keyword
        if upper.startswith(("INPUT", "READ", "PRINT", "OUTPUT")):
            shape = "parallelogram"
            style = ', style=filled, fillcolor="#E3F2FD"'
        elif upper.startswith(("IF", "ELSE IF", "ELIF", "ELSE")):
            shape = "diamond"
            style = ', style=filled, fillcolor="#FFF3E0"'
        elif upper.startswith(("FOR", "WHILE", "LOOP")):
            shape = "hexagon"
            style = ', style=filled, fillcolor="#F3E5F5"'
        elif upper.startswith(("END", "STOP", "RETURN", "START", "BEGIN")):
            shape = "doubleoctagon"
            style = ', style=filled, fillcolor="#E8F5E9"'
        else:
            shape = "box"
            style = ', style=filled, fillcolor="#FFFFFF"'

        # Escape quotes inside the label
        safe_label = line.replace('"', '\\"').replace("\n", " ")
        nodes.append(
            f'    {current_id} [shape={shape}, label="{safe_label}"{style}];'
        )

        if prev_id is not None:
            edges.append(f"    {prev_id} -> {current_id};")

        prev_id = current_id

    return "\n".join(
        [
            "digraph G {",
            '    rankdir=TB;',
            '    node [fontname="Helvetica", fontsize=10];',
            '    edge [fontname="Helvetica", fontsize=9];',
            *nodes,
            *edges,
            "}",
        ]
    )


# ---------------------------------------------------------------------------
# Sidebar — wizard stepper (kept intact)
# ---------------------------------------------------------------------------
def render_sidebar() -> None:
    with st.sidebar:
        st.title("🧩 Logic Builder")
        st.caption("A 6-step problem-solving wizard")

        st.divider()

        for index, step_name in enumerate(STEPS):
            is_current = index == st.session_state.current_step
            is_done = index < st.session_state.current_step
            label = f"{index + 1}. {step_name}"
            if is_done:
                label = f"{label}  ✅"
            if st.button(
                label,
                key=f"sidebar_step_{index}",
                use_container_width=True,
                type="primary" if is_current else "secondary",
            ):
                st.session_state.current_step = index
                st.session_state.finished = False
                st.rerun()

        st.divider()

        progress = st.session_state.current_step / len(STEPS)
        st.progress(progress, text=f"Step {st.session_state.current_step + 1} of {len(STEPS)}")

        if st.button("🔄 Reset Wizard", use_container_width=True):
            reset_wizard()
            st.rerun()

        render_sidebar_chat()


# ---------------------------------------------------------------------------
# Sidebar chatbot — Socratic AI tutor
# ---------------------------------------------------------------------------
def render_sidebar_chat() -> None:
    with st.sidebar:
        st.divider()
        st.markdown("#### 💬 AI Tutor")
        if not GROQ_API_KEY:
            st.caption(f"⚠️ {AI_NOT_CONFIGURED}")

        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        prompt = st.chat_input("Ask the Socratic tutor…")
        if prompt:
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)
            with st.chat_message("assistant"):
                with st.spinner("The tutor is thinking…"):
                    reply = ask_groq(tutor_system_prompt(), prompt)
                content = reply if reply else AI_NOT_CONFIGURED
                st.markdown(content)
            st.session_state.messages.append({"role": "assistant", "content": content})


# ---------------------------------------------------------------------------
# Navigation buttons (Back / Next) at the bottom of the main area
# ---------------------------------------------------------------------------
def render_navigation() -> None:
    col_left, _col_mid, col_right = st.columns([1, 1, 1])

    with col_left:
        if st.session_state.current_step > 0 and st.button(
            "← Back", use_container_width=True
        ):
            st.session_state.current_step -= 1
            st.session_state.finished = False
            st.rerun()

    with col_right:
        on_last_step = st.session_state.current_step == len(STEPS) - 1
        next_clicked = (
            st.button("Next →", use_container_width=True, type="primary")
            if not on_last_step
            else False
        )
        finish_clicked = (
            st.button("✅ Finish", use_container_width=True, type="primary")
            if on_last_step
            else False
        )

        if next_clicked and validate_step(st.session_state.current_step):
            st.session_state.current_step += 1
            st.session_state.finished = False
            st.rerun()
        elif finish_clicked and validate_step(st.session_state.current_step):
            st.session_state.finished = True
            st.rerun()

    st.divider()
    st.caption("Logic Builder · 6-step problem-solving wizard")


def validate_step(step_index: int) -> bool:
    """Light per-step validation before allowing Next."""
    if step_index == IDX_PROBLEM and not st.session_state.problem_title.strip():
        st.toast("Please give your problem a title before continuing.", icon="⚠️")
        return False
    if step_index == IDX_ALGORITHM and not st.session_state.pseudocode.strip():
        st.toast("Write a little pseudocode first — even a rough outline helps.", icon="⚠️")
        return False
    if step_index == IDX_CODE and not st.session_state.python_code.strip():
        st.toast("Add some Python code before moving on to Testing.", icon="⚠️")
        return False
    return True


# ---------------------------------------------------------------------------
# Step 1 — Problem Statement
# ---------------------------------------------------------------------------
def step_problem_statement() -> None:
    st.subheader("📝 Problem Statement")
    st.caption(
        "Describe what you are solving: what goes in, what comes out, "
        "and the rules that connect them."
    )

    st.text_area(
        "Paste problem statement",
        key="problem_description",
        height=120,
        persist_state="session",
        on_change=_clear_title_suggestions,
        placeholder=(
            "Paste the complete problem here. For example: Given a number, "
            "print whether it is even or odd."
        ),
    )
    if st.button("✨ Suggest titles", key="suggest_problem_titles"):
        problem_description = st.session_state.problem_description.strip()
        if not problem_description:
            st.warning("Paste a problem statement first.")
            st.session_state.title_suggestions = []
        else:
            with st.spinner("Generating title suggestions…"):
                reply = ask_groq(
                    TITLE_SUGGESTION_SYSTEM_PROMPT,
                    problem_description,
                    temperature=0.4,
                )
            suggestions = _parse_title_suggestions(reply)
            if suggestions:
                st.session_state.title_suggestions = suggestions
            else:
                st.session_state.title_suggestions = []
                if reply:
                    st.error("Could not read the title suggestions. Please try again.")
                else:
                    st.warning(AI_NOT_CONFIGURED)

    if st.session_state.title_suggestions:
        st.caption("Click a suggestion to use it as your title.")
        for index, suggestion in enumerate(st.session_state.title_suggestions):
            st.button(
                suggestion,
                key=f"use_title_suggestion_{index}",
                on_click=_use_title_suggestion,
                args=(suggestion,),
                width="stretch",
            )

    col_title, col_title_check = st.columns([6, 1], vertical_alignment="bottom")
    with col_title:
        st.text_input(
            "Title",
            key="problem_title",
            max_chars=120,
            persist_state="session",
            placeholder="e.g. FizzBuzz — print numbers 1..n with Fizz/Buzz rules",
        )
    with col_title_check:
        if st.button("🤖 Check", key="check_title", use_container_width=True):
            run_field_check(
                "Title",
                st.session_state.problem_title,
                _problem_statement_without("Title"),
            )

    col_in, col_out = st.columns(2, gap="large")
    with col_in:
        field_col, check_col = st.columns([4, 1], vertical_alignment="bottom")
        with field_col:
            st.text_area(
                "Inputs",
                key="problem_inputs",
                height=170,
                persist_state="session",
                on_change=_clear_field_quiz,
                args=("inputs_quiz",),
                placeholder="- n : int — upper bound of the range",
            )
            render_field_quiz("Inputs", "problem_inputs", "inputs_quiz")
        with check_col:
            if st.button("🤖 Check", key="check_inputs", use_container_width=True):
                run_field_check(
                    "Inputs",
                    st.session_state.problem_inputs,
                    _problem_statement_without("Inputs"),
                )
    with col_out:
        field_col, check_col = st.columns([4, 1], vertical_alignment="bottom")
        with field_col:
            st.text_area(
                "Outputs",
                key="problem_outputs",
                height=170,
                persist_state="session",
                on_change=_clear_field_quiz,
                args=("outputs_quiz",),
                placeholder="- list[str] — one result per number",
            )
            render_field_quiz("Outputs", "problem_outputs", "outputs_quiz")
        with check_col:
            if st.button("🤖 Check", key="check_outputs", use_container_width=True):
                run_field_check(
                    "Outputs",
                    st.session_state.problem_outputs,
                    _problem_statement_without("Outputs"),
                )

    col_rules, col_rules_check = st.columns([6, 1], vertical_alignment="bottom")
    with col_rules:
        st.text_area(
            "Rules",
            key="problem_rules",
            height=170,
            persist_state="session",
            on_change=_clear_field_quiz,
            args=("rules_quiz",),
            placeholder=(
                "1. Multiples of 3 become 'Fizz'\n"
                "2. Multiples of 5 become 'Buzz'\n"
                "3. Multiples of both become 'FizzBuzz'"
            ),
        )
        render_field_quiz("Rules", "problem_rules", "rules_quiz")
    with col_rules_check:
        if st.button("🤖 Check", key="check_rules", use_container_width=True):
            run_field_check(
                "Rules",
                st.session_state.problem_rules,
                _problem_statement_without("Rules"),
            )

    render_ai_validation(0)

    title = st.session_state.problem_title.strip()
    if title:
        with st.expander("📄 Problem summary"):
            st.markdown(f"**{title}**")
            st.markdown(
                f"- **Inputs:** {st.session_state.problem_inputs.strip() or '_none yet_'}"
            )
            st.markdown(
                f"- **Outputs:** {st.session_state.problem_outputs.strip() or '_none yet_'}"
            )
            st.markdown(
                f"- **Rules:** {st.session_state.problem_rules.strip() or '_none yet_'}"
            )
    else:
        st.info("Start by giving your problem a title — it is required to continue.")


# ---------------------------------------------------------------------------
# Step 2 — Requirements Analysis
# ---------------------------------------------------------------------------
def step_requirements_analysis() -> None:
    st.subheader("🧾 Requirements Analysis")
    st.caption(
        "Pick the data types your solution will use and the programming concepts it needs."
    )

    st.button(
        "🧠 Select only required items",
        key="suggest_requirements",
        on_click=_suggest_requirements,
    )
    _render_requirements_recommendation()

    col_types, col_concepts = st.columns(2, gap="large")

    with col_types:
        st.markdown("##### Data types")
        for dtype in DATA_TYPES:
            st.checkbox(
                dtype,
                key=f"datatype_{dtype}",
                persist_state="session",
                on_change=_clear_requirements_recommendation,
            )

    with col_concepts:
        st.markdown("##### Concepts needed")
        st.multiselect(
            "Concepts needed",
            CONCEPTS,
            key="concept_selection",
            label_visibility="collapsed",
            persist_state="session",
            on_change=_clear_requirements_recommendation,
            placeholder="Select one or more concepts…",
        )

    render_ai_validation(1)

    selected_types = [d for d in DATA_TYPES if st.session_state[f"datatype_{d}"]]
    selected_concepts = list(st.session_state.concept_selection)

    if not selected_types and not selected_concepts:
        st.info(
            "Select at least one data type or concept to build your requirements profile."
        )
        return

    st.markdown("##### Requirements profile")
    st.markdown(
        f"- **Data types:** {', '.join(f'`{t}`' for t in selected_types) or '_none selected_'}\n"
        f"- **Concepts:** {', '.join(selected_concepts) or '_none selected_'}"
    )
    if st.session_state.problem_inputs.strip():
        st.caption(f"Declared inputs → {st.session_state.problem_inputs.strip()}")


# ---------------------------------------------------------------------------
# Step 3 — Algorithm Design
# ---------------------------------------------------------------------------
def step_algorithm_design() -> None:
    st.subheader("🧠 Algorithm Design")
    st.caption("Write your algorithm as pseudocode — plain language, one step at a time.")

    col_pseudo, col_check, col_validate = st.columns(
        [6, 1, 1], vertical_alignment="bottom"
    )
    with col_pseudo:
        st.text_area(
            "Pseudocode",
            key="pseudocode",
            height=360,
            persist_state="session",
            on_change=_clear_algorithm_line_review,
            placeholder=(
                "START\n"
                "  READ n\n"
                "  FOR i FROM 1 TO n DO\n"
                "    IF i MOD 3 = 0 AND i MOD 5 = 0 THEN PRINT \"FizzBuzz\"\n"
                "    ELSE IF i MOD 3 = 0 THEN PRINT \"Fizz\"\n"
                "    ELSE IF i MOD 5 = 0 THEN PRINT \"Buzz\"\n"
                "    ELSE PRINT i\n"
                "  END FOR\n"
                "END"
            ),
        )
    with col_check:
        if st.button("🤖 Check", key="check_pseudocode", use_container_width=True):
            run_field_check(
                "Pseudocode",
                st.session_state.pseudocode,
                _problem_statement(),
            )
    with col_validate:
        if st.button("✓ Validate Algorithm", key="validate_algorithm",
                     use_container_width=True):
            with st.spinner("Checking your pseudocode…"):
                reply = ask_groq(
                    ALGORITHM_VALIDATION_SYSTEM_PROMPT,
                    _algorithm_validation_message(),
                    temperature=0.2,
                )
            show_algorithm_validation(_parse_algorithm_validation(reply))

    col_next_line, col_check_line = st.columns(2)
    with col_next_line:
        if st.button("💡 Suggest next line", key="get_ai_suggestion"):
            message = (
                f"Problem statement:\n{_problem_statement_text()}\n\n"
                f"Current pseudocode:\n"
                f"{st.session_state.pseudocode.strip() or '(no lines yet)'}"
            )
            with st.spinner("Thinking of the next step…"):
                reply = ask_groq(
                    SUGGESTION_SYSTEM_PROMPT,
                    message,
                    temperature=0.3,
                )
            suggestion = _parse_next_algorithm_line(reply) if reply else None
            if suggestion:
                st.session_state.ai_suggestion = suggestion
            else:
                st.session_state.ai_suggestion = None
                if reply:
                    st.error("AI ki next-line suggestion samajh nahi ayi. Dobara koshish karein.")
                else:
                    st.warning(AI_NOT_CONFIGURED)
    with col_check_line:
        st.button(
            "✅ Check latest line",
            key="check_latest_algorithm_line",
            on_click=_check_latest_algorithm_line,
        )

    line_review = st.session_state.algorithm_line_review
    if line_review:
        if "error" in line_review:
            if line_review["error"] == AI_NOT_CONFIGURED:
                st.warning(line_review["error"])
            else:
                st.error(line_review["error"])
        elif line_review.get("result"):
            result = line_review["result"]
            if result["is_correct"]:
                st.success(f"✅ Sahi line: `{line_review['line']}`")
            else:
                st.error(f"❌ Yeh line sahi nahi: {result['feedback_roman_urdu']}")
                st.markdown("**Sahi line:**")
                st.code(result["correct_line"], language="text")
            st.markdown(f"**Wajah:** {result['reason_roman_urdu']}")
        else:
            st.error("AI ka line review samajh nahi aya. Dobara koshish karein.")

    if st.session_state.ai_suggestion:
        suggestion = st.session_state.ai_suggestion
        if suggestion["is_complete"]:
            st.success("🎉 AI ke mutabiq algorithm mukammal hai.")
        else:
            st.info(
                f"💡 **Agli line:** `{suggestion['next_line']}`\n\n"
                f"**Wajah:** {suggestion['reason_roman_urdu']}"
            )
            st.button(
                "📋 Add suggested line",
                key="copy_ai_suggestion",
                on_click=_copy_ai_suggestion_to_editor,
            )

    render_ai_validation(2)

    with st.expander("💡 Tips for good pseudocode"):
        st.markdown(
            "- Use **UPPERCASE** keywords: `READ`, `SET`, `IF`, `ELSE`, `FOR`, `WHILE`, `RETURN`\n"
            "- Keep **one action per line**\n"
            "- Describe *what* the program does, not Python *syntax*\n"
            "- Think about **edge cases** (empty input, n ≤ 0, duplicates…)"
        )

    if st.session_state.pseudocode.strip():
        st.markdown("##### Preview")
        st.code(st.session_state.pseudocode, language="text")

        # --- NEW: Flowchart rendering ---
        st.markdown("##### Flowchart")
        dot_string = build_dot(st.session_state.pseudocode)
        if dot_string:
            st.graphviz_chart(dot_string)
        else:
            st.warning("Could not generate flowchart from the given pseudocode.")
    else:
        st.info("Sketch your algorithm here — a rough outline is enough to continue.")


# ---------------------------------------------------------------------------
# Step 4 helper — PEP 8 file name suggestion
# ---------------------------------------------------------------------------
FILENAME_SYSTEM_PROMPT = (
    "You name Python files. Given a programming problem, reply with ONLY one "
    "file name in PEP 8 style: lowercase snake_case, 2 to 4 words, descriptive "
    "of the problem, ending in .py (for example: reverse_string.py). No quotes, "
    "no explanation."
)
_FILENAME_STOPWORDS = {
    "a", "an", "the", "of", "to", "in", "for", "and", "or", "with", "from",
    "write", "program", "that", "find", "given", "using", "by", "is", "are",
}
_MAX_FILENAME_STEM = 40


def sanitize_filename(raw: str | None, fallback_text: str = "") -> str:
    """Turn a raw name into a PEP 8 module file name (snake_case, ends in .py)."""
    candidate = (raw or "").strip().splitlines()[0] if (raw or "").strip() else ""
    candidate = candidate.strip(" `'\"")
    if candidate.lower().endswith(".py"):
        candidate = candidate[:-3]
    stem = re.sub(r"[^a-z0-9]+", "_", candidate.lower()).strip("_")

    if not stem:
        words = [
            w for w in re.findall(r"[a-z0-9]+", fallback_text.lower())
            if w not in _FILENAME_STOPWORDS
        ]
        stem = "_".join(words[:4])

    stem = stem[:_MAX_FILENAME_STEM].rstrip("_") or "solution"
    if stem[0].isdigit():
        stem = f"problem_{stem}"
    # Avoid names that would shadow keywords or standard-library modules.
    if keyword.iskeyword(stem) or stem in sys.stdlib_module_names:
        stem = f"{stem}_solution"
    return f"{stem}.py"


def suggest_filename() -> str:
    """Ask the AI for a file name; fall back to one built from the title."""
    title = st.session_state.problem_title
    raw = ask_groq(FILENAME_SYSTEM_PROMPT, _problem_statement_text())
    return sanitize_filename(raw, title)


# ---------------------------------------------------------------------------
# Step 4 — Code Writing
# ---------------------------------------------------------------------------
def step_code_writing() -> None:
    st.subheader("💻 Code Writing")
    st.caption("Translate your pseudocode into Python. It will be executed in Step 5.")

    if st.button("📄 Insert starter template"):
        if st.session_state.python_code.strip():
            st.warning("You already have code — clear the editor first if you want the template.")
        else:
            st.session_state.python_code = (
                '"""Solution generated with Logic Builder."""\n\n\n'
                "def solve(data):\n"
                "    # TODO: translate your pseudocode here\n"
                "    result = []\n"
                "    return result\n\n\n"
                'if __name__ == "__main__":\n'
                "    print(solve([]))\n"
            )
            st.rerun()

    st.text_area(
        "Python code",
        key="python_code",
        height=420,
        persist_state="session",
        on_change=_clear_code_line_review,
        placeholder="def solve(...):\n    ...\n",
    )

    code_actions = st.columns(2)
    with code_actions[0]:
        if st.button("💡 Suggest next Python line", key="suggest_next_code_line"):
            _suggest_next_code_line()
    with code_actions[1]:
        st.button(
            "✅ Check latest Python line",
            key="check_latest_code_line",
            on_click=_check_latest_code_line,
        )

    code_suggestion = st.session_state.code_line_suggestion
    if code_suggestion:
        if code_suggestion.get("error"):
            if code_suggestion["error"] == AI_NOT_CONFIGURED:
                st.warning(AI_NOT_CONFIGURED)
            else:
                st.error("Agli code line ki suggestion samajh nahi ayi. Dobara koshish karein.")
        elif code_suggestion["is_complete"]:
            st.success("AI ke mutabiq required Python code mukammal hai.")
        else:
            st.info(
                f"**Agli required line:** `{code_suggestion['next_line']}`\n\n"
                f"**Wajah:** {code_suggestion['reason_roman_urdu']}"
            )
            st.button(
                "📋 Add suggested Python line",
                key="append_code_suggestion",
                on_click=_append_code_suggestion,
            )

    code_review = st.session_state.code_line_review
    if code_review:
        if "error" in code_review:
            if code_review["error"] == AI_NOT_CONFIGURED:
                st.warning(code_review["error"])
            else:
                st.error(code_review["error"])
        elif code_review.get("result"):
            result = code_review["result"]
            if result["is_correct"]:
                st.success("✅ Latest Python line sahi hai.")
            else:
                st.error(f"❌ {result['feedback_roman_urdu']}")
                st.markdown("**Sahi line:**")
                st.code(result["correct_line"], language="python")
            st.markdown(f"**Wajah:** {result['reason_roman_urdu']}")

    if st.session_state.python_code.strip():
        line_count = len(st.session_state.python_code.splitlines())
        st.caption(f"📝 {line_count} line(s) written — continue to Testing to run it.")
    else:
        st.info("Write your Python solution (or insert the starter template) to continue.")

    if st.button("🏷️ Suggest file name", key="suggest_filename"):
        with st.spinner("Choosing a good file name…"):
            st.session_state.suggested_filename = suggest_filename()

    if st.session_state.suggested_filename:
        name = st.session_state.suggested_filename
        st.caption("PEP 8 file name (lowercase, words joined by underscores):")
        st.code(name, language="text")
        st.download_button(
            f"⬇️ Download {name}",
            data=st.session_state.python_code,
            file_name=name,
            mime="text/x-python",
            key="download_code",
            disabled=not st.session_state.python_code.strip(),
        )


# ---------------------------------------------------------------------------
# Step 5 — Testing
# ---------------------------------------------------------------------------
def run_user_code() -> None:
    """Execute the user's Python snippet in a subprocess and capture the result."""
    code = st.session_state.python_code
    if not code.strip():
        st.session_state.test_ran = False
        return

    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(code)
        script_path = Path(handle.name)

    try:
        process = subprocess.run(
            [sys.executable, "-I", str(script_path)],
            input=st.session_state.test_input,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        st.session_state.test_output = process.stdout
        st.session_state.test_error = process.stderr
        st.session_state.test_returncode = process.returncode
        st.session_state.test_ran = True
    except subprocess.TimeoutExpired:
        st.session_state.test_output = ""
        st.session_state.test_error = "⏱️ Execution timed out after 10 seconds."
        st.session_state.test_returncode = None
        st.session_state.test_ran = True
    finally:
        script_path.unlink(missing_ok=True)


def step_testing() -> None:
    st.subheader("🧪 Testing")
    st.caption("Run your Python code and inspect the results.")

    col_run_in, col_run = st.columns([3, 1], gap="large")
    with col_run_in:
        st.text_area(
            "stdin (optional)",
            key="test_input",
            height=130,
            persist_state="session",
            on_change=_clear_field_quiz,
            args=("test_quiz",),
            placeholder="Text piped to input(), one value per line…",
        )
        render_field_quiz("Test cases", "test_input", "test_quiz")
        st.text_area(
            "Expected output (optional)",
            key="expected_test_output",
            height=100,
            persist_state="session",
            placeholder="Expected program output for this test input…",
        )
    with col_run:
        st.write("")
        if st.button("▶️ Run code", type="primary", use_container_width=True):
            with st.spinner("Running your code…"):
                run_user_code()

    if not st.session_state.test_ran:
        st.info("No results yet — press **Run code** to execute your solution.")
        return

    if st.session_state.test_returncode == 0:
        if st.session_state.expected_test_output.strip():
            actual = st.session_state.test_output.rstrip("\r\n")
            expected = st.session_state.expected_test_output.rstrip("\r\n")
            if actual == expected:
                st.success("✅ Ran successfully and matched the expected output.")
            else:
                st.error("❌ Actual output does not match the expected output.")
                st.markdown("**Expected output**")
                st.code(st.session_state.expected_test_output, language="text")
        else:
            st.success("✅ Ran successfully (exit code 0)")
    else:
        st.error(f"❌ Exited with code {st.session_state.test_returncode}")

    if st.session_state.test_output:
        st.markdown("**Standard output**")
        st.code(st.session_state.test_output, language="text")
    elif st.session_state.test_returncode == 0:
        st.caption("The program produced no output.")

    if st.session_state.test_error:
        st.markdown("**Standard error**")
        st.code(st.session_state.test_error, language="text")

    st.caption("Tip: feed different values through **stdin** and re-run to test edge cases.")


# ---------------------------------------------------------------------------
# Step 6 — Optimization (dummy Big-O estimate)
# ---------------------------------------------------------------------------
COMPLEXITY_OPTIONS = ["", "O(1)", "O(log n)", "O(n)", "O(n log n)", "O(n²)", "O(n³)", "O(2ⁿ)"]


def estimate_big_o(code: str) -> dict:
    """Return a *dummy* Big-O estimate using simple text heuristics (no profiling)."""
    body = [
        line.strip()
        for line in code.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    text = "\n".join(body)

    loops = sum(
        1
        for line in body
        if line.startswith(("for ", "while ", "async for "))
        or " for " in line
        or " while " in line
    )

    time_complexity = {0: "O(1)", 1: "O(n)", 2: "O(n²)"}.get(loops, "O(n³)")

    if ("sorted(" in text or ".sort(" in text) and loops <= 1:
        time_complexity = "O(n log n)"
    elif "bisect" in text and loops == 0:
        time_complexity = "O(log n)"

    builds_data = any(
        token in text for token in ("[", "{", "list(", "dict(", "set(", ".append(")
    )
    space_complexity = "O(n)" if (loops or builds_data) else "O(1)"

    return {"time": time_complexity, "space": space_complexity, "loops": loops}


def step_optimization() -> None:
    st.subheader("⚙️ Optimization")
    st.caption(
        "Review the estimated complexity of your solution and note possible improvements."
    )

    estimate = estimate_big_o(st.session_state.python_code)

    col_time, col_space, col_loops = st.columns(3)
    col_time.metric("⏱️ Estimated time", estimate["time"])
    col_space.metric("💾 Estimated space", estimate["space"])
    col_loops.metric("🔁 Loops detected", estimate["loops"])

    st.caption(
        "💡 This is a *dummy*, heuristic estimate derived from your code — "
        "treat it as a conversation starter, not a measurement."
    )

    st.button(
        "🧠 Assess only the needed complexity",
        key="suggest_optimization",
        on_click=_suggest_optimization,
    )
    _render_optimization_recommendation()

    st.markdown("##### Big-O cheat sheet")
    st.table(
        {
            "Complexity": ["O(1)", "O(log n)", "O(n)", "O(n log n)", "O(n²)", "O(n³)"],
            "Name": [
                "Constant",
                "Logarithmic",
                "Linear",
                "Linearithmic",
                "Quadratic",
                "Cubic",
            ],
            "Example": [
                "Indexing into a list",
                "Binary search",
                "One loop over n items",
                "sorted() in Python",
                "Nested loops over n items",
                "Triple-nested loops",
            ],
        }
    )

    col_t, col_s = st.columns(2, gap="large")
    with col_t:
        st.selectbox(
            "Your time-complexity assessment",
            COMPLEXITY_OPTIONS,
            key="opt_time_complexity",
            persist_state="session",
            on_change=_clear_optimization_recommendation,
            format_func=lambda value: value or "— not set —",
        )
    with col_s:
        st.selectbox(
            "Your space-complexity assessment",
            COMPLEXITY_OPTIONS,
            key="opt_space_complexity",
            persist_state="session",
            on_change=_clear_optimization_recommendation,
            format_func=lambda value: value or "— not set —",
        )

    st.text_area(
        "Optimization notes",
        key="opt_notes",
        height=160,
        persist_state="session",
        on_change=_clear_optimization_recommendation,
        placeholder=(
            "What could be improved? e.g. "
            "\"replace the nested loop with a set lookup → O(n²) becomes O(n)\""
        ),
    )


# ---------------------------------------------------------------------------
# Renderers, in wizard order
# ---------------------------------------------------------------------------
STEP_RENDERERS = [
    step_problem_statement,
    step_requirements_analysis,
    step_algorithm_design,
    step_code_writing,
    step_testing,
    step_optimization,
]


# ---------------------------------------------------------------------------
# Summary screen (shown after Finish)
# ---------------------------------------------------------------------------
def render_summary() -> None:
    st.success("🎉 All six steps complete — nice work!")

    selected_types = [d for d in DATA_TYPES if st.session_state[f"datatype_{d}"]]
    selected_concepts = list(st.session_state.concept_selection)

    with st.expander("1 · Problem Statement", expanded=True):
        st.markdown(f"**{st.session_state.problem_title.strip() or '_untitled_'}**")
        if st.session_state.problem_description.strip():
            st.markdown(f"**Problem:** {st.session_state.problem_description.strip()}")
        st.markdown(f"- **Inputs:** {st.session_state.problem_inputs.strip() or '_none_'}")
        st.markdown(f"- **Outputs:** {st.session_state.problem_outputs.strip() or '_none_'}")
        st.markdown(f"- **Rules:** {st.session_state.problem_rules.strip() or '_none_'}")

    with st.expander("2 · Requirements Analysis", expanded=True):
        st.markdown(
            f"- **Data types:** {', '.join(f'`{t}`' for t in selected_types) or '_none_'}\n"
            f"- **Concepts:** {', '.join(selected_concepts) or '_none_'}"
        )

    with st.expander("3 · Algorithm Design", expanded=True):
        st.code(st.session_state.pseudocode or "_not provided_", language="text")
        # --- NEW: Show flowchart in summary too ---
        if st.session_state.pseudocode.strip():
            st.markdown("**Flowchart:**")
            dot_string = build_dot(st.session_state.pseudocode)
            if dot_string:
                st.graphviz_chart(dot_string)

    with st.expander("4 · Code Writing", expanded=True):
        st.code(st.session_state.python_code or "_not provided_", language="python")

    with st.expander("5 · Testing", expanded=True):
        if st.session_state.test_ran:
            if st.session_state.test_returncode == 0:
                st.success("✅ Last run succeeded (exit code 0)")
            else:
                st.error(
                    f"❌ Last run failed (exit code {st.session_state.test_returncode})"
                )
            if st.session_state.test_output:
                st.code(st.session_state.test_output, language="text")
            if st.session_state.expected_test_output.strip():
                st.markdown("**Expected output:**")
                st.code(st.session_state.expected_test_output, language="text")
            if st.session_state.test_error:
                st.code(st.session_state.test_error, language="text")
        else:
            st.info("The code was not executed in this session.")

    with st.expander("6 · Optimization", expanded=True):
        estimate = estimate_big_o(st.session_state.python_code)
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("⏱️ Time (dummy)", estimate["time"])
        col_b.metric("💾 Space (dummy)", estimate["space"])
        col_c.metric("🔁 Loops", estimate["loops"])
        st.markdown(
            f"- **Your time assessment:** "
            f"{st.session_state.opt_time_complexity or '_not set_'}\n"
            f"- **Your space assessment:** "
            f"{st.session_state.opt_space_complexity or '_not set_'}\n"
            f"- **Notes:** {st.session_state.opt_notes.strip() or '_none_'}"
        )

    st.divider()
    col_back, col_reset = st.columns(2)
    with col_back:
        if st.button("⬅️ Back to the wizard", use_container_width=True):
            st.session_state.finished = False
            st.rerun()
    with col_reset:
        if st.button("🔄 Start over", use_container_width=True):
            reset_wizard()
            st.rerun()


# ---------------------------------------------------------------------------
# Roman Urdu helper chatbot — explains anything the learner did not understand
# ---------------------------------------------------------------------------
URDU_HELPER_HISTORY_TURNS = 6


def urdu_helper_system_prompt() -> str:
    """Patient Roman Urdu teacher prompt, aware of the current wizard step."""
    index = st.session_state.current_step
    step = f"{index + 1}. {STEPS[index]}"
    data = wizard_user_data(index)
    return TEACHING_STYLE + " " + (
        "OVERRIDE for this task: you are a patient helper inside Logic Builder "
        "and you must reply in very easy Roman Urdu (Urdu written in English "
        "letters, like 'Yeh loop baar baar kaam dohrata hai'). Keep programming "
        "words in English, but explain each one in Roman Urdu in brackets. "
        "Unlike a Socratic tutor, you MAY explain the idea directly and "
        "clearly when the learner did not understand something, but do not "
        "write the full solution code for their problem. Use everyday "
        "examples (seb ginna, dukaan se saman kharidna, moze chhantna). "
        "When correcting their work, explain the mistake and reason in Roman "
        "Urdu, but show the exact corrected wording or pseudocode in English. "
        "Guide them in short numbered steps: which box to fill first, what to "
        "write next, and why. Accept valid equivalent answers and do not repeat "
        "correct steps. If required information is unknown, ask one short "
        "question instead of guessing. Use at most 4 key points. "
        "The learner may write in Roman Urdu, Urdu or English; always answer "
        f"in Roman Urdu. Current step: {step}. Learner data: {data}"
    )


def _urdu_helper_message(question: str) -> str:
    """Question plus a few recent turns, since ask_groq takes a single message."""
    recent = st.session_state.urdu_messages[-URDU_HELPER_HISTORY_TURNS:]
    history = "\n".join(
        f"{'Learner' if m['role'] == 'user' else 'Teacher'}: {m['content']}"
        for m in recent
    )
    if not history:
        return question
    return f"Earlier conversation:\n{history}\n\nLearner's new question: {question}"


def render_urdu_helper() -> None:
    """Expander with a Roman Urdu Q&A chat for anything the learner is stuck on."""
    with st.expander("🧑‍🏫 Samajh nahi aya? Roman Urdu mein poochein"):
        if not GROQ_API_KEY:
            st.caption(f"⚠️ {AI_NOT_CONFIGURED}")
        st.caption(
            "Koi bhi baat, AI ka jawab ya step samajh na aye to yahan likhein. "
            "Teacher aasan Roman Urdu mein samjhayega."
        )

        for message in st.session_state.urdu_messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        with st.form("urdu_helper_form", clear_on_submit=True):
            question = st.text_area(
                "Apna sawal likhein",
                key="urdu_helper_question",
                height=90,
                placeholder="Misal: loop kya hota hai? Ya: AI ne jo kaha wo samajh nahi aya…",
            )
            asked = st.form_submit_button("Samjhao 🙏", use_container_width=True)

        if asked and question.strip():
            question = question.strip()
            with st.spinner("Teacher soch raha hai…"):
                reply = ask_groq(
                    urdu_helper_system_prompt(), _urdu_helper_message(question)
                )
            st.session_state.urdu_messages.append({"role": "user", "content": question})
            st.session_state.urdu_messages.append(
                {"role": "assistant", "content": reply if reply else AI_NOT_CONFIGURED}
            )
            st.rerun()


# ---------------------------------------------------------------------------
# Previous steps recap — shown at the top of every step after the first
# ---------------------------------------------------------------------------
def _recap_text(value: str) -> str:
    return value.strip() if value and value.strip() else "_(not filled)_"


def _render_step_recap(index: int) -> None:
    """Read-only summary of what the learner entered in one earlier step."""
    state = st.session_state
    if index == IDX_PROBLEM:
        st.markdown(f"**Title:** {_recap_text(state.problem_title)}")
        st.markdown(f"**Inputs:** {_recap_text(state.problem_inputs)}")
        st.markdown(f"**Outputs:** {_recap_text(state.problem_outputs)}")
        st.markdown(f"**Rules:** {_recap_text(state.problem_rules)}")
    elif index == IDX_REQUIREMENTS:
        selected = [d for d in DATA_TYPES if state[f"datatype_{d}"]]
        st.markdown(f"**Data types:** {', '.join(selected) or '_(none selected)_'}")
        st.markdown(
            f"**Concepts:** {', '.join(state.concept_selection) or '_(none selected)_'}"
        )
    elif index == IDX_ALGORITHM:
        if state.pseudocode.strip():
            st.code(state.pseudocode, language="text")
        else:
            st.markdown("_(not filled)_")
    elif index == IDX_CODE:
        if state.python_code.strip():
            st.code(state.python_code, language="python")
        else:
            st.markdown("_(not filled)_")
    elif index == IDX_TESTING:
        if state.test_ran:
            st.markdown(f"**Exit code:** {state.test_returncode}")
            if state.test_output:
                st.markdown("**Output:**")
                st.code(state.test_output, language="text")
            if state.test_error:
                st.markdown("**Errors:**")
                st.code(state.test_error, language="text")
        else:
            st.markdown("_(the code has not been run yet)_")


def render_previous_steps() -> None:
    """Clearly visible panel with the work from every earlier step."""
    current = st.session_state.current_step
    if current == 0:
        return
    with st.container(border=True):
        st.markdown("### 📚 Your work so far")
        st.caption("Look back at your earlier steps while you work on this one.")
        tabs = st.tabs([f"{i + 1}. {STEPS[i]}" for i in range(current)])
        for index, tab in enumerate(tabs):
            with tab:
                _render_step_recap(index)


# ---------------------------------------------------------------------------
# Main area
# ---------------------------------------------------------------------------
def render_main() -> None:
    if st.session_state.finished:
        render_summary()
        render_urdu_helper()
        return

    step_index = st.session_state.current_step
    st.header(STEPS[step_index])
    st.caption(f"Step {step_index + 1} of {len(STEPS)} · Logic Builder wizard")
    st.divider()

    render_previous_steps()
    STEP_RENDERERS[step_index]()
    render_navigation()
    render_urdu_helper()


# ---------------------------------------------------------------------------
# App entry point
# ---------------------------------------------------------------------------
init_session_state()
render_sidebar()
render_main()