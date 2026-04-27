"""Reference walker for the Simplex clinical-question decision tree.

Drug-agnostic. Given a tree from `client.get_clinical_questions(...)` and a
`decide(node, answers_so_far) -> str` callback, produces the
`{answers, resolved}` payload that `client.fill_pa(clinical_questions=...)`
consumes.

The visibility logic here is the contract — do not reimplement it per project.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

DecideFn = Callable[[dict, dict], str]


def _visible(node: dict, answers: dict) -> bool:
    """Return True if `node` should be rendered/answered given prior answers."""
    v = node.get("visibility")
    if not v:
        return True
    prior = answers.get(v["depends_on"])
    op = v["op"]
    if op == "answered":
        return prior is not None
    val = v.get("value")
    if op == "eq":
        return prior == val
    if op == "in":
        return prior in (val or [])
    raise ValueError(f"unknown visibility op: {op!r}")


def _label_for(node: dict, value: str) -> str:
    """Resolve the human label for an answer. For select questions returns
    the matching option's `label`; otherwise echoes the raw value."""
    for opt in node.get("options") or []:
        if opt.get("value") == value:
            return opt.get("label", value)
    return value


def walk_tree(
    root: dict,
    decide: DecideFn,
) -> dict[str, Any]:
    """Walk the decision tree top-down; return the fill_pa clinical_questions
    payload. Visibility gates are honored — children whose gate is unsatisfied
    are skipped entirely, so no phantom answers from sibling branches.

    `decide(node, answers_so_far)` must return:
      - select  -> one of node["options"][*]["value"]
      - boolean -> "yes" or "no"
      - number  -> stringified number ("30", "27.5")
    """
    answers: dict[str, str] = {}
    resolved: list[dict] = []

    def visit(node: dict) -> None:
        if not _visible(node, answers):
            return
        answer_value = decide(node, answers)
        answers[node["id"]] = answer_value
        resolved.append(
            {
                "question_id": node["id"],
                "prompt": node["prompt"],
                "answer_value": answer_value,
                "answer_label": _label_for(node, answer_value),
            }
        )
        for child in node.get("children") or []:
            visit(child)

    visit(root)
    return {"answers": answers, "resolved": resolved}


def iter_visible_nodes(root: dict, answers: dict) -> Iterable[dict]:
    """Yield every node whose visibility is satisfied given the current
    `answers` dict. Useful when the agent wants to preview the next set of
    questions without committing answers yet (e.g. UI rendering)."""
    if not _visible(root, answers):
        return
    yield root
    for child in root.get("children") or []:
        yield from iter_visible_nodes(child, answers)
