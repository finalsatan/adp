from __future__ import annotations

import csv
import io

from ..builder import ElementBuilder, table_row_text


def parse_csv(text: str, builder: ElementBuilder, max_rows: int) -> None:
    sample = text[:65536]
    delimiter = ","
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        builder.warn("未能可靠识别 CSV 分隔符，已按逗号解析。")

    table_group = 1
    header_emitted = False
    expected_columns: int | None = None
    ragged_warned = False
    physical_rows = 0
    truncated = False

    try:
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        for row_index, row in enumerate(reader, start=1):
            if row_index > max_rows:
                truncated = True
                break
            physical_rows = row_index
            if not row or all(str(cell).strip() == "" for cell in row):
                continue

            cells = [cell for cell in row]
            if expected_columns is None:
                expected_columns = len(cells)
            elif len(cells) != expected_columns and not ragged_warned:
                builder.warn(
                    f"CSV 第 {row_index} 行列数（{len(cells)}）与表头列数"
                    f"（{expected_columns}）不一致，已按实际单元格输出。"
                )
                ragged_warned = True

            element_type = "table_header" if not header_emitted else "table_row"
            builder.add(
                element_type,
                table_row_text(cells),
                row=row_index,
                table_group=table_group,
            )
            header_emitted = True
    except csv.Error as exc:
        builder.warn(f"CSV 在第 {physical_rows + 1} 行附近解析失败：{exc}")

    if truncated:
        builder.warn(f"CSV 超过 {max_rows} 行上限，后续行已截断。")
    if not header_emitted:
        return
    if delimiter != ",":
        builder.warn(f"CSV 使用分隔符 {delimiter!r} 解析。")
