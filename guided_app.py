"""Guided Solve: a clean MCQ-driven logic builder (Roman Urdu, spoon-feeding)."""

import json
import os

import streamlit as st
from dotenv import load_dotenv

try:
    from groq import Groq, GroqError
except ImportError:  # pragma: no cover
    Groq = None
    GroqError = Exception

load_dotenv()

MODEL = "openai/gpt-oss-120b"
NO_AI = "AI tutor configure nahi hai. GROQ_API_KEY set karein (.env ya Streamlit secrets)."

STAGES = [
    ("samajh", "1️⃣ Problem samjhein", "Inputs, outputs aur rules (jo is problem ke liye zaroori hain)"),
    ("algorithm", "2️⃣ Algorithm banayein", "Pseudocode ki ek ek line"),
    ("code", "3️⃣ Python code likhein", "Code ki ek ek line"),
    ("test", "4️⃣ Test karein", "Test cases aur unke expected results"),
]

STAGE_RULES = {
    "samajh": "Ask only the questions needed to identify the inputs, the outputs and the key rules of THIS problem. Each 'adds' is one short English bullet starting with 'Input:', 'Output:' or 'Rule:'.",
    "algorithm": "Ask 'next pseudocode line kaunsi hogi?' one line at a time, in the correct order. Each 'adds' is exactly that one correct pseudocode line (English).",
    "code": "Ask 'next Python line kaunsi hogi?' one line at a time, in the correct order, following the pseudocode. Each 'adds' is exactly that one correct Python line (keep indentation with spaces).",
    "test": "Ask which test cases (normal, edge) are needed and what the expected result is. Each 'adds' is one line like 'input -> expected output'.",
}

SYSTEM = (
    "Tum ek sabr wala Roman Urdu teacher ho jo complete beginner ko ek programming "
    "problem hal karna sikhata hai. Sawal, options ki wajah aur reason Roman Urdu mein "
    "likho (code/pseudocode English mein). Sirf utne sawal poochho jitne is problem ko "
    "hal karne ke liye zaroori hain, na ziada na kam (aam taur par 2-6). Har sawal ke "
    "2-4 options do, sirf ek sahi, baqi believable lekin ghalat. Reply sirf JSON mein: "
    '{"questions": [{"question": "...", "options": ["..", ".."], "correct": 0, '
    '"reason": "sahi jawab ki wajah Roman Urdu mein", "adds": "..."}]}. '
    "Koi markdown ya extra text nahi."
)


