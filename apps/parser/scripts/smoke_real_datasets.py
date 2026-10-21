#!/usr/bin/env python3
"""Optional smoke check against public samples in datasets/document-parsing.

Run from the repository root:
    apps/parser/.venv/bin/python apps/parser/scripts/smoke_real_datasets.py

The script only reads the dataset files and sends their bytes through the same
HTTP handler used by uvicorn.  It never writes into the data directory.
"""

from __future__ import annotations

import base64
import sys
import time
from pathlib import Path

PARSER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PARSER_ROOT.parents[1]
sys.path.insert(0, str(PARSER_ROOT))

import app as app_module
from adp_parser.config import Settings
from fastapi.testclient import TestClient

DATASETS = REPO_ROOT / "datasets" / "document-parsing"

SAMPLES = [
    DATASETS / "bizdocbench/documents/policy/Massachusetts_InfoSec.docx",
    DATASETS / "bizdocbench/documents/technical/SOP_52_Digital_Conductivity_Meter_Calibration.docx",
    DATASETS / "bizdocbench/documents/business/BCG_MA_HC_Collaborative_v2.pptx",
    DATASETS
    / "ohr-bench/pdfs/law/RgcResourcesInc_20151216_8-K_EX-10.3_9372751_EX-10.3_Franchise Agreement.pdf",
    DATASETS / "ohr-bench/pdfs/academic/DUDE_158bfc97cd496d509a0ced89c07cb9f9.pdf",
    DATASETS / "ohr-bench/pdfs/manual/DUDE_0302d10b91a0ad03522b5889d1c453c7.pdf",
]


def main() -> int:
    available = [path for path in SAMPLES if path.exists()]
    if not available:
        print("No dataset samples found under", DATASETS)
        return 1

    application = app_module.create_app(Settings(token=None, timeout_seconds=180))
    failures = 0
    with TestClient(application) as client:
        for path in available:
            data = path.read_bytes()
            started = time.time()
            response = client.post(
                "/v1/parse",
                json={
                    "fileName": path.name,
                    "dataBase64": base64.b64encode(data).decode("ascii"),
                },
            )
            elapsed = time.time() - started
            payload = response.json()
            if response.status_code != 200:
                failures += 1
                print(
                    f"FAIL {path.name} status={response.status_code} "
                    f"error={payload.get('error')} message={payload.get('message')}"
                )
                continue
            types: dict[str, int] = {}
            for element in payload["elements"]:
                types[element["type"]] = types.get(element["type"], 0) + 1
            pages = sorted(
                {element["page"] for element in payload["elements"] if "page" in element}
            )
            print(
                f"OK   {path.name} input={len(data) / 1024:.0f}KiB "
                f"time={elapsed:.2f}s engine={payload['engine']} "
                f"chars={len(payload['text'])} elements={len(payload['elements'])} "
                f"pages={pages[:5]} types={types}"
            )
            for warning in payload["warnings"][:4]:
                print("     warning:", warning)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
