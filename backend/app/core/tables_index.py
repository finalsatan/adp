"""集中 import 各模块的 tables.py，让 Alembic 能看到所有表。

新增模块时在这里加一行，例如：
    from app.kb import tables as kb_tables  # noqa: F401
"""

from app.audit import tables as audit_tables  # noqa: F401
from app.auth import tables as auth_tables  # noqa: F401
from app.gateway import tables as gateway_tables  # noqa: F401
from app.iam import tables as iam_tables  # noqa: F401
from app.kb import tables as kb_tables  # noqa: F401
from app.models import tables as models_tables  # noqa: F401
from app.resources import tables as resources_tables  # noqa: F401
from app.settings import tables as settings_tables  # noqa: F401
