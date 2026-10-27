"""批量运行解析引擎并生成离线对比报告。"""

from __future__ import annotations

import argparse
import csv
import json
import multiprocessing as mp
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.convert import Converter
from app.engines.base import ParseContext
from app.engines.registry import bootstrap, get_engine
from app.filecheck import check_file
from app.filetypes import type_for_ext
from app.pool import _worker_main
from app.postprocess import postprocess
from bench.report import write_report

VARIANTS = {
    "docling": ("docling", {}),
    "docling-force": ("docling", {"scan_mode": "force"}),
    "pypdfium": ("pypdfium", {}),
    "markitdown": ("markitdown", {}),
    "mineru-flash": ("mineru", {"tier": "flash"}),
    "mineru-basic": ("mineru", {"tier": "basic"}),
    "builtin": ("builtin", {}),
}


class LocalStorage:
    def __init__(self, root):
        self.root = root

    def upload_file(self, source, key):
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def worker_main(tasks, results, force):
    if force:
        from docling.datamodel.base_models import InputFormat

        from app.engines.docling_engine import DoclingEngine

        # X06 的公开 warmup 只预热 auto；force 需预热同一个私有转换器缓存。
        def warmup(self):
            self._get_converter("force").initialize_pipeline(InputFormat.PDF)

        DoclingEngine.warmup = warmup
    _worker_main(0, tasks, results, False)


def heading_counts(document):
    headings = {}
    for item in document.texts:
        label = str(item.label.value)
        if label == "title":
            headings["title"] = headings.get("title", 0) + 1
        elif label == "section_header":
            level = str(getattr(item, "level", 1))
            headings[level] = headings.get(level, 0) + 1
    return headings


