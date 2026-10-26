"""生成加密样例（仅重新生成时需临时依赖，不加入服务依赖）。

在 parser/ 下执行：uv run --with msoffcrypto-tool==5.4.2 python tests/samples/encrypt.py
先用 generate.py 生成 sample.docx，再用 MS-OFFCRYPTO 加密，测试密码 test-password。
"""

from pathlib import Path

import msoffcrypto

base = Path(__file__).parent
with (base / "sample.docx").open("rb") as source, (base / "encrypted.docx").open("wb") as output:
    msoffcrypto.OfficeFile(source).encrypt("test-password", output)
