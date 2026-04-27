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
    """Multi-section example chart note that mirrors what a real PA reviewer
    expects to see (demographics, vitals, history, weight-management program,
    assessment/plan, prescriber attestation).

    In production replace this with the real chart notes:
        documents=["/path/to/chart_notes.pdf"]
    """
    import io
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )
    from reportlab.lib import colors

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
    )
    styles = getSampleStyleSheet()
    h = ParagraphStyle("h", parent=styles["Heading2"], spaceAfter=4, textColor=colors.HexColor("#1a3a6c"))
    body = ParagraphStyle("body", parent=styles["BodyText"], leading=14)

    story = []
    story.append(Paragraph("<b>Northwest Endocrinology Clinic</b><br/>2130 NW Lovejoy St, Suite 400, Portland, OR 97210<br/>Phone: (503) 555-0318  |  Fax: (503) 555-0319", body))
    story.append(Spacer(1, 8))
    story.append(Paragraph("<b>OFFICE VISIT — PROGRESS NOTE</b>", styles["Heading1"]))
    story.append(Spacer(1, 6))

    demo = [
        ["Patient:", "Nocito, Marco", "DOB:", "1987-08-22 (38 y/o, M)"],
        ["MRN:", "PSHP8839241076", "Visit date:", "2026-04-24"],
        ["Provider:", "Evelyn Harper, MD (NPI 1427389056)", "Insurance:", "CVS Caremark — BIN 004336 / PCN ADV"],
    ]
    t = Table(demo, colWidths=[0.9 * inch, 2.2 * inch, 0.9 * inch, 2.7 * inch])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 12))

    story.append(Paragraph("Chief complaint", h))
    story.append(Paragraph("Obesity with weight-related comorbidities; failed comprehensive lifestyle intervention. Requesting initiation of GLP-1 receptor agonist (Wegovy) for chronic weight management.", body))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Vitals (today)", h))
    vitals = [
        ["Height", "5'10\" (178 cm)", "BP", "138/86 mmHg"],
        ["Weight", "195 lb (88.5 kg)", "HR", "78 bpm, regular"],
        ["BMI", "28.0 kg/m²", "Waist circumference", "104 cm"],
    ]
    vt = Table(vitals, colWidths=[1.0 * inch, 2.0 * inch, 1.6 * inch, 2.1 * inch])
    vt.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(vt)
    story.append(Spacer(1, 10))

    story.append(Paragraph("Active diagnoses", h))
    story.append(Paragraph(
        "• <b>E66.9</b> — Obesity, unspecified (BMI 28, weight-related comorbidities present)<br/>"
        "• <b>I10</b> — Essential hypertension (on lisinopril 20 mg daily; SBP 130–140 range)<br/>"
        "• <b>E78.5</b> — Hyperlipidemia (LDL 142 mg/dL on most recent lipid panel, 02/2026)<br/>"
        "• <b>E11.9</b> — Pre-diabetes (HbA1c 6.0% on 03/2026; not yet meeting T2DM criteria)",
        body,
    ))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Weight-management history (≥ 6 months structured program)", h))
    story.append(Paragraph(
        "Patient enrolled in our clinic's structured weight-management program from <b>09/2025 through 04/2026 (7 months)</b>. Components:<br/>"
        "• Reduced-calorie diet — Mediterranean pattern, 1500–1700 kcal/day, supervised by RD (visits 09/15, 11/03, 12/19, 02/06, 03/27).<br/>"
        "• Increased physical activity — graded exercise plan reaching 180 min/week moderate-intensity aerobic + 2× weekly resistance training; logged via Fitbit (avg 8,200 steps/day for the trailing 12 weeks).<br/>"
        "• Behavioral modification — 6 cognitive-behavioral group sessions (10/02, 10/16, 11/13, 12/04, 01/15, 02/12).<br/>"
        "Outcome: weight reduced from 207 lb (BMI 29.7) to 195 lb (BMI 28.0) — a <b>5.8% reduction</b>. Patient reports plateau over the past 8 weeks despite continued adherence.",
        body,
    ))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Prior pharmacotherapy", h))
    story.append(Paragraph(
        "• <b>Phentermine 37.5 mg</b> daily — trialed 06/2025–08/2025; discontinued due to elevated BP (peak 152/94) and tachycardia.<br/>"
        "• <b>Orlistat 120 mg</b> tid — trialed 04/2025–06/2025; discontinued due to intolerable GI side effects (steatorrhea).",
        body,
    ))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Assessment", h))
    story.append(Paragraph(
        "38-year-old male with obesity (BMI 28) and multiple weight-related comorbidities (HTN, hyperlipidemia, pre-diabetes). Patient meets BMI ≥ 27 with weight-related comorbidity criterion per the Caremark UM policy for Wegovy initiation. Has completed > 6 months of structured comprehensive weight-management with reduced-calorie diet AND increased physical activity. Two prior pharmacotherapy trials failed. Wegovy is medically necessary to achieve clinically meaningful weight loss and reduce cardiometabolic risk.",
        body,
    ))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Plan", h))
    story.append(Paragraph(
        "1. <b>Initiate Wegovy (semaglutide) 0.25 mg SC weekly × 4 weeks</b>, then escalate per FDA-labeled titration to maintenance dose 2.4 mg SC weekly. Continue reduced-calorie diet and physical activity.<br/>"
        "2. Follow-up at 12 weeks for tolerability, weight, BP, and lipids; re-assess at 6 months for ≥ 5% baseline weight reduction (continuation criterion).<br/>"
        "3. Submit prior authorization to CVS Caremark.",
        body,
    ))
    story.append(Spacer(1, 14))

    story.append(Paragraph(
        "<i>I attest that the above clinical findings are accurate and that the requested therapy is medically necessary.</i><br/><br/>"
        "<b>Evelyn Harper, MD</b><br/>NPI 1427389056  |  Endocrinology  |  04/24/2026",
        body,
    ))

    doc.build(story)
    return buf.getvalue()


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
