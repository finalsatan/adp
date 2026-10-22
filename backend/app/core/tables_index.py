"""集中 import 各模块的 tables.py，让 Alembic 能看到所有表。

新增模块时在这里加一行，例如：
    from app.iam import tables as iam_tables  # noqa: F401
"""