def ask_ai(system: str, user: str) -> str | None:
    key = (os.getenv("GROQ_API_KEY") or "").strip()
    if not key:
        try:
            key = str(st.secrets.get("GROQ_API_KEY", "")).strip()
        except (AttributeError, FileNotFoundError, TypeError, KeyError):
            key = ""
    if not key or Groq is None:
        return None
    try:
        resp = Groq(api_key=key).chat.completions.create(
            model=MODEL,
            temperature=0.4,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except (GroqError, OSError):
        return None
    return resp.choices[0].message.content if resp.choices else None


def parse_questions(raw: str | None) -> list[dict] | None:
    if not raw:
        return None
    text = raw.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    items = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        options = item.get("options")
        correct = item.get("correct")
        question = str(item.get("question", "")).strip()
        if (
            not question
            or not isinstance(options, list)
            or not 2 <= len(options) <= 4
            or isinstance(correct, bool)
            or not isinstance(correct, int)
            or not 0 <= correct < len(options)
        ):
            continue
        result.append(
            {
                "question": question,
                "options": [str(o).strip() for o in options],
                "correct": correct,
                "reason": str(item.get("reason", "")).strip(),
                "adds": str(item.get("adds", "")).rstrip(),
            }
        )
    return result or None


def solution_text() -> str:
    return "\n".join(
        f"{label}\n" + "\n".join(st.session_state.solution[key])
        for key, label, _ in STAGES
        if st.session_state.solution[key]
    )


def init_state() -> None:
    st.session_state.setdefault("problem", "")
    st.session_state.setdefault("stage", -1)
    st.session_state.setdefault("questions", [])
    st.session_state.setdefault("q_index", 0)
    st.session_state.setdefault("checked", False)
    st.session_state.setdefault("error", "")
    st.session_state.setdefault("solution", {key: [] for key, _, _ in STAGES})


def load_stage(stage: int) -> None:
    key = STAGES[stage][0]
    user = (
        f"Problem:\n{st.session_state.problem}\n\n"
        f"Ab tak ka solution:\n{solution_text() or '(abhi kuch nahi)'}\n\n"
        f"Stage: {key}. {STAGE_RULES[key]}"
    )
    questions = parse_questions(ask_ai(SYSTEM, user))
    st.session_state.stage = stage
    st.session_state.q_index = 0
    st.session_state.checked = False
    if questions is None:
        st.session_state.questions = []
        st.session_state.error = NO_AI
    else:
        st.session_state.questions = questions
        st.session_state.error = ""


def start() -> None:
    st.session_state.problem = st.session_state.problem_input.strip()
    st.session_state.solution = {key: [] for key, _, _ in STAGES}
    load_stage(0)


def restart() -> None:
    st.session_state.stage = -1
    st.session_state.questions = []
    st.session_state.error = ""
    st.session_state.solution = {key: [] for key, _, _ in STAGES}


def check_answer() -> None:
    st.session_state.checked = True


def next_question(stage: int, index: int) -> None:
    if st.session_state.stage != stage or st.session_state.q_index != index:
        return
    question = st.session_state.questions[index]
    if question["adds"]:
        st.session_state.solution[STAGES[stage][0]].append(question["adds"])
    st.session_state.checked = False
    if index + 1 < len(st.session_state.questions):
        st.session_state.q_index = index + 1
    elif stage + 1 < len(STAGES):
        load_stage(stage + 1)
    else:
        st.session_state.stage = len(STAGES)


def render_question(stage: int) -> None:
    index = st.session_state.q_index
    questions = st.session_state.questions
    question = questions[index]
    st.progress((index) / len(questions), text=f"Sawal {index + 1} / {len(questions)}")
    st.subheader(question["question"])
    pick = st.radio(
        "Apna jawab chunein",
        range(len(question["options"])),
        format_func=lambda i: question["options"][i],
        index=None,
        key=f"pick_{stage}_{index}",
        disabled=st.session_state.checked,
    )
    if not st.session_state.checked:
        st.button("✅ Jawab check karein", on_click=check_answer, disabled=pick is None)
        return
    if pick == question["correct"]:
        st.success(f"✅ Bilkul sahi! {question['reason']}")
    else:
        st.error(
            f"❌ Ye jawab ghalat hai. Sahi jawab: **{question['options'][question['correct']]}**\n\n"
            f"Wajah: {question['reason']}"
        )
    if question["adds"]:
        st.caption("Ye aap ke solution mein add hoga:")
        st.code(question["adds"], language="python" if STAGES[stage][0] == "code" else None)
    st.button("➡️ Agla", on_click=next_question, args=(stage, index), type="primary")


def build_flowchart(lines: list[str]) -> str:
    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace('"', "'")[:60]

    dot = ["digraph G { rankdir=TB; node [fontname=Helvetica];"]
    dot.append('start [label="Start", shape=oval, style=filled, fillcolor="#c8e6c9"];')
    previous = "start"
    for i, line in enumerate(lines):
        node = f"n{i}"
        is_decision = line.strip().lower().startswith(("if ", "while ", "for ", "repeat"))
        shape = "diamond" if is_decision else "box"
        dot.append(f'{node} [label="{esc(line.strip())}", shape={shape}];')
        dot.append(f"{previous} -> {node};")
        previous = node
    dot.append('end [label="End", shape=oval, style=filled, fillcolor="#ffcdd2"];')
    dot.append(f"{previous} -> end; }}")
    return "\n".join(dot)


def render_flowchart() -> None:
    lines = [ln for ln in st.session_state.solution["algorithm"] if ln.strip()]
    if lines:
        st.graphviz_chart(build_flowchart(lines))


def render_solution() -> None:
    st.success("🎉 Mubarak! Aap ka solution tayyar hai.")
    sol = st.session_state.solution
    for key, label, _ in STAGES:
        st.markdown(f"**{label}**")
        body = "\n".join(sol[key])
        if body:
            st.code(body, language="python" if key == "code" else None)
    st.markdown("**🔀 Flowchart**")
    render_flowchart()
    st.button("🔄 Nayi problem", on_click=restart)


def main() -> None:
    st.set_page_config(page_title="Guided Logic Builder", page_icon="🧠")
    init_state()
    st.title("🧠 Guided Logic Builder")
    stage = st.session_state.stage

    if stage == -1:
        st.text_area(
            "Apni problem statement yahan paste karein",
            key="problem_input",
            height=180,
            placeholder="Misal: Aik list of numbers mein se sab se bara number nikalna hai...",
        )
        st.button(
            "🚀 Shuru karein",
            on_click=start,
            type="primary",
            disabled=not st.session_state.problem_input.strip(),
        )
        if st.session_state.error:
            st.error(st.session_state.error)
        return

    if stage >= len(STAGES):
        render_solution()
        return

    _, label, hint = STAGES[stage]
    st.caption(f"Problem: {st.session_state.problem}")
    st.header(label)
    st.write(hint)
    if st.session_state.error:
        st.error(st.session_state.error)
        st.button("🔄 Dobara koshish", on_click=load_stage, args=(stage,))
        st.button("⬅️ Wapas", on_click=restart)
        return
    render_question(stage)
    with st.sidebar:
        st.subheader("Ab tak ka solution")
        st.code(solution_text() or "(abhi khali)")
        render_flowchart()
        st.button("🔄 Dobara shuru", on_click=restart)


main()
