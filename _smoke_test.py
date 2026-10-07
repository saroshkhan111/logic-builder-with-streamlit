"""Temporary smoke test for main.py using Streamlit's AppTest."""
import os

# Simulate Groq being unavailable (no API key) — the app must degrade gracefully.
os.environ["GROQ_API_KEY"] = ""

from streamlit.testing.v1 import AppTest

at = AppTest.from_file("main.py", default_timeout=30)
at.run()

assert not at.exception, f"Initial render raised: {at.exception}"
assert len(at.sidebar.button) == 7, f"Expected 6 step buttons + reset, got {len(at.sidebar.button)}"
assert len(at.chat_input) == 1, "Sidebar AI tutor chat input should exist"

# --- Field check buttons (🤖 Check next to Step 1 fields) ------------------
check_keys = [b.key for b in at.button if b.label == "🤖 Check"]
assert check_keys == ["check_title", "check_inputs", "check_outputs", "check_rules"], check_keys

at.button(key="check_title").click()
at.run()
assert not at.exception, at.exception
assert any(
    "AI tutor not configured" in w.value for w in at.warning
), [w.value for w in at.warning]  # dialog opens with graceful fallback
at.button(key="field_check_close").click()
at.run()
assert not at.exception, at.exception
assert not any(
    "AI tutor not configured" in w.value for w in at.warning
), "dialog should close after Close is pressed"

# --- Problem statement title suggestions ------------------------------------
at.text_area(key="problem_description").set_value(
    "Given a number, determine whether it is even or odd."
)
at.run()
at.button(key="suggest_problem_titles").click()
at.run()
assert not at.exception, at.exception
assert any(
    "AI tutor not configured" in w.value for w in at.warning
), [w.value for w in at.warning]
assert at.session_state["title_suggestions"] == []

# A displayed suggestion should fill the title widget when clicked.
at.session_state["title_suggestions"] = ["Even or Odd Number Checker"]
at.run()
at.button(key="use_title_suggestion_0").click()
at.run()
assert not at.exception, at.exception
assert at.session_state["problem_title"] == "Even or Odd Number Checker"
assert at.text_input(key="problem_title").value == "Even or Odd Number Checker"
at.text_area(key="problem_description").set_value(
    "Given a price, calculate the discount."
)
at.run()
assert at.session_state["title_suggestions"] == [], "stale suggestions were retained"


def press(label: str) -> None:
    next(b for b in at.button if b.label == label).click()
    at.run()


# --- AI tutor graceful degradation (no API key) -----------------------------
assert any(
    "AI tutor not configured" in c.value for c in at.sidebar.caption
), [c.value for c in at.sidebar.caption]

next(b for b in at.button if b.label == "🔍 Validate with AI").click()
at.run()
assert not at.exception, at.exception
assert any(
    "AI tutor not configured" in i.value for i in at.info
), [i.value for i in at.info]
assert at.session_state["ai_validation"]["step"] == 0

at.chat_input[0].set_value("How should I handle empty input?")
at.run()
assert not at.exception, at.exception
messages = at.session_state["messages"]
assert [m["role"] for m in messages] == ["user", "assistant"], messages
assert messages[1]["content"] == "AI tutor not configured", messages

# Validation reply must not leak into other steps
at.text_input(key="problem_title").set_value("FizzBuzz")
at.run()
press("Next →")
assert at.session_state["current_step"] == 1
assert not any(
    "AI tutor not configured" in i.value for i in at.info
), "Validation reply should only show on its own step"
press("← Back")
assert at.session_state["current_step"] == 0


# Step 1 — Problem Statement
assert at.session_state["current_step"] == 0
at.text_input(key="problem_title").set_value("FizzBuzz")
at.text_area(key="problem_description").set_value("")
at.text_area(key="problem_inputs").set_value("- n: int")
at.text_area(key="problem_outputs").set_value("- list[str]")
at.text_area(key="problem_rules").set_value("1. multiples of 3 -> Fizz")
at.run()
assert not at.exception, at.exception
press("Next →")
assert at.session_state["current_step"] == 1, at.session_state["current_step"]

# Step 2 — Requirements Analysis
at.checkbox(key="datatype_int").set_value(True)
at.checkbox(key="datatype_str").set_value(True)
at.multiselect(key="concept_selection").set_value(["Loops (for/while)"])
at.run()
assert not at.exception, at.exception
assert at.session_state["concept_selection"] == ["Loops (for/while)"]
press("Next →")
assert at.session_state["current_step"] == 2

# Step 3 — Algorithm Design
at.text_area(key="pseudocode").set_value("START\n  READ n\nEND")
at.run()
assert at.button(key="check_pseudocode").label == "🤖 Check", "Pseudocode check button missing"

