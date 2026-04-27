---
name: simplex-clinical-questions
description: "Fetch a payer's prior-auth clinical-question decision tree via Simplex `/get_clinical_questions`, walk it correctly (honoring visibility gates), and produce the `clinical_questions` payload that `client.fill_pa(...)` consumes. Use when filling a PA where the agent has the patient's clinical info in context (BMI, weight history, comorbidities, prior therapies, lab values) and needs to answer the payer's questionnaire."
allowed-tools: Bash, Read, Grep, Glob
---

# Simplex clinical-question decision-tree skill

Use this skill when you have:

- The patient's plan card fields: `bin`, `pcn`, `state` (and ideally `member_id`).
- The drug brand or generic (e.g. `wegovy`, `zepbound`, `mounjaro`, `ozempic`).
- The ICD-10 diagnosis (e.g. `E66.9`, `E11.9`, `K76.81`).
- Enough clinical context to answer the policy's UM criteria (BMI, comorbidities, prior 6-mo weight-management program, age, baseline labs, etc.).

The flow is always:

1. Call `client.get_clinical_questions(...)` — get the **decision tree**.
2. Walk the tree top-down, **only answering nodes whose `visibility` rule is satisfied** by your answers so far.
3. Hand the resulting `{answers, resolved}` payload to `client.fill_pa(clinical_questions=...)`.

Do **not** hand-write `answers` from training data. The tree is policy-versioned per payer per drug and changes — always fetch it fresh.

## 1. Fetch the tree

```python
from simplex import SimplexClient

client = SimplexClient(api_key=os.environ["SIMPLEX_API_KEY"])

result = client.get_clinical_questions(
    bin="004336",
    pcn="ADV",          # optional but improves LOB precision
    state="IL",
    drug_name="wegovy",  # brand or generic; matched case-insensitively on substring
    icd_code="E66.9",
    member_id="...",     # optional; tiebreaks shared-processor BINs
)
```

### Coverage gate

Before walking, check `result["coverage_status"]`:

| value | meaning | what to do |
|---|---|---|
| `"resolved"` | Tree returned in `result["decision_tree"]` | proceed |
| `"excluded"` | Drug is statutorily excluded for this LOB (e.g. weight-loss drugs under Part D) | abort PA, surface the `rationale` to the user |
| `"unmapped"` | LOB matched but no tree exists for this drug/ICD | fall back to a generic global PA without a clinical answer set |

If `result["matched"]` is `False`, the BIN/PCN/state combo wasn't recognized — surface that, don't guess.

## 2. Decision-tree contract

`result["decision_tree"]` looks like:

```json
{
  "decision_tree_id": "caremark_global_antiobesity_wegovy_2025",
  "version": "2025",
  "applicable_lobs": ["aetna_caremark_4774c", ...],
  "source_policy": { "source_url": "...", "sha256": "..." },
  "root": { ...node... }
}
```

Each **node** has:

| field | meaning |
|---|---|
| `id` | Question ID — use as the key in `answers`. |
| `prompt` | Human-readable question text. |
| `input_type` | `"select"` \| `"boolean"` \| `"number"`. |
| `required` | If `true`, you must answer when visible. |
| `options` | For `select` only — `[{value, label}, ...]`. Answer must be one of the `value`s. |
| `children` | Nested follow-up questions, each with their own `visibility` rule. |
| `visibility` | Gate that decides whether to render/answer this node. |
| `citation` | `{source_id, source_url, citation_url, page, verbatim_quote}` — the policy text behind this question. |
| `approval_duration` | `{initial_months, renewal_months}` — usually on the phase node (initiation/continuation). |

### Visibility rules — the rule you cannot break

A node's `visibility` is `{depends_on, op, value}`. Evaluate it against the answers you've collected so far:

| `op` | semantic |
|---|---|
| `eq` | `answers[depends_on] == value` |
| `in` | `answers[depends_on] in value` (where `value` is a list) |
| `answered` | `depends_on` has any non-null answer |

**Never include an answer for a node whose visibility gate is unsatisfied.** Phantom answers from a different branch (e.g. answering both `mash_adult_*` and `wm_adult_*` because you walked both) will trip server-side validation in `fill_pa`.

If a node has no `visibility`, it's always shown.

### Input-type rules

