from __future__ import annotations

import csv
from pathlib import Path
import sys

import fitz


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from engine import analyze_documents, dump_result, write_annotated_pdf  # noqa: E402


JOB = ROOT / "data" / "jobs" / "664f444f2cc04c6185760dc0959af1f1"
RESULT_DIR = ROOT / "output" / "results"
PDF_DIR = ROOT / "output" / "pdf"
TEMP_DIR = ROOT / "tmp" / "pdfs"
JSON_PATH = RESULT_DIR / "圆形焊口识别结果_第7-12页.json"
CSV_PATH = RESULT_DIR / "圆形焊口识别汇总_第7-12页.csv"
PDF_PATH = PDF_DIR / "圆形焊口识别标注_第7-12页.pdf"


def main() -> None:
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    result = analyze_documents(
        JOB / "target.pdf",
        reference_pdfs=sorted(JOB.glob("reference-*.pdf")),
        pcf_files=sorted(JOB.glob("source-*.pcf")),
        symbol_config={
            "detectionMode": "placement",
            "placementSymbols": {
                "blackCircleEnabled": True,
                "baseVectorShape": "circle",
                "approximateCircleEnabled": False,
                "includeResearchFallback": False,
            },
        },
        start_page=7,
        end_page=12,
        progress_callback=print,
    )

    all_candidates = []
    rows = []
    for page in result["pages"]:
        unmatched = 0
        encodings: dict[str, int] = {}
        for candidate in page["candidates"]:
            label = str(candidate.get("referenceLabel") or "").strip()
            if not label:
                unmatched += 1
                label = f"P{page['page']}-U{unmatched}"
            candidate["number"] = label
            encoding = str(candidate.get("circleEncoding") or "unknown")
            encodings[encoding] = encodings.get(encoding, 0) + 1
            all_candidates.append(candidate)
        reference = page.get("reference") or {}
        rows.append({
            "page": page["page"],
            "selected_count": page["candidateCount"],
            "selected_labels": ",".join(candidate["number"] for candidate in page["candidates"]),
            "reference_count": reference.get("unfilteredExpectedCalloutCount", ""),
            "circle_encodings": ",".join(f"{key}:{value}" for key, value in sorted(encodings.items())),
            "approximate_circle_count": sum(
                candidate.get("circleEncoding") == "low-edge-approximate-circle"
                for candidate in page["candidates"]
            ),
            "rejected_arrow_risk_labels": ",".join(reference.get("rejectedArrowRiskLabels") or []),
            "unresolved_gap": reference.get("unresolvedCalloutGap", ""),
        })

    dump_result(JSON_PATH, result)
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    temporary_full_pdf = TEMP_DIR / "圆形焊口识别标注_完整临时.pdf"
    write_annotated_pdf(JOB / "target.pdf", temporary_full_pdf, all_candidates)
    with fitz.open(temporary_full_pdf) as annotated, fitz.open() as subset:
        subset.insert_pdf(annotated, from_page=6, to_page=11)
        subset.save(PDF_PATH, garbage=4, deflate=True)

    print(f"JSON: {JSON_PATH}")
    print(f"CSV: {CSV_PATH}")
    print(f"PDF: {PDF_PATH}")
    print([(row["page"], row["selected_count"], row["circle_encodings"]) for row in rows])


if __name__ == "__main__":
    main()
