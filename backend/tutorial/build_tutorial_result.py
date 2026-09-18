"""Rebuild and number the bundled 000207 tutorial result."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import fitz


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from engine import analyze_documents, dump_result, _layout_obstacles  # noqa: E402
from label_layout import optimize_result_label_positions  # noqa: E402


ROOT = Path(__file__).resolve().parent
PDF_PATH = ROOT / "000207.pdf"
REFERENCE_ROOT = ROOT / "000207"
RESULT_PATH = ROOT / "result.json"
COMPONENT_PREFIXES = {"valve": "V", "flange": "FL", "support": "SP"}


def number_result(result: dict) -> None:
    for page in sorted(result.get("pages", []), key=lambda item: int(item.get("page") or 0)):
        counters = {"weld": 1, "valve": 1, "flange": 1, "support": 1}
        candidates = sorted(
            (item for item in page.get("candidates", []) if item.get("included", True) is not False),
            key=lambda item: (float(item.get("y") or 0), float(item.get("x") or 0)),
        )
        for candidate in candidates:
            component_type = str(candidate.get("componentType") or "")
            if component_type in COMPONENT_PREFIXES:
                prefix = str(candidate.get("autoNumberPrefix") or COMPONENT_PREFIXES[component_type])
                candidate["number"] = str(
                    candidate.get("referenceLabel") or f"{prefix}{counters[component_type]}"
                )
                counters[component_type] += 1
            else:
                candidate["number"] = str(candidate.get("referenceLabel") or counters["weld"])
                counters["weld"] += 1


def refresh_layout_obstacles(result: dict) -> None:
    """Keep bundled tutorial layout boundaries in sync with the engine."""
    by_page = {int(page.get("page") or 0): page for page in result.get("pages", [])}
    with fitz.open(PDF_PATH) as document:
        for page_number, target in by_page.items():
            if not 1 <= page_number <= document.page_count:
                continue
            cached = (target.get("layoutObstacles") or {}).get("processSegments") or []
            target["layoutObstacles"] = _layout_obstacles(
                document[page_number - 1], {"strong_process_segments": cached}
            )


def reset_cached_label_positions(result: dict) -> None:
    """Force bundled tutorial data to use the current layout algorithm.

    Production reflow deliberately preserves most existing coordinates so a
    user's manual adjustments do not jump.  A rebuilt tutorial cache is a
    different case: its coordinates are generated data, so retaining them
    would mix two algorithm versions.
    """
    layout_keys = (
        "labelX",
        "labelY",
        "labelXNorm",
        "labelYNorm",
        "layoutDiagnostics",
    )
    for page in result.get("pages", []):
        for candidate in page.get("candidates", []):
            for key in layout_keys:
                candidate.pop(key, None)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--number-only", action="store_true")
    args = parser.parse_args()
    if args.number_only:
        result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    else:
        references = sorted(REFERENCE_ROOT.glob("*.pdf"), key=lambda path: path.name.casefold())
        result = analyze_documents(
            PDF_PATH,
            reference_pdfs=references,
            start_page=7,
            end_page=26,
            progress_callback=print,
        )
    number_result(result)
    refresh_layout_obstacles(result)
    reset_cached_label_positions(result)
    result.update({
        "status": "complete",
        "completedPages": len(result.get("pages", [])),
        "progressStage": "complete",
        "progressMessage": "教程预解析结果已就绪",
        "revision": 0,
    })
    optimize_result_label_positions(result)
    dump_result(RESULT_PATH, result)
    print(f"pages={len(result.get('pages', []))}, candidates={sum(len(page.get('candidates', [])) for page in result.get('pages', []))}")


if __name__ == "__main__":
    main()