def execute(source, variant, output, conversion_s):
    engine_name, params = VARIANTS[variant]
    ext = source.suffix.lower().lstrip(".")
    file_type = type_for_ext(ext)
    row = {"status": "失败", "error_code": "", "conversion_s": conversion_s, "warmup_s": 0}
    bootstrap()
    engine = get_engine(engine_name)
    if not engine.supports(file_type, ext):
        row.update(status="不支持", error_code="UNSUPPORTED_TYPE")
        return row
    with tempfile.TemporaryDirectory(prefix="adp-bench-") as tmp:
        work = Path(tmp)
        worker = None
        try:
            check_file(source, ext)
            if engine_name == "mineru":
                ctx = ParseContext(
                    input_path=str(source),
                    file_type=file_type,
                    ext=ext,
                    engine_params=params,
                    preprocess={},
                    table={},
                    work_dir=str(work),
                    report=lambda _done, _total: None,
                )
                start = time.monotonic()
                try:
                    result = engine.parse(ctx)
                finally:
                    row["duration_s"] = time.monotonic() - start
                    row["peak_memory_mb"] = "见 mineru 容器"
            else:
                context = mp.get_context("spawn")
                tasks, results = context.Queue(), context.Queue()
                worker = context.Process(
                    target=worker_main, args=(tasks, results, variant == "docling-force")
                )
                start = time.monotonic()
                worker.start()
                ready = results.get(timeout=900)
                row["warmup_s"] = time.monotonic() - start
                if ready["type"] != "ready":
                    raise RuntimeError("worker 未就绪")
                start = time.monotonic()
                tasks.put(
                    dict(
                        job_id="bench",
                        input_path=str(source),
                        file_type=file_type,
                        ext=ext,
                        engine=engine_name,
                        engine_params=params,
                        preprocess={},
                        table={},
                        work_dir=str(work),
                        remote=None,
                    )
                )
                while True:
                    message = results.get(timeout=1800)
                    if message["type"] in {"done", "error"}:
                        break
                row["duration_s"] = time.monotonic() - start
                tasks.put(None)
                worker.join(timeout=30)
                if worker.is_alive():
                    worker.terminate()
                    worker.join()
                rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
                row["peak_memory_mb"] = (
                    "见 mineru 容器"
                    if engine_name == "mineru"
                    else round(rss / (1024 * 1024 if sys.platform == "darwin" else 1024), 2)
                )
                if message["type"] == "error":
                    row.update(
                        status="不支持" if message["code"] == "UNSUPPORTED_TYPE" else "失败",
                        error_code=message["code"],
                        error=message["message"],
                    )
                    return row
                result = message["output"]
            output.mkdir(parents=True, exist_ok=True)
            postprocess(
                result,
                options={},
                work_dir=work,
                images_prefix="images",
                storage=LocalStorage(output),
            )
            shutil.copyfile(work / "out/doc.md", output / "doc.md")
            shutil.copyfile(work / "out/doc.json", output / "doc.json")
            doc = result.document
            row.update(
                status="成功",
                pages=result.pages,
                ocr_pages=result.ocr_pages,
                headings=json.dumps(heading_counts(doc), ensure_ascii=False, sort_keys=True),
                tables=len(doc.tables),
                images=len(doc.pictures),
                characters=(
                    sum(len(item.text) for item in doc.texts)
                    + sum(len(cell.text) for table in doc.tables for cell in table.data.table_cells)
                ),
            )
        except Exception as exc:  # noqa: BLE001
            row.update(error_code=getattr(exc, "code", "ENGINE_ERROR"), error=str(exc))
        finally:
            if worker is not None and worker.is_alive():
                worker.terminate()
                worker.join()
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--engines", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()
    variants = args.engines.split(",")
    if args.repeat < 1 or any(v not in VARIANTS for v in variants) or not args.input.is_dir():
        ap.error("输入目录、引擎或 repeat 无效")
    args.out.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in args.input.rglob("*") if p.is_file() and type_for_ext(p.suffix))
    rows = []
    legacy = {
        "doc": ("docx", {"docling", "markitdown"}),
        "ppt": ("pptx", {"docling", "markitdown"}),
        "xls": ("xlsx", {"builtin"}),
    }
    with tempfile.TemporaryDirectory(prefix=".bench-convert-", dir=args.out) as cache:
        for index, source in enumerate(files):
            rel = source.relative_to(args.input)
            spec = legacy.get(source.suffix.lower().lstrip("."))
            converted = None
            conversion_s = 0.0
            conversion_error = None
            if spec and any(VARIANTS[v][0] in spec[1] for v in variants):
                converted = Path(cache) / str(index) / (source.stem + "." + spec[0])
                converted.parent.mkdir(parents=True)
                start = time.monotonic()
                converter = Converter()
                try:
                    check_file(source, source.suffix)
                    converter.convert_file(source, converted)
                except Exception as exc:  # noqa: BLE001
                    conversion_error = {
                        "status": "失败",
                        "error_code": getattr(exc, "code", "CONVERT_FAILED"),
                        "error": str(exc),
                    }
                finally:
                    try:
                        converter.stop()
                    except Exception as exc:  # noqa: BLE001
                        if conversion_error is None:
                            conversion_error = {
                                "status": "失败",
                                "error_code": "CONVERT_FAILED",
                                "error": str(exc),
                            }
                    conversion_s = time.monotonic() - start
            for variant in variants:
                output = args.out / "documents" / rel / variant
                needs_conversion = spec is not None and VARIANTS[variant][0] in spec[1]
                runs = []
                if needs_conversion and conversion_error is not None:
                    runs.append({**conversion_error, "conversion_s": conversion_s})
                else:
                    input_path = converted if needs_conversion else source
                    for _ in range(args.repeat):
                        with tempfile.TemporaryDirectory() as tmp:
                            result = Path(tmp) / "result.json"
                            completed = subprocess.run(
                                [
                                    sys.executable,
                                    __file__,
                                    "--worker",
                                    str(input_path.resolve()),
                                    variant,
                                    str(output.resolve()),
                                    str(result),
                                    str(conversion_s if needs_conversion else 0),
                                ],
                                check=False,
                            )
                            if completed.returncode or not result.exists():
                                runs.append(
                                    {
                                        "status": "失败",
                                        "error_code": "ENGINE_ERROR",
                                        "error": f"解析进程退出：{completed.returncode}",
                                        "conversion_s": conversion_s if needs_conversion else 0,
                                    }
                                )
                            else:
                                runs.append(json.loads(result.read_text()))
                row = runs[-1]
                for key in ("duration_s", "conversion_s", "warmup_s", "peak_memory_mb"):
                    values = [r[key] for r in runs if isinstance(r.get(key), (int, float))]
                    if values:
                        row[key] = round(
                            max(values) if key == "peak_memory_mb" else statistics.median(values), 3
                        )
                row.update(
                    file=str(rel),
                    type=type_for_ext(source.suffix),
                    engine=variant,
                    document=str(output.relative_to(args.out)),
                )
                rows.append(row)
                print(f"{rel} / {variant}: {row['status']}", flush=True)
    fields = [
        "file",
        "type",
        "engine",
        "status",
        "error_code",
        "error",
        "pages",
        "ocr_pages",
        "duration_s",
        "conversion_s",
        "warmup_s",
        "peak_memory_mb",
        "headings",
        "tables",
        "images",
        "characters",
        "document",
    ]
    with (args.out / "results.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    write_report(rows, args.out)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        _, _, source, variant, output, result, conversion_s = sys.argv
        Path(result).write_text(
            json.dumps(
                execute(Path(source), variant, Path(output), float(conversion_s)),
                ensure_ascii=False,
            )
        )
    else:
        main()
