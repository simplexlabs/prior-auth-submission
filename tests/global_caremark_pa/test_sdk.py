"""Fill (and optionally submit) the global Caremark PA via the Simplex Python SDK.

Mirrors the data in mapped.json (Jordan Whitaker, BIN 004336/ADV/OR,
Zepbound KwikPen 2.5mg/0.6ml, ICD E66.3) but goes through the public
SDK end-to-end instead of Reducto directly:

  1. search_drugs() → resolve a drug_slug from a free-text query.
  2. fill_pa()      → server fills + presigns the merged PA PDF.
  3. submit_pa()    → fax it to the resolved Caremark PA number.
                      LEFT COMMENTED OUT for safety.

Run:
    export SIMPLEX_API_KEY=...
    python tests/global_caremark_pa/test_sdk.py
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.request import urlretrieve

from simplex import (
    CaremarkGlobalPaFormQuestions,
    ClinicalAnswers,
    FillPaResponse,
    PatientInfo,
    PrescriberInfo,
    SimplexClient,
    SimplexError,
)


# --- Inputs (inlined from mapped.json) --------------------------------------

BIN = "004336"
PCN = "ADV"
STATE = "OR"
MEMBER_ID = "PSHP8839241076"
ICD10 = "E66.3"
DRUG_QUERY = "wegovy 2.4mg auto-injectors"

PATIENT: PatientInfo = {
    "first_name": "Marco",
    "last_name": "Nocito",
    "dob": "1987-08-22",
    "id": MEMBER_ID,
    "address": "4412 SE STEELE ST",
    "city": "PORTLAND",
    "state": STATE,
    "zip": "97206",
    "home_phone": "5035550142",
    "gender": "F",
}

PRESCRIBER: PrescriberInfo = {
    "first_name": "Evelyn",
    "last_name": "Harper",
    "npi": "1427389056",
    "address": "2130 NW Lovejoy St, Suite 400",
    "city": "Portland",
    "state": STATE,
    "zip": "97210",
    "office_phone": "5035550318",
}

FORM_SPECIFIC_QUESTIONS: CaremarkGlobalPaFormQuestions = {
    "prescription_date": "04/24/2026",
}

# Adult weight-management initiation, BMI ≥ 30 with comorbidity (E66.3 is
# overweight). Reuses the same indication branch as the SDK Wegovy example
# since Zepbound is the same wm_adult_injection clinical decision tree.
CLINICAL_ANSWERS: ClinicalAnswers = {
    "answers": {
        "indication_selector": "wm_adult_injection",
        "wm_adult_injection_phase": "initiation",
        "wm_adult_init_age_18_or_older": "18",
        "wm_adult_init_diet_and_activity": "yes",
        "wm_adult_init_6mo_wm_program": "yes",
        "wm_adult_init_bmi_branch": "bmi_ge_27_with_comorbidity",
        "wm_adult_init_bmi_value": "28",
    },
    "resolved": [
        {
            "question_id": "indication_selector",
            "prompt": "Which indication is this prior authorization for?",
            "answer_value": "wm_adult_injection",
            "answer_label": "Weight management (Adult) — Injection",
        },
        {
            "question_id": "wm_adult_injection_phase",
            "prompt": "Is this initiation or continuation?",
            "answer_value": "initiation",
            "answer_label": "Initiation",
        },
        {
            "question_id": "wm_adult_init_age_18_or_older",
            "prompt": "What is the patient's age in years?",
            "answer_value": "18",
            "answer_label": "18 years",
        },
        {
            "question_id": "wm_adult_init_diet_and_activity",
            "prompt": "Will Zepbound be used with a reduced-calorie diet AND increased physical activity?",
            "answer_value": "yes",
            "answer_label": "Yes",
        },
        {
            "question_id": "wm_adult_init_6mo_wm_program",
            "prompt": "Has the patient participated in a comprehensive weight-management program for at least 6 months?",
            "answer_value": "yes",
            "answer_label": "Yes",
        },
        {
            "question_id": "wm_adult_init_bmi_branch",
            "prompt": "Which BMI criterion does the patient meet?",
            "answer_value": "bmi_ge_27_with_comorbidity",
            "answer_label": "BMI ≥ 27 kg/m² with weight-related comorbidity",
        },
        {
            "question_id": "wm_adult_init_bmi_value",
            "prompt": "What is the patient's baseline BMI (kg/m²)?",
            "answer_value": "28",
            "answer_label": "28 kg/m2",
        },
    ],
}


def _chart_notes_stub() -> bytes:
    """Tiny in-memory PDF so the example runs without a companion file.
    In production attach the real chart notes via a path:
        documents=["/path/to/chart_notes.pdf"]"""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Resources<<>>/Contents 4 0 R>>endobj\n"
        b"4 0 obj<</Length 56>>stream\n"
        b"BT /F1 12 Tf 72 720 Td (Chart notes: BMI 28, comorbid HTN noted) Tj ET\n"
        b"endstream endobj\n"
        b"xref\n0 5\n0000000000 65535 f \n0000000009 00000 n \n"
        b"0000000053 00000 n \n0000000096 00000 n \n0000000174 00000 n \n"
        b"trailer<</Size 5/Root 1 0 R>>\nstartxref\n270\n%%EOF\n"
    )


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
        # 1. Resolve the drug_slug from a free-text query.
        drug_result = client.search_drugs(DRUG_QUERY, limit=5)
        matches = drug_result.get("matches") or []
        if not matches:
            raise SystemExit(f"No drug matches for {DRUG_QUERY!r}.")
        slug = matches[0].get("slug")
        print(f"Resolved drug: {matches[0].get('full_name')} ({slug})")

        # 2. Fill the PA — server stamps the Caremark global PA template.
        response: FillPaResponse = client.fill_pa(
            bin=BIN,
            pcn=PCN,
            state=STATE,
            member_id=MEMBER_ID,
            drug_slug=slug,
            icd10_diagnosis=ICD10,
            patient=PATIENT,
            prescriber=PRESCRIBER,
            form_specific_questions=FORM_SPECIFIC_QUESTIONS,
            clinical_questions=CLINICAL_ANSWERS,
            documents=[("chart_notes.pdf", _chart_notes_stub())],
        )

        print(f"\nsucceeded:  {response.get('succeeded')}")
        print(f"form_id:    {response.get('form_id')}")
        print(f"expires_at: {response.get('expires_at')}")

        signed_url = response.get("signed_url")
        if signed_url:
            out_path = Path(__file__).parent / "results" / f"sdk_{response.get('form_id')}.pdf"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            urlretrieve(signed_url, out_path)
            print(f"\nDownloaded filled PDF: {out_path} ({out_path.stat().st_size} bytes)")

        # 3. Submit the PA. Currently routed via the backend stub to
        #    INBOUND_EXAMPLE_NUMBER, not the real Caremark fax line.
        form_id = response.get("form_id")
        sent = client.submit_pa(form_id)
        print(f"\nsubmit status: {sent.get('status')}")
        print(f"submitted_at:  {sent.get('submitted_at')}")

    except SimplexError as e:
        print(f"Simplex error: {e}")


if __name__ == "__main__":
    main()
