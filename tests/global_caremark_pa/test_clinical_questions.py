"""Fetch + pretty-print the clinical-question decision tree for one
(BIN, PCN, state, drug, ICD) tuple so we can design a drug-agnostic skill
that teaches an agent how to walk any payer's tree and answer it.

Defaults to BIN 004336/PCN ADV/IL + wegovy + E66.9 — change the constants
below to probe a different combo.

Run:
    export SIMPLEX_API_KEY=...
    python tests/global_caremark_pa/test_clinical_questions.py
"""

from __future__ import annotations

import json
import os

from simplex import SimplexClient, SimplexError

BIN = "004336"
PCN = "ADV"
STATE = "IL"
DRUG_NAME = "wegovy"
ICD10 = "E66.9"


def _print_node(node: dict, depth: int = 0) -> None:
    """Walk a decision_tree node. Each node has id/prompt/input_type/options
    and optionally `children` (each child carries a `visibility` rule that
    gates it on a parent answer) and a `citation` back to the policy PDF."""
    indent = "  " * depth
    qid = node.get("id", "")
    prompt = node.get("prompt", "")
    qtype = node.get("input_type", "")
    required = " *" if node.get("required") else ""
    vis = node.get("visibility")
    gate = ""
    if vis:
        gate = f"  [show when {vis.get('depends_on')} {vis.get('op')} {vis.get('value')!r}]"
    print(f"{indent}- [{qid}] ({qtype}){required} {prompt}{gate}")

    for opt in node.get("options") or []:
        print(f"{indent}    • {opt.get('value')!r} — {opt.get('label', '')}")

    citation = node.get("citation")
    if citation:
        print(
            f"{indent}    cite: {citation.get('source_id', '')} p.{citation.get('page', '?')} "
            f"— {citation.get('verbatim_quote', '')[:80]}"
        )

    dur = node.get("approval_duration")
    if dur:
        print(f"{indent}    duration: initial={dur.get('initial_months')}mo renewal={dur.get('renewal_months')}mo")

    for child in node.get("children") or []:
        _print_node(child, depth + 1)


def main() -> None:
    api_key = os.environ.get("SIMPLEX_API_KEY")
    if not api_key:
        raise SystemExit("SIMPLEX_API_KEY environment variable is required")

    api_url = os.environ.get("SIMPLEX_API_URL")
    client = (
        SimplexClient(api_key=api_key, base_url=api_url)
        if api_url
        else SimplexClient(api_key=api_key)
    )

    try:
        result = client.get_clinical_questions(
            bin=BIN,
            state=STATE,
            drug_name=DRUG_NAME,
            icd_code=ICD10,
            pcn=PCN,
        )
    except SimplexError as e:
        raise SystemExit(f"Simplex error: {e}")

    print(f"matched:         {result.get('matched')}")
    print(f"coverage_status: {result.get('coverage_status')}")
    print(f"lob_id:          {result.get('lob_id')}")
    print(f"rationale:       {result.get('rationale')}")
    print()
    print(f"routing: {json.dumps(result.get('routing'), indent=2)}")
    print()

    tree = result.get("decision_tree") or {}
    print(f"decision_tree: {tree.get('decision_tree_id', '')}  v{tree.get('version', '')}")
    print(f"applicable_lobs: {tree.get('applicable_lobs')}")
    print()
    root = tree.get("root")
    if root:
        _print_node(root)

    sources = result.get("sources") or []
    if sources:
        print()
        print("sources:")
        for s in sources:
            print(f"  - {s}")

    # Dump full payload so we can design the agent skill against the real
    # shape (decision-tree branches, option values, citations).
    print()
    print("=" * 60)
    print("RAW JSON")
    print("=" * 60)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
