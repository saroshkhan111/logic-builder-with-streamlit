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
    "problem_inputs": "",
    "problem_outputs": "",
    "problem_rules": "",
    # Step 2 — Requirements Analysis
    **{f"datatype_{dtype}": False for dtype in DATA_TYPES},
    "concept_selection": [],
    # Step 3 — Algorithm Design
    "pseudocode": "",
    # Step 4 — Code Writing
    "python_code": "",
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
def ask_groq(system_prompt: str, user_message: str) -> str | None:
    """Send one chat completion to Groq. Returns None on any failure."""
    if groq is None or Groq is None or not GROQ_API_KEY:
        return None
    try:
        client = Groq(api_key=GROQ_API_KEY)
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            temperature=0.7,
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
            f"Title: {state.problem_title}",
            f"Inputs: {state.problem_inputs}",
            f"Outputs: {state.problem_outputs}",
            f"Rules: {state.problem_rules}",
        ]
    elif step_index == IDX_REQUIREMENTS:
        selected = [d for d in DATA_TYPES if state[f"datatype_{d}"]]
        parts = [
            f"Data types: {', '.join(selected) or 'none'}",
            f"Concepts: {', '.join(state.concept_selection) or 'none'}",
        ]
    elif step_index == IDX_ALGORITHM:
        parts = [f"Pseudocode:\n{state.pseudocode}"]
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
    return TEACHING_STYLE + " " + (
        "You are a Socratic programming tutor for Logic Builder. "
        "Never give direct answers; guide step-by-step. "
        f"Current step: {step}. User data: {data}"
    )


VALIDATION_SYSTEM_PROMPT = TEACHING_STYLE + " " + (
    "You are a meticulous requirements analyst inside Logic Builder. "
    "Find logical flaws, contradictions, ambiguities and missing edge cases in "
    "the material for this step. Reply with short bullet points only — never "
    "give direct solutions or full code."
)


def render_ai_validation(step_index: int) -> None:
    """'🔍 Validate with AI' button + result (Steps 1-3)."""
    if st.button("🔍 Validate with AI", key=f"validate_ai_{step_index}"):
        with st.spinner("Asking the AI reviewer…"):
            reply = ask_groq(VALIDATION_SYSTEM_PROMPT, wizard_user_data(step_index))
        st.session_state.ai_validation = {
            "step": step_index,
            "reply": reply if reply else AI_NOT_CONFIGURED,
        }

    validation = st.session_state.ai_validation
    if validation and validation["step"] == step_index:
        st.info(validation["reply"])