- `select` → answer is the option `value` string. Match exactly; the server does not coerce labels.
- `boolean` → answer is the string `"yes"` or `"no"` (not `True`/`False`).
- `number` → answer is the value as a string (`"30"`, not `30`). Use whole numbers when the question is age/months; one decimal place is fine for BMI.

## 3. Walk the tree

```python
def walk(node: dict, answers_so_far: dict, decide) -> dict:
    """Recurse the tree; return {question_id: answer_value} for every node
    whose visibility gate is satisfied. `decide(node, answers_so_far)` returns
    the agent's answer for one node (string)."""
    answers = dict(answers_so_far)

    def visible(n: dict) -> bool:
        v = n.get("visibility")
        if not v:
            return True
        prior = answers.get(v["depends_on"])
        op, val = v["op"], v["value"]
        if op == "eq":       return prior == val
        if op == "in":       return prior in (val or [])
        if op == "answered": return prior is not None
        raise ValueError(f"unknown visibility op: {op}")

    if not visible(node):
        return {}

    answers[node["id"]] = decide(node, answers)

    for child in node.get("children") or []:
        answers.update(walk(child, answers, decide))
    return answers
```

The `decide()` callback is where the agent uses its clinical context. For each node the agent should:

1. Look at `node["prompt"]` and `node["input_type"]`.
2. Pick the answer from the patient's chart / your conversation context.
3. Validate against `node["options"]` if `input_type == "select"`.
4. If you genuinely cannot answer (data not in context), do not guess — stop and ask the user.

## 4. Build the `clinical_questions` payload

`fill_pa(clinical_questions=...)` wants:

```python
{
    "answers": { question_id: answer_value, ... },          # required
    "resolved": [
        {
            "question_id": "...",
            "prompt": "...",
            "answer_value": "...",
            "answer_label": "...",  # for select: the option's label; for boolean/number: a human string
        },
        ...
    ],
}
```

`resolved` mirrors `answers` but in the order you walked the tree, with the human-readable label preserved. It's used for audit + chart-note rendering, so it's worth populating even though `answers` is what drives the form fill.

```python
def to_resolved(node: dict, answer_value: str) -> dict:
    label = answer_value
    for opt in node.get("options") or []:
        if opt["value"] == answer_value:
            label = opt["label"]
            break
    return {
        "question_id": node["id"],
        "prompt": node["prompt"],
        "answer_value": answer_value,
        "answer_label": label,
    }
```

## 5. Cite back to policy in chart notes

Every node carries a `citation` with `verbatim_quote` and `citation_url` (deep-linked to the PDF page). When the agent writes chart-notes for the PA submission, it should quote the policy text from the citation rather than paraphrase — payer reviewers are looking for the exact phrasing.

## 6. Reference: end-to-end skeleton

```python
# 1. fetch tree
res = client.get_clinical_questions(bin=BIN, pcn=PCN, state=STATE,
                                    drug_name=DRUG, icd_code=ICD)
if res["coverage_status"] != "resolved":
    raise SystemExit(f"PA not viable: {res['rationale']}")

tree = res["decision_tree"]["root"]

# 2. walk
trace = []  # ordered list of (node, answer)
def decide(node, answers):
    answer = AGENT_PICKS_FROM_CONTEXT(node, answers)  # boolean=yes/no, select=value, number=str
    trace.append((node, answer))
    return answer

answers = walk(tree, {}, decide)

# 3. submit
clinical_questions = {
    "answers": answers,
    "resolved": [to_resolved(n, a) for n, a in trace],
}
client.fill_pa(..., clinical_questions=clinical_questions)
```

## Common mistakes

- **Answering both branches.** If you walk past a `visibility` gate that isn't satisfied, you'll write garbage answers from a sibling branch. Always check visibility *before* recursing.
- **Returning option labels instead of values.** `select` answers must be the `value` (e.g. `"bmi_ge_30"`), never the label (`"BMI ≥ 30 kg/m²"`).
- **Booleans as Python booleans.** Send `"yes"` / `"no"` strings.
- **Caching the tree across runs.** Trees are versioned per policy update — re-fetch each PA so you don't submit stale criteria.
- **Hand-writing `answers` from training data.** The questions and IDs change per payer per drug per year. Always fetch.

## Reference walker

A complete reference walker (with visibility evaluation, label resolution, and the `{answers, resolved}` shape) lives at `walk_tree.py` in this skill folder. Copy or import it; don't reinvent the visibility logic per project.