# --- Step 3 Copilot-style AI assistant (graceful degradation, no API key) ---
at.button(key="get_ai_suggestion").click()
at.run()
assert not at.exception, at.exception
assert any(
    "AI tutor not configured" in w.value for w in at.warning
), [w.value for w in at.warning]
assert at.session_state["ai_suggestion"] is None, "no suggestion should be stored on failure"

# Copy to Editor: inject a suggestion and verify it lands in the editor
at.session_state["ai_suggestion"] = "INPUT n\nPRINT n * 2"
at.run()
assert at.button(key="copy_ai_suggestion").label == "📋 Copy to Editor"
at.button(key="copy_ai_suggestion").click()
at.run()
assert not at.exception, at.exception
assert at.session_state["pseudocode"] == "INPUT n\nPRINT n * 2"
assert at.session_state["ai_suggestion"] == "INPUT n\nPRINT n * 2"

# ✓ Validate Algorithm → dialog fallback + close
at.button(key="validate_algorithm").click()
at.run()
assert not at.exception, at.exception
assert any(
    "AI tutor not configured" in w.value for w in at.warning
), [w.value for w in at.warning]
at.button(key="algo_validation_close").click()
at.run()
assert not at.exception, at.exception
assert not any(
    "AI tutor not configured" in w.value for w in at.warning
), "validate dialog should close"

# Restore the canonical pseudocode for later assertions
at.text_area(key="pseudocode").set_value("START\n  READ n\nEND")
at.run()
press("Next →")
assert at.session_state["current_step"] == 3, at.session_state["current_step"]

# Step 4 — Code Writing (must survive navigating away)
code = (
    "import sys\n"
    "data = sys.stdin.read().split()\n"
    "for x in data:\n"
    "    print(int(x) * 2)\n"
)
at.text_area(key="python_code").set_value(code)
at.run()
press("Next →")
assert at.session_state["current_step"] == 4
assert at.session_state["python_code"] == code, "python_code was lost when leaving Step 4!"
assert at.session_state["problem_title"] == "FizzBuzz", "problem_title was lost!"

# Step 5 — Testing: run the code with stdin
at.text_area(key="test_input").set_value("2 3 4")
at.run()
press("▶️ Run code")
assert not at.exception, at.exception
assert at.session_state["test_ran"] is True
assert at.session_state["test_returncode"] == 0, at.session_state["test_error"]
assert at.session_state["test_output"].split() == ["4", "6", "8"], at.session_state["test_output"]
press("Next →")
assert at.session_state["current_step"] == 5

# Step 6 — Optimization (dummy Big-O)
labels = [m.label for m in at.metric]
assert any("Estimated time" in label for label in labels), labels
assert any("Estimated space" in label for label in labels), labels
at.selectbox(key="opt_time_complexity").set_value("O(n)")
at.text_area(key="opt_notes").set_value("Use a set lookup")
at.run()
assert not at.exception, at.exception

# Navigate back and forth — everything must survive
press("← Back")
assert at.session_state["current_step"] == 4
assert at.session_state["test_ran"] is True
press("← Back")
assert at.session_state["current_step"] == 3
assert at.session_state["pseudocode"] == "START\n  READ n\nEND"

# Jump via sidebar to Optimization, then Finish
at.sidebar.button[5].click()
at.run()
assert at.session_state["current_step"] == 5
assert at.session_state["opt_notes"] == "Use a set lookup"
press("✅ Finish")
assert at.session_state["finished"] is True, at.exception

# Start over from the summary screen
press("🔄 Start over")
assert at.session_state["finished"] is False
assert at.session_state["current_step"] == 0
assert at.session_state["problem_title"] == ""
assert at.session_state["problem_description"] == ""
assert at.session_state["title_suggestions"] == []
assert at.session_state["python_code"] == ""
assert at.session_state["opt_notes"] == ""
assert at.session_state["concept_selection"] == []
assert at.session_state["datatype_int"] is False
assert at.session_state["messages"] == []
assert at.session_state["ai_validation"] is None
assert at.session_state["ai_suggestion"] is None

# Wizard renders fresh after reset
assert not at.exception, at.exception
press("Next →")  # title empty -> validation should block
assert at.session_state["current_step"] == 0, "Validation should block empty title"

# Values must not resurrect after reset; validation must block empty steps
at.text_input(key="problem_title").set_value("Second run")
at.run()
press("Next →")  # 0 -> 1
press("Next →")  # 1 -> 2
press("Next →")  # blocked: pseudocode is empty after reset
assert at.session_state["current_step"] == 2, "Validation should block empty pseudocode"
assert at.session_state["pseudocode"] == ""
assert at.session_state["python_code"] == "", (
    f"Reset value resurrected: {at.session_state['python_code']!r}"
)

print("ALL SMOKE TESTS PASSED")