# ---------------------------------------------------------------------------
# Field-level AI checks (Step 1 fields + Step 3 pseudocode)
# ---------------------------------------------------------------------------
FIELD_CHECK_SYSTEM_PROMPT = TEACHING_STYLE + " " + (
    "You are a reviewer inside Logic Builder, an app where learners write out a "
    "programming problem before coding it. Validate whether the user's '{field}' "
    "is appropriate for a programming problem: clear, specific, unambiguous and "
    "complete enough for a beginner to implement.\n"
    "Reply with ONLY a JSON object (no markdown fences) with exactly these keys:\n"
    '{{"is_valid": true/false, '
    '"issues": ["Issue 1 in simple patient language with everyday example"], '
    '"suggestions": ["Suggestion 1 in simple words with an everyday example"]}}\n'
    "OVERRIDE for this task: the rules about saying one kind thing first and "
    "ending with encouragement do NOT apply here — the app already shows the "
    "kind opening ('Good try!') and the closing encouragement in the dialog "
    "itself. Your issues and suggestions bullets must contain ONLY the helpful "
    "content, written in simple words with everyday examples.\n"
    "Rules: is_valid may be true only when issues is empty. Ground every issue "
    "and suggestion in the given context instead of giving generic advice. "
    "Your issues and suggestions MUST use the patient teaching style: simple "
    "words, short sentences, and everyday examples (apples, shop, boxes). "
    "Write only the bullet contents. Do not add greetings or closing "
    "encouragement — the app shows those separately. "
    "Your explanation, issues, and suggestions fields must use the patient "
    "teaching style: simple words, short sentences, everyday examples."
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
    if data is None or "is_valid" not in data:
        return None
    return {
        "is_valid": bool(data.get("is_valid")),
        "issues": _as_list(data.get("issues")),
        "suggestions": _as_list(data.get("suggestions")),
    }


def _parse_algorithm_validation(raw: str | None) -> dict | None:
    """Parse the validate-algorithm reply into the documented shape; None on failure."""
    data = _extract_json_object(raw)
    if data is None or "is_correct" not in data:
        return None
    return {
        "is_correct": bool(data.get("is_correct")),
        "issues": _as_list(data.get("issues")),
        "missing_steps": _as_list(data.get("missing_steps")),
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
    raw = ask_groq(FIELD_CHECK_SYSTEM_PROMPT.format(field=field_name), user_message)
    return _parse_field_check(raw)


def _problem_statement() -> dict:
    """Step 1 data as context for other steps."""
    state = st.session_state
    return {
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
        st.success(f"🌟 Well done! Your {field_name} looks great!")
        if result["suggestions"]:
            st.markdown("**Bonus ideas**")
            for suggestion in result["suggestions"]:
                st.markdown(f"- 💡 {suggestion}")
    else:
        st.error(f"🌱 Good try! Let's improve your {field_name} together.")
        if result["issues"]:
            st.markdown("**What we can fix**")
            for issue in result["issues"]:
                st.markdown(f"- {issue}")
        if result["suggestions"]:
            st.markdown("**Friendly ideas to try**")
            for suggestion in result["suggestions"]:
                st.markdown(f"- {suggestion}")
        st.caption("You are learning — every try makes you stronger! 💪")
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
SUGGESTION_SYSTEM_PROMPT = TEACHING_STYLE + " " + (
    "You are a pseudocode tutor. Generate clear step-by-step pseudocode that "
    "solves this exact problem. Use UPPERCASE keywords (INPUT, SET, FOR, IF, "
    "ELSE, PRINT). One action per line. Reply with ONLY the pseudocode lines, "
    "no explanations."
)

ALGORITHM_VALIDATION_SYSTEM_PROMPT = TEACHING_STYLE + " " + (
    "Check whether this pseudocode correctly solves the problem. Reply with "
    "ONLY a JSON object: {\"is_correct\": true/false, \"issues\": [...], "
    "\"missing_steps\": [...], \"explanation\": \"...\"} "
    "Your explanation, issues, and suggestions fields must use the patient "
    "teaching style: simple words, short sentences, everyday examples."
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


def _copy_ai_suggestion_to_editor() -> None:
    """on_click callback — runs before widgets are instantiated, so it may
    legally write to the widget-bound 'pseudocode' key."""
    if st.session_state.ai_suggestion:
        st.session_state.pseudocode = st.session_state.ai_suggestion


@st.dialog("✓ Validate algorithm", width="medium")
def show_algorithm_validation(result: dict | None) -> None:
    """Modal showing the result of '✓ Validate Algorithm'."""
    if result is None:
        st.warning(AI_NOT_CONFIGURED)
    elif result["is_correct"]:
        st.success("✅ Your pseudocode correctly solves the problem.")
        if result["explanation"]:
            st.markdown(result["explanation"])
        st.markdown("_Nice work — trace it with one example, then move on to Code Writing._")
    else:
        st.error("❌ This pseudocode does not fully solve the problem yet.")
        if result["issues"]:
            st.markdown("**Issues**")
            for issue in result["issues"]:
                st.markdown(f"- {issue}")
        if result["missing_steps"]:
            st.markdown("**Missing steps**")
            for step in result["missing_steps"]:
                st.markdown(f"- {step}")
        if result["explanation"]:
            st.markdown(f"**Explanation:** {result['explanation']}")
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
                placeholder="- n : int — upper bound of the range",
            )
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
                placeholder="- list[str] — one result per number",
            )
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
            placeholder=(
                "1. Multiples of 3 become 'Fizz'\n"
                "2. Multiples of 5 become 'Buzz'\n"
                "3. Multiples of both become 'FizzBuzz'"
            ),
        )
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

    col_types, col_concepts = st.columns(2, gap="large")

    with col_types:
        st.markdown("##### Data types")
        for dtype in DATA_TYPES:
            st.checkbox(dtype, key=f"datatype_{dtype}", persist_state="session")

    with col_concepts:
        st.markdown("##### Concepts needed")
        st.multiselect(
            "Concepts needed",
            CONCEPTS,
            key="concept_selection",
            label_visibility="collapsed",
            persist_state="session",
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
                )
            show_algorithm_validation(_parse_algorithm_validation(reply))

    # 💡 Get AI Suggestion — below the pseudocode textarea
    if st.button("💡 Get AI Suggestion", key="get_ai_suggestion"):
        with st.spinner("Drafting pseudocode with AI…"):
            suggestion = ask_groq(SUGGESTION_SYSTEM_PROMPT, _problem_statement_text())
        if suggestion and suggestion.strip():
            st.session_state.ai_suggestion = suggestion.strip()
        else:
            st.session_state.ai_suggestion = None
            st.warning(AI_NOT_CONFIGURED)

    if st.session_state.ai_suggestion:
        st.info(
            "💡 **AI suggestion** — paste-ready pseudocode:\n\n"
            f"```\n{st.session_state.ai_suggestion}\n```"
        )
        if st.button(
            "📋 Copy to Editor",
            key="copy_ai_suggestion",
            use_container_width=True,
            on_click=_copy_ai_suggestion_to_editor,
        ):
            st.rerun()

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
        placeholder="def solve(...):\n    ...\n",
    )

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
            placeholder="Text piped to input(), one value per line…",
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
            format_func=lambda value: value or "— not set —",
        )
    with col_s:
        st.selectbox(
            "Your space-complexity assessment",
            COMPLEXITY_OPTIONS,
            key="opt_space_complexity",
            persist_state="session",
            format_func=lambda value: value or "— not set —",
        )

    st.text_area(
        "Optimization notes",
        key="opt_notes",
        height=160,
        persist_state="session",
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