import os
import subprocess
import sys
import tempfile
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Groq AI tutor — configuration (loaded from the .env file)
# ---------------------------------------------------------------------------
load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = "openai/gpt-oss-120b"
AI_NOT_CONFIGURED = "AI tutor not configured"

try:
    import groq
    from groq import Groq
except ImportError:  # pragma: no cover - groq SDK not installed
    groq = None
    Groq = None

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
    "ai_validation": None,
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
    return (
        "You are a Socratic programming tutor for Logic Builder. "
        "Never give direct answers; guide step-by-step. "
        f"Current step: {step}. User data: {data}"
    )


VALIDATION_SYSTEM_PROMPT = (
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

    st.text_input(
        "Title",
        key="problem_title",
        max_chars=120,
        placeholder="e.g. FizzBuzz — print numbers 1..n with Fizz/Buzz rules",
    )

    col_in, col_out = st.columns(2, gap="large")
    with col_in:
        st.text_area(
            "Inputs",
            key="problem_inputs",
            height=170,
            placeholder="- n : int — upper bound of the range",
        )
    with col_out:
        st.text_area(
            "Outputs",
            key="problem_outputs",
            height=170,
            placeholder="- list[str] — one result per number",
        )

    st.text_area(
        "Rules",
        key="problem_rules",
        height=170,
        placeholder=(
            "1. Multiples of 3 become 'Fizz'\n"
            "2. Multiples of 5 become 'Buzz'\n"
            "3. Multiples of both become 'FizzBuzz'"
        ),
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

    st.text_area(
        "Pseudocode",
        key="pseudocode",
        height=360,
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
        placeholder="def solve(...):\n    ...\n",
    )

    if st.session_state.python_code.strip():
        line_count = len(st.session_state.python_code.splitlines())
        st.caption(f"📝 {line_count} line(s) written — continue to Testing to run it.")
    else:
        st.info("Write your Python solution (or insert the starter template) to continue.")


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
            format_func=lambda value: value or "— not set —",
        )
    with col_s:
        st.selectbox(
            "Your space-complexity assessment",
            COMPLEXITY_OPTIONS,
            key="opt_space_complexity",
            format_func=lambda value: value or "— not set —",
        )

    st.text_area(
        "Optimization notes",
        key="opt_notes",
        height=160,
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
# Main area
# ---------------------------------------------------------------------------
def render_main() -> None:
    if st.session_state.finished:
        render_summary()
        return

    step_index = st.session_state.current_step
    st.header(STEPS[step_index])
    st.caption(f"Step {step_index + 1} of {len(STEPS)} · Logic Builder wizard")
    st.divider()

    STEP_RENDERERS[step_index]()
    render_navigation()


# ---------------------------------------------------------------------------
# App entry point
# ---------------------------------------------------------------------------
init_session_state()
render_sidebar()
render_main()