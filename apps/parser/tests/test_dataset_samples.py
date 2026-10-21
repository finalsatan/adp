from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DATASETS = REPO_ROOT / "datasets" / "document-parsing"

SAMPLES = {
    "docx": DATASETS / "bizdocbench/documents/policy/Massachusetts_InfoSec.docx",
    "pptx": DATASETS / "bizdocbench/documents/business/BCG_MA_HC_Collaborative_v2.pptx",
    "pdf": DATASETS
    / "ohr-bench/pdfs/law/RgcResourcesInc_20151216_8-K_EX-10.3_9372751_EX-10.3_Franchise Agreement.pdf",
}


@pytest.mark.skipif(
    any(not path.exists() for path in SAMPLES.values()),
    reason="public document-parsing datasets are not present",
)
@pytest.mark.parametrize("kind", sorted(SAMPLES))
def test_real_dataset_sample(parse_bytes, client, kind):
    path = SAMPLES[kind]
    response = parse_bytes(client, path.name, path.read_bytes())
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schemaVersion"] == 1
    assert len(payload["text"]) > 1000
    assert payload["elements"]
    assert all(element["order"] == index for index, element in enumerate(payload["elements"], 1))

    if kind == "docx":
        assert any(element["type"] == "heading" for element in payload["elements"])
        assert any(element["type"] == "table_header" for element in payload["elements"])
        assert any(element.get("paragraph") for element in payload["elements"])
    elif kind == "pptx":
        assert payload["elements"][0]["page"] == 1
        assert any(element["type"] == "heading" for element in payload["elements"])
    else:
        pages = {element.get("page") for element in payload["elements"]}
        assert 1 in pages
        assert len(pages) >= 2
