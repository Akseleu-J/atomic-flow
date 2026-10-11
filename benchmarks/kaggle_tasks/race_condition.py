"""Reference Kaggle task: repair and execute a threaded counter with a race."""

import kaggle_benchmarks as kbench
from kaggle_benchmarks.prompting import ResponseParsingError
from pydantic import BaseModel, Field


class RaceConditionFix(BaseModel):
    fixed_code: str = Field(description="The complete, runnable fixed Python code resolving the race condition.")
    explanation: str = Field(description="Brief explanation of the race condition bug in 30 words or fewer.")


@kbench.task(name="fix race condition in cpython threading", description="Return and execute a race-free threaded counter fix.")
def fix_race_condition_in_cpython_threading(llm):
    prompt = (
        "Fix the race condition in this Python (CPython threading) snippet:\n\n"
        "```python\n"
        "import threading\n"
        "c=0\n"
        "def inc():\n"
        "  global c\n"
        "  for _ in range(100000): c+=1\n"
        "ts=[threading.Thread(target=inc) for _ in range(8)]\n"
        "for t in ts: t.start()\n"
        "for t in ts: t.join()\n"
        "print(c)\n"
        "```\n\n"
        "Provide the complete fixed Python code and explain the bug in 30 words or fewer."
    )
    try:
        response = llm.prompt(prompt, schema=RaceConditionFix)
    except ResponseParsingError as exc:
        kbench.assertions.assert_fail(expectation=f"Response should conform to RaceConditionFix schema: {exc.error}")
        return

    word_count = len(response.explanation.strip().split())
    kbench.assertions.assert_true(word_count <= 30,
                                 expectation=f"Explanation must be 30 words or fewer, got {word_count} words.")
    code = kbench.tools.python.extract_code(response.fixed_code) or response.fixed_code
    result = kbench.tools.python.script_runner.run_code(code)
    kbench.assertions.assert_equal(0, result.exit_code,
                                  expectation=f"Fixed code must run without error. Stderr: {result.stderr}")
    kbench.assertions.assert_contains_regex(r"800000", result.stdout.strip(),
                                           expectation="The race-free program prints 800000.")

    assessment = kbench.assertions.assess_response_with_judge(
        criteria=[
            "The explanation accurately describes why the race condition occurs, such as non-atomic read-modify-write operations or thread preemption.",
            "The explanation is concise and relevant to the bug.",
        ],
        response_text=response.explanation,
        judge_llm=kbench.judge_llm,
    )
    if assessment is None:
        kbench.assertions.assert_fail(expectation="Judge LLM should return a valid assessment of the explanation.")
        return
    for item in assessment.results:
        kbench.assertions.assert_true(item.passed,
                                     expectation=f"Criterion '{item.criterion}' failed: {item.reason}")


fix_race_condition_in_cpython_threading.run(kbench.llm)
