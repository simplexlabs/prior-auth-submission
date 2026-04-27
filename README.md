# prior-auth-submission

Skills and tests for submitting prior authorizations through the Simplex SDK.

## Layout

- `skills/clinical-questions/` — drug-agnostic skill that teaches an agent how
  to fetch, walk, and answer a payer's PA decision tree, then hand the
  answers to `client.fill_pa(...)`. Includes a reference walker
  (`walk_tree.py`) that should be reused rather than reimplemented.
- `tests/global_caremark_pa/` — end-to-end test scripts:
  - `test_sdk.py` — `search_drugs` → `fill_pa` → `submit_pa` for a
    Caremark commercial member (BIN 004336/ADV/OR). The `submit_pa`
    backend is currently a stub that routes to `INBOUND_EXAMPLE_NUMBER`,
    not a real Caremark fax line.
  - `test_clinical_questions.py` — calls `get_clinical_questions` for one
    `(BIN, PCN, state, drug, ICD)` tuple and pretty-prints the resulting
    decision tree.

## Run

```bash
pip install simplex
export SIMPLEX_API_KEY=...
python tests/global_caremark_pa/test_clinical_questions.py
python tests/global_caremark_pa/test_sdk.py
```
