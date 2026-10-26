"""在测试镜像中生成 X02 小样例；不依赖外部下载或 Office 商业软件。

用法：/opt/unoserver/bin/python tests/samples/generate.py /tmp/samples
生成的每个文件都必须小于 100KB。
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def generate(destination: Path) -> None:
    import uno

    def property_value(name, value):
        prop = uno.createUnoStruct("com.sun.star.beans.PropertyValue")
        prop.Name, prop.Value = name, value
        return prop

    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as profile:
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "unoserver.server",
                "--user-installation",
                profile,
                "--quiet",
            ],
            start_new_session=True,
        )
        try:
            resolver = uno.getComponentContext().ServiceManager.createInstanceWithContext(
                "com.sun.star.bridge.UnoUrlResolver", uno.getComponentContext()
            )
            deadline = time.monotonic() + 30
            while True:
                try:
                    context = resolver.resolve(
                        "uno:socket,host=127.0.0.1,port=2002;urp;StarOffice.ComponentContext"
                    )
                    break
                except Exception:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.2)
            desktop = context.ServiceManager.createInstanceWithContext(
                "com.sun.star.frame.Desktop", context
            )
            provider = context.ServiceManager.createInstanceWithContext(
                "com.sun.star.configuration.ConfigurationProvider", context
            )
            config = provider.createInstanceWithArguments(
                "com.sun.star.configuration.ConfigurationUpdateAccess",
                (
                    property_value(
                        "nodepath", "/org.openoffice.Office.Common/Filter/Microsoft/Export"
                    ),
                ),
            )
            # 二进制 PPT 的缩略图本身约 430KB；样例只需正文，不保存缩略图。
            config.setPropertyValue("EnablePowerPointPreview", False)
            config.commitChanges()
            for kind, ext, filter_name in [
                ("swriter", "doc", "MS Word 97"),
                ("simpress", "ppt", "MS PowerPoint 97"),
                ("scalc", "xls", "MS Excel 97"),
            ]:
                document = desktop.loadComponentFromURL(
                    f"private:factory/{kind}", "_blank", 0, (property_value("Hidden", True),)
                )
                if kind == "swriter":
                    cursor = document.Text.createTextCursor()
                    cursor.CharFontNameAsian = "Noto Sans CJK SC"
                    document.Text.insertString(cursor, "智能体开发平台中文预览测试", False)
                elif kind == "simpress":
                    page = document.DrawPages.getByIndex(0)
                    shape = document.createInstance("com.sun.star.drawing.TextShape")
                    size = uno.createUnoStruct("com.sun.star.awt.Size")
                    size.Width, size.Height = 20000, 4000
                    shape.setSize(size)
                    page.add(shape)
                    shape.String = "智能体开发平台演示文稿"
                else:
                    document.Sheets.getByIndex(0).getCellRangeByName("A1").setString("中文表格")
                document.storeAsURL(
                    (destination / f"sample.{ext}").as_uri(),
                    (property_value("FilterName", filter_name), property_value("Overwrite", True)),
                )
                extra_formats = {
                    "swriter": [
                        ("docx", "Office Open XML Text"),
                        ("rtf", "Rich Text Format"),
                        ("odt", "writer8"),
                    ],
                    "simpress": [("odp", "impress8")],
                    "scalc": [("ods", "calc8")],
                }
                for extra_ext, extra_filter in extra_formats[kind]:
                    document.storeAsURL(
                        (destination / f"sample.{extra_ext}").as_uri(),
                        (
                            property_value("FilterName", extra_filter),
                            property_value("Overwrite", True),
                        ),
                    )
                document.close(True)
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(server.pid, signal.SIGKILL)
            server.wait()
    for path in destination.iterdir():
        assert path.stat().st_size <= 100 * 1024, path


if __name__ == "__main__":
    generate(Path(sys.argv[1]).resolve())
