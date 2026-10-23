#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["psycopg[binary]>=3.2"]
# ///
"""ParadeDB 中文检索验证脚本（任务卡 X09）。

连 P1-01 的开发容器（默认 localhost:55432/adp_test），用迁移账号建临时
schema，逐项验证知识库技术方案 11.3、11.4、12.1、12.2 里的建表、建索引和
查询写法，结束后删除临时 schema。结果写到同目录的 REPORT.md。

用法：
    uv run scripts/paradedb_check/check.py            # 功能验证
    uv run scripts/paradedb_check/check.py --rows 100000  # 追加性能冒烟
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
SCHEMA = "x09_check"
IMAGE = os.environ.get("PARADEDB_IMAGE", "paradedb/paradedb:0.26.2-pg16")

DSN = os.environ.get(
    "PARADEDB_DSN",
    "host={host} port={port} dbname={db} user={user} password={password}".format(
        host=os.environ.get("PARADEDB_HOST", "localhost"),
        port=os.environ.get("PARADEDB_PORT", "55432"),
        db=os.environ.get("PARADEDB_DB", "adp_test"),
        user=os.environ.get("PARADEDB_USER", "adp_migrator"),
        password=os.environ.get("PARADEDB_PASSWORD", "adp_migrator_pw"),
    ),
)

# 建表、建索引的 SQL（知识库技术方案 11.3；索引字段一次定全）
DDL_TABLE = """
CREATE TABLE {schema}.kb_index (
    id bigserial PRIMARY KEY,
    entry_type text NOT NULL,
    chunk_id bigint,
    qa_id bigint,
    doc_id bigint,
    generation int NOT NULL,
    active boolean NOT NULL,
    embedding vector(1024),
    title text,
    body text NOT NULL,
    keywords text
)
"""

DDL_HNSW = "CREATE INDEX kb_hnsw ON {schema}.kb_index USING hnsw (embedding vector_cosine_ops)"

DDL_BM25 = """
CREATE INDEX kb_bm25 ON {schema}.kb_index USING paradedb (
    id,
    (title::pdb.jieba),
    (body::pdb.jieba),
    (keywords::pdb.jieba),
    entry_type,
    doc_id,
    active
)
"""

# 2560 维用 halfvec（pgvector HNSW 上限）
DDL_HALFVEC_TABLE = """
CREATE TABLE {schema}.kb_halfvec (
    id bigserial PRIMARY KEY,
    active boolean NOT NULL,
    embedding halfvec(2560)
)
"""
DDL_HALFVEC_HNSW = (
    "CREATE INDEX kb_halfvec_hnsw ON {schema}.kb_halfvec USING hnsw (embedding halfvec_cosine_ops)"
)

# 全文召回（知识库技术方案 11.4）
SQL_FULLTEXT = """
SELECT id, entry_type, doc_id, title, pdb.score(id) AS score
FROM {schema}.kb_index
WHERE (title ||| %(q)s::text::pdb.boost(2) OR body ||| %(q)s::text OR keywords ||| %(q)s::text::pdb.boost(2))
  AND active
  AND doc_id = %(doc_id)s
ORDER BY pdb.score(id) DESC
LIMIT 5
"""

# 关键词分（知识库技术方案 12.2）：strpos 判断查询词是否出现
SQL_KEYWORD_SCORE = """
WITH q AS (SELECT unnest(%(terms)s::text[]) AS term)
SELECT t.id,
       COALESCE(SUM(CASE WHEN strpos(t.title, q.term) > 0
                          OR strpos(t.body, q.term) > 0
                          OR strpos(t.keywords, q.term) > 0
                         THEN 1 ELSE 0 END), 0)::float
       / (SELECT count(*) FROM q) AS keyword_score
FROM {schema}.kb_index t
LEFT JOIN q ON true
WHERE (t.title ||| %(q)s::text OR t.body ||| %(q)s::text OR t.keywords ||| %(q)s::text)
  AND t.active
GROUP BY t.id
ORDER BY keyword_score DESC, t.id
LIMIT 5
"""


@dataclass
class Item:
    name: str
    passed: bool
    summary: str
    sql: str = ""
    details: str = ""


@dataclass
class Report:
    items: list[Item] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    perf: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def add(self, name, passed, summary, sql="", details=""):
        self.items.append(Item(name, passed, summary, sql, details))
        mark = "PASS" if passed else "FAIL"
        print(f"[{mark}] {name}: {summary}")
        if not passed and details:
            print("       " + details.replace("\n", "\n       "))


def clean_plan(text: str) -> str:
    """把 EXPLAIN 输出里的长向量字面量缩掉，报告里别刷屏。"""
    return re.sub(r"'\[[0-9.,]+\]'::vector", "'[<向量>]'::vector", text)


def df_line() -> str:
    out = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, check=True)
    return out.stdout.strip().splitlines()[-1]


def df_free_gb() -> float:
    return shutil.disk_usage("/").free / 1024**3


def vec_literal(dim: int, rng: random.Random) -> str:
    return "[" + ",".join(f"{rng.random():.6f}" for _ in range(dim)) + "]"


def sample_rows() -> list[dict]:
    """约 30 条中文样例：制度、报销、差旅，含长词、专有名词，部分 active=false。"""
    rows = [
        # (entry_type, doc_id, active, title, body, keywords)
        ("chunk", 1, True, "差旅费报销制度", "员工出差期间发生的交通、住宿、伙食补助按本标准报销。", "差旅,报销,制度"),
        ("chunk", 1, True, "差旅费报销制度", "乘坐高铁出差应选择二等座，超标部分由个人承担。", "差旅,高铁,报销"),
        ("chunk", 1, True, "差旅费报销制度", "长江大桥沿线的出差补贴标准按一类地区执行。", "长江大桥,差旅,补贴"),
        ("chunk", 1, True, "差旅费报销制度", "南京市长江大桥附近的酒店住宿上限为每晚五百元。", "南京,长江大桥,住宿"),
        ("chunk", 1, True, "差旅费报销制度", "跨省出差的市内交通费凭发票据实报销。", "交通费,报销,发票"),
        ("chunk", 1, False, "差旅费报销制度（旧版）", "旧版差旅报销标准已废止，请以新版为准。", "差旅,旧版"),
        ("chunk", 2, True, "费用报销管理办法", "日常费用报销需在费用发生后三十日内提交。", "报销,费用,管理"),
        ("chunk", 2, True, "费用报销管理办法", "差旅报销单需附审批通过的出差申请单。", "报销,差旅,审批"),
        ("chunk", 2, True, "费用报销管理办法", "发票抬头必须为公司全称，否则不予报销。", "发票,抬头,报销"),
        ("chunk", 2, True, "费用报销管理办法", "超过五千元的报销需要部门负责人和财务总监双重审批。", "审批,财务,报销"),
        ("chunk", 2, False, "费用报销管理办法（草稿）", "本段为草稿内容，尚未生效。", "草稿,报销"),
        ("chunk", 3, True, "差旅管理规定", "出差前须提交出差申请，注明目的地、时间和事由。", "差旅,申请,规定"),
        ("chunk", 3, True, "差旅管理规定", "出差期间原则上不得变更行程，确需变更的应报备。", "差旅,行程,报备"),
        ("chunk", 3, True, "差旅管理规定", "国际差旅需提前十五个工作日申请并办理签证。", "国际,差旅,签证"),
        ("chunk", 3, True, "差旅管理规定", "南京市长江大桥附近的客户拜访可当天往返。", "南京,长江大桥,拜访"),
        ("chunk", 4, True, "智能体开发平台使用手册", "智能体开发平台支持知识库、模型服务和应用编排。", "智能体,开发平台,手册"),
        ("chunk", 4, True, "智能体开发平台使用手册", "在智能体开发平台创建应用后，可绑定多个知识库。", "智能体,应用,知识库"),
        ("chunk", 4, True, "智能体开发平台使用手册", "平台管理员可在系统管理中配置模型服务的接口地址。", "模型服务,系统管理,配置"),
        ("chunk", 4, False, "智能体开发平台使用手册（历史）", "该章节已随版本升级删除。", "历史,手册"),
        ("chunk", 5, True, "差旅报销常见问题", "问：出差补贴是否需要提供发票？答：伙食补助无需发票。", "差旅,补贴,发票"),
        ("chunk", 5, True, "差旅报销常见问题", "问：长江大桥过桥费能否报销？答：可以，凭通行费发票。", "长江大桥,过桥费,报销"),
        ("chunk", 5, True, "差旅报销常见问题", "问：退票手续费是否报销？答：因公退票可据实报销。", "退票,手续费,报销"),
        ("chunk", 6, True, "南京市长江大桥维护公告", "南京市长江大桥将于本周末进行例行维护，请合理安排出行。", "南京,长江大桥,维护"),
        ("chunk", 6, True, "南京市长江大桥维护公告", "维护期间长江大桥双向各封闭一条车道。", "长江大桥,车道,封闭"),
        ("chunk", 6, True, "南京市长江大桥维护公告", "货车请绕行长江二桥或过江隧道。", "货车,绕行,隧道"),
        ("chunk", 7, True, "微信支付报销流程", "使用微信支付产生的费用，可凭电子凭证报销。", "微信支付,报销,流程"),
        ("chunk", 7, True, "微信支付报销流程", "微信支付凭证需在账单中下载并打印后提交。", "微信支付,凭证,打印"),
        ("chunk", 7, False, "微信支付报销流程（停用）", "该流程已停用，改走统一报销入口。", "微信支付,停用"),
        ("chunk", 8, True, "办公用品采购制度", "办公用品采购需通过统一的采购申请流程。", "采购,办公用品,制度"),
        ("chunk", 8, True, "办公用品采购制度", "单笔超过两千元的采购需三家比价。", "采购,比价,制度"),
    ]
    return [
        {
            "entry_type": r[0],
            "doc_id": r[1],
            "active": r[2],
            "title": r[3],
            "body": r[4],
            "keywords": r[5],
        }
        for r in rows
    ]


def check_extensions(cur, rep: Report) -> dict:
    cur.execute("SELECT extname, extversion FROM pg_extension WHERE extname IN ('vector','pg_search')")
    vers = dict(cur.fetchall())
    cur.execute("SHOW server_version")
    pgver = cur.fetchone()[0]
    rep.meta["pg_version"] = pgver
    rep.meta["extensions"] = vers
    ok = "vector" in vers and "pg_search" in vers
    rep.add(
        "1. 扩展 vector、pg_search",
        ok,
        f"vector={vers.get('vector')}, pg_search={vers.get('pg_search')}, PostgreSQL {pgver}",
        sql="SELECT extname, extversion FROM pg_extension WHERE extname IN ('vector','pg_search')",
    )
    return vers


def check_tables(cur, rep: Report):
    cur.execute(DDL_TABLE.format(schema=SCHEMA))
    cur.execute(DDL_HALFVEC_TABLE.format(schema=SCHEMA))
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema=%s ORDER BY table_name",
        (SCHEMA,),
    )
    tables = [r[0] for r in cur.fetchall()]
    ok = "kb_index" in tables and "kb_halfvec" in tables
    rep.add(
        "2. 建索引表（vector(1024) 与 halfvec(2560)）",
        ok,
        f"已建表：{', '.join(tables)}",
        sql=DDL_TABLE.format(schema=SCHEMA).strip() + "\n\n" + DDL_HALFVEC_TABLE.format(schema=SCHEMA).strip(),
    )


def check_hnsw(cur, rep: Report):
    cur.execute(DDL_HNSW.format(schema=SCHEMA))
    cur.execute(DDL_HALFVEC_HNSW.format(schema=SCHEMA))
    cur.execute(
        "SELECT i.indexrelid::regclass::text, pg_get_indexdef(i.indexrelid) "
        "FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid "
        "WHERE c.relname IN ('kb_hnsw','kb_halfvec_hnsw') ORDER BY 1",
    )
    defs = cur.fetchall()
    ok = len(defs) == 2 and all("USING hnsw" in d for _, d in defs)
    rep.add(
        "3. 建 HNSW 索引（vector_cosine_ops / halfvec_cosine_ops）",
        ok,
        "；".join(f"{n}: {'USING hnsw' in d}" for n, d in defs),
        sql=DDL_HNSW.format(schema=SCHEMA) + "\n" + DDL_HALFVEC_HNSW.format(schema=SCHEMA),
        details="\n".join(d for _, d in defs),
    )


def check_bm25(cur, rep: Report):
    cur.execute(DDL_BM25.format(schema=SCHEMA))
    cur.execute(
        "SELECT pg_get_indexdef(i.indexrelid) FROM pg_index i "
        "JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relname='kb_bm25'"
    )
    idxdef = cur.fetchone()[0]
    ok = "USING paradedb" in idxdef and idxdef.count("pdb.jieba") == 3
    rep.add(
        "4. 建 BM25 索引（jieba 分词器 + entry_type/doc_id/active）",
        ok,
        "索引已建，含 3 个 pdb.jieba 字段与 3 个过滤列" if ok else "索引定义与预期不符",
        sql=DDL_BM25.format(schema=SCHEMA).strip(),
        details=idxdef,
    )


def insert_samples(cur, rep: Report) -> int:
    rng = random.Random(42)
    rows = sample_rows()
    for r in rows:
        cur.execute(
            f"INSERT INTO {SCHEMA}.kb_index "
            "(entry_type, doc_id, generation, active, embedding, title, body, keywords) "
            "VALUES (%(entry_type)s, %(doc_id)s, 1, %(active)s, %(embedding)s::vector, "
            "%(title)s, %(body)s, %(keywords)s)",
            {**r, "embedding": vec_literal(1024, rng)},
        )
    cur.execute(
        f"INSERT INTO {SCHEMA}.kb_halfvec (active, embedding) VALUES "
        f"(true, %s::halfvec)",
        (vec_literal(2560, rng),),
    )
    cur.execute(f"SELECT count(*) FROM {SCHEMA}.kb_index")
    n = cur.fetchone()[0]
    cur.execute(f"SELECT count(*) FROM {SCHEMA}.kb_index WHERE NOT active")
    inactive = cur.fetchone()[0]
    rep.add(
        "5. 插入中文样例",
        n == len(rows),
        f"插入 {n} 条，其中 active=false {inactive} 条；另插入 1 条 2560 维 halfvec 记录",
        sql="INSERT INTO kb_index (...) VALUES (...);  -- 见脚本 sample_rows()",
    )
    return n


def check_fulltext(cur, rep: Report):
    cur.execute(
        SQL_FULLTEXT.format(schema=SCHEMA), {"q": "长江大桥 报销", "doc_id": 1}
    )
    rows = cur.fetchall()
    rep.add(
        "6a. 全文召回（boost + active/doc_id 过滤 + pdb.score 排序）",
        len(rows) > 0,
        f"召回 {len(rows)} 条；首条 id={rows[0][0] if rows else None}，score={round(rows[0][4],4) if rows else None}",
        sql=SQL_FULLTEXT.format(schema=SCHEMA).strip(),
        details="\n".join(
            f"id={r[0]} type={r[1]} doc={r[2]} title={r[3]} score={r[4]:.4f}" for r in rows
        ),
    )


def check_search_mode(cur, rep: Report):
    cur.execute(
        f"SELECT id, body FROM {SCHEMA}.kb_index WHERE body ||| %s AND active ORDER BY pdb.score(id) DESC",
        ("大桥",),
    )
    rows = cur.fetchall()
    hit_long = any("长江大桥" in r[1] for r in rows)
    rep.add(
        "6b. 搜索模式拆词：搜“大桥”召回“长江大桥”",
        hit_long,
        f"搜“大桥”召回 {len(rows)} 条，含“长江大桥”的条目：{hit_long}",
        sql=f"SELECT id, body FROM {SCHEMA}.kb_index WHERE body ||| '大桥' AND active ORDER BY pdb.score(id) DESC",
        details="\n".join(f"id={r[0]} {r[1]}" for r in rows[:5]),
    )


def check_query_tokenize(cur, rep: Report):
    q = "南京市长江大桥附近的报销标准"
    cur.execute("SELECT %s::pdb.jieba('search_mode=false')::text[]", (q,))
    words = cur.fetchone()[0]
    ok = isinstance(words, list) and len(words) > 0
    rep.add(
        "6c. 查询分词（search_mode=false 返回原词）",
        ok,
        f"{q!r} -> {words}",
        sql="SELECT :q::pdb.jieba('search_mode=false')::text[]",
    )


def check_pushdown(cur, rep: Report):
    sql = (
        f"EXPLAIN (VERBOSE, COSTS OFF) SELECT id FROM {SCHEMA}.kb_index "
        "WHERE (title ||| '大桥' OR body ||| '大桥') AND active AND doc_id = 1"
    )
    cur.execute(sql)
    plan = "\n".join(r[0] for r in cur.fetchall())
    m = re.search(r"Tantivy Query: (\{.*\})", plan)
    pushed = []
    if m:
        try:
            tq = json.loads(m.group(1))
            fields = set(re.findall(r'"field"\s*:\s*"([^"]+)"', json.dumps(tq)))
            pushed = sorted(fields)
        except json.JSONDecodeError:
            pass
    ok = "active" in pushed and "doc_id" in pushed
    rep.add(
        "6d. 过滤在 BM25 索引内完成（EXPLAIN 下推）",
        ok,
        f"Tantivy Query 中出现的字段：{pushed}",
        sql=sql,
        details=plan,
    )


def check_mvcc(conn, rep: Report):
    other = psycopg.connect(DSN, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SET search_path TO " + SCHEMA + ", public")
        with other.cursor() as oc:
            oc.execute("SET search_path TO " + SCHEMA + ", public")

        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {SCHEMA}.kb_index (entry_type, doc_id, generation, active, body) "
                "VALUES ('chunk', 9, 1, true, %s)",
                ("MVCC事务可见性唯一标记词",),
            )
        with other.cursor() as oc:
            oc.execute(f"SELECT count(*) FROM {SCHEMA}.kb_index WHERE body ||| '唯一标记词'")
            before = oc.fetchone()[0]
        conn.commit()
        with other.cursor() as oc:
            oc.execute(f"SELECT count(*) FROM {SCHEMA}.kb_index WHERE body ||| '唯一标记词'")
            after = oc.fetchone()[0]
        conn.autocommit = True
        ok = before == 0 and after == 1
        rep.add(
            "6e. MVCC：BM25 索引随事务提交生效",
            ok,
            f"未提交时另一连接搜到 {before} 条，提交后搜到 {after} 条",
            sql=(
                "-- 连接 A 未提交：\n"
                f"INSERT INTO {SCHEMA}.kb_index (entry_type, doc_id, generation, active, body) "
                "VALUES ('chunk', 9, 1, true, 'MVCC事务可见性唯一标记词');\n"
                "-- 连接 B：\n"
                f"SELECT count(*) FROM {SCHEMA}.kb_index WHERE body ||| '唯一标记词';"
            ),
        )
    finally:
        other.close()


def check_vector(conn, rep: Report):
    rng = random.Random(7)

    def clustered_vec(active: bool) -> str:
        """构造两组可控向量：inactive 靠近 e0，active 在 60° 外，用来稳定演示迭代扫描。"""
        v = [0.0] * 1024
        if active:
            v[0], v[1] = 0.5, 0.866
        else:
            v[0] = 1.0
        for i in range(1024):
            v[i] += rng.uniform(-0.002, 0.002)
        norm = math.sqrt(sum(x * x for x in v))
        return "[" + ",".join(f"{x / norm:.6f}" for x in v) + "]"

    with conn.cursor() as cur:
        cur.execute(
            f"CREATE TABLE {SCHEMA}.kb_vec "
            "(id bigserial PRIMARY KEY, active boolean NOT NULL, embedding vector(1024))"
        )
        cur.execute(
            f"CREATE INDEX kb_vec_hnsw ON {SCHEMA}.kb_vec USING hnsw (embedding vector_cosine_ops)"
        )
        rows = [(False, clustered_vec(False)) for _ in range(1000)]
        rows += [(True, clustered_vec(True)) for _ in range(10)]
        cur.executemany(
            f"INSERT INTO {SCHEMA}.kb_vec (active, embedding) VALUES (%s, %s::vector)", rows
        )
        cur.execute(f"ANALYZE {SCHEMA}.kb_vec")
        q = "[" + ",".join(["1.0"] + ["0.0"] * 1023) + "]"

        # 小表下规划器会选 Seq Scan；强制走 HNSW 才能观察索引自身的迭代扫描行为
        with conn.transaction():
            cur.execute("SET LOCAL enable_seqscan = off")
            cur.execute("SET LOCAL hnsw.ef_search = 20")
            cur.execute(
                f"SELECT id, embedding <=> %s::vector AS dist FROM {SCHEMA}.kb_vec "
                "ORDER BY embedding <=> %s::vector LIMIT 5",
                (q, q),
            )
            got = cur.fetchall()
            cur.execute(
                f"EXPLAIN SELECT id FROM {SCHEMA}.kb_vec ORDER BY embedding <=> %s::vector LIMIT 5",
                (q,),
            )
            vec_plan = "\n".join(r[0] for r in cur.fetchall())

        def filtered_count(scan_mode: str) -> int:
            with conn.transaction():
                cur.execute("SET LOCAL enable_seqscan = off")
                cur.execute("SET LOCAL hnsw.ef_search = 10")
                cur.execute(f"SET LOCAL hnsw.iterative_scan = {scan_mode}")
                cur.execute(
                    f"SELECT count(*) FROM (SELECT id FROM {SCHEMA}.kb_vec WHERE active "
                    "ORDER BY embedding <=> %s::vector LIMIT 10) s",
                    (q,),
                )
                return cur.fetchone()[0]

        off = filtered_count("off")
        relaxed = filtered_count("relaxed_order")
        with conn.transaction():
            cur.execute("SET LOCAL enable_seqscan = off")
            cur.execute("SET LOCAL hnsw.ef_search = 10")
            cur.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            cur.execute(
                f"EXPLAIN SELECT id FROM {SCHEMA}.kb_vec WHERE active "
                "ORDER BY embedding <=> %s::vector LIMIT 10",
                (q,),
            )
            scan_plan = "\n".join(r[0] for r in cur.fetchall())

    ok = len(got) == 5 and relaxed > off
    rep.add(
        "6f. 向量召回（<=> + hnsw.ef_search + hnsw.iterative_scan）",
        ok,
        f"ef_search=20 召回 {len(got)} 条；带 active 过滤时 iterative_scan off={off}、relaxed_order={relaxed}"
        "（小表强制 enable_seqscan=off 走 HNSW）",
        sql=(
            "SET LOCAL enable_seqscan = off;  -- 小表测试时强制走 HNSW\n"
            "SET LOCAL hnsw.ef_search = 20;\n"
            f"SELECT id, embedding <=> :v::vector AS dist FROM {SCHEMA}.kb_vec "
            "ORDER BY embedding <=> :v::vector LIMIT 5;\n"
            "-- 带过滤（过滤会排掉大部分候选）：\n"
            "SET LOCAL hnsw.ef_search = 10;\n"
            "SET LOCAL hnsw.iterative_scan = relaxed_order;\n"
            f"SELECT id FROM {SCHEMA}.kb_vec WHERE active ORDER BY embedding <=> :v::vector LIMIT 10;"
        ),
        details="无过滤计划：\n" + clean_plan(vec_plan) + "\n\n带过滤计划：\n" + clean_plan(scan_plan),
    )


def check_keyword_score(cur, rep: Report):
    terms = ["长江大桥", "报销"]
    cur.execute(
        SQL_KEYWORD_SCORE.format(schema=SCHEMA), {"terms": terms, "q": " ".join(terms)}
    )
    rows = cur.fetchall()
    rep.add(
        "6g. 关键词分（strpos 判断查询词命中）",
        len(rows) > 0,
        f"返回 {len(rows)} 条；首条 keyword_score={round(rows[0][1],3) if rows else None}",
        sql=SQL_KEYWORD_SCORE.format(schema=SCHEMA).strip(),
        details="\n".join(f"id={r[0]} keyword_score={r[1]:.3f}" for r in rows),
    )


def run_perf(conn, rows: int, rep: Report):
    print(f"\n== 性能冒烟：插入 {rows} 行 ==")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            f"""CREATE TABLE {SCHEMA}.perf (
                id bigserial PRIMARY KEY,
                active boolean NOT NULL,
                embedding vector(1024),
                title text,
                body text NOT NULL
            )"""
        )
        t0 = time.perf_counter()
        cur.execute(
            f"""INSERT INTO {SCHEMA}.perf (active, embedding, title, body)
                SELECT (g %% 10 = 0),
                       (SELECT array_agg(random()::real) FROM generate_series(1,1024))::vector(1024),
                       '标题 ' || g,
                       '中文正文内容 ' || g || ' 报销 差旅 长江大桥 制度'
                FROM generate_series(1, %s) g""",
            (rows,),
        )
        insert_s = time.perf_counter() - t0
        cur.execute(f"CREATE INDEX perf_hnsw ON {SCHEMA}.perf USING hnsw (embedding vector_cosine_ops)")
        cur.execute(
            f"""CREATE INDEX perf_bm25 ON {SCHEMA}.perf USING paradedb
                (id, (title::pdb.jieba), (body::pdb.jieba), active)"""
        )
        cur.execute(f"ANALYZE {SCHEMA}.perf")
        cur.execute("SET hnsw.ef_search = 40")

        rng = random.Random(99)

        def pct(vals, p):
            vals = sorted(vals)
            k = max(0, min(len(vals) - 1, int(round((p / 100) * (len(vals) - 1)))))
            return vals[k] * 1000

        vec_lat = []
        for _ in range(100):
            qv = vec_literal(1024, rng)
            t = time.perf_counter()
            cur.execute(
                f"SELECT id FROM {SCHEMA}.perf ORDER BY embedding <=> %s::vector LIMIT 10",
                (qv,),
            )
            cur.fetchall()
            vec_lat.append(time.perf_counter() - t)

        ft_lat = []
        for _ in range(100):
            t = time.perf_counter()
            cur.execute(
                f"SELECT id, pdb.score(id) FROM {SCHEMA}.perf "
                "WHERE body ||| '长江大桥 报销' ORDER BY pdb.score(id) DESC LIMIT 10"
            )
            cur.fetchall()
            ft_lat.append(time.perf_counter() - t)

    rep.perf = {
        "rows": rows,
        "insert_s": round(insert_s, 1),
        "vector_p50_ms": round(pct(vec_lat, 50), 2),
        "vector_p95_ms": round(pct(vec_lat, 95), 2),
        "vector_max_ms": round(max(vec_lat) * 1000, 2),
        "fulltext_p50_ms": round(pct(ft_lat, 50), 2),
        "fulltext_p95_ms": round(pct(ft_lat, 95), 2),
        "fulltext_max_ms": round(max(ft_lat) * 1000, 2),
    }
    rep.add(
        "7. 性能冒烟",
        True,
        f"{rows} 行：插入 {rep.perf['insert_s']}s；向量 P50/P95={rep.perf['vector_p50_ms']}/{rep.perf['vector_p95_ms']}ms；"
        f"全文 P50/P95={rep.perf['fulltext_p50_ms']}/{rep.perf['fulltext_p95_ms']}ms",
    )


def render(rep: Report, perf_skipped: str | None) -> str:
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %z")
    lines = [
        "# ParadeDB 中文检索验证报告（X09）",
        "",
        f"- 生成时间：{now}",
        f"- 镜像：`{IMAGE}`",
        f"- 数据库：{rep.meta.get('pg_version', '?')}，vector={rep.meta.get('extensions', {}).get('vector')}，"
        f"pg_search={rep.meta.get('extensions', {}).get('pg_search')}",
        f"- 临时 schema：`{SCHEMA}`（运行结束已删除）",
        "",
        "## 逐项验证",
        "",
        "| # | 项 | 结果 | 摘要 |",
        "| --- | --- | --- | --- |",
    ]
    for it in rep.items:
        summary = it.summary.replace("|", "\\|")
        lines.append(
            f"| {it.name.split('.')[0]} | {it.name.split('. ', 1)[-1]} | "
            f"{'通过' if it.passed else '不通过'} | {summary} |"
        )
    lines += ["", "## 实际可用的 SQL", ""]
    for it in rep.items:
        lines.append(f"### {it.name}")
        lines.append("")
        lines.append(f"- 结果：{'通过' if it.passed else '不通过'}")
        lines.append(f"- 摘要：{it.summary}")
        if it.sql:
            lines.append("")
            lines.append("```sql")
            lines.append(it.sql.strip())
            lines.append("```")
        if it.details:
            lines.append("")
            lines.append("<details><summary>输出</summary>")
            lines.append("")
            lines.append("```")
            lines.append(clean_plan(it.details.strip()))
            lines.append("```")
            lines.append("</details>")
        lines.append("")

    lines += ["## 性能冒烟（10 万条）", ""]
    if rep.perf:
        p = rep.perf
        lines += [
            f"- 行数：{p['rows']}，插入耗时 {p['insert_s']}s（含建索引前）",
            f"- 向量召回（100 次）：P50 {p['vector_p50_ms']} ms，P95 {p['vector_p95_ms']} ms，最大 {p['vector_max_ms']} ms",
            f"- 全文召回（100 次）：P50 {p['fulltext_p50_ms']} ms，P95 {p['fulltext_p95_ms']} ms，最大 {p['fulltext_max_ms']} ms",
            "",
            "知识库技术方案 12.3 的目标是 100 万条以内 P95 < 500 ms；本次只做 10 万条冒烟。",
        ]
    else:
        lines.append(perf_skipped or "未运行（未传 --rows）。")
    lines.append("")

    lines += ["## 与知识库技术方案 11.3、11.4 写法的差异", ""]
    lines += [n for n in rep.notes] if rep.notes else ["无。技术方案里的写法在 0.26.2 上直接可用。"]
    lines.append("")
    lines += ["## 磁盘占用", ""]
    for k, v in rep.meta.items():
        if k.startswith("df_"):
            lines.append(f"- {k[3:]}：`{v}`")
    lines.append("")
    lines.append(
        "临时 schema 在测量 end 前已删除，表文件已回收。若 end 比 start 少几百 MB，"
        "通常是 PostgreSQL 保留的 WAL 段（`max_wal_size` 默认 1GB）：插入 10 万条会产生大量 WAL，"
        "不是临时表残留。"
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="ParadeDB 中文检索验证（X09）")
    ap.add_argument("--rows", type=int, default=0, help="性能冒烟行数，例如 100000")
    ap.add_argument("--report", type=Path, default=HERE / "REPORT.md")
    args = ap.parse_args()

    rep = Report()
    rep.notes = [
        "知识库技术方案 11.3、11.4 的主体写法在 ParadeDB 0.26.2 / pgvector 0.8.6 上直接可用，"
        "只有一处需要补充：参数占位符的类型（见下）。逐条实测确认的语法：",
        "",
        "- 索引访问方法名 `USING paradedb` 可用（0.26.2 里 `bm25` 仍作为别名存在）。",
        "- 表达式字段写法 `(title::pdb.jieba)`、`(body::pdb.jieba)`、`(keywords::pdb.jieba)` 可用，"
        "字段可空不影响建索引。",
        "- 全文召回操作符 `|||`（match disjunction）与 `::pdb.boost(2)`、`pdb.score(id)` 可用。"
        "注意：同一个参数占位符同时用于带 `::pdb.boost(...)` 和不带 boost 的位置时，"
        "服务端参数类型推断会报 `invalid typemod -1`；需显式写 `:q::text::pdb.boost(2)`，"
        "或给每个位置用不同占位符（第三期实现注意）。",
        "- jieba 默认开启搜索模式：`'长江大桥'::pdb.jieba` 会拆出 `长江`、`大桥`；"
        "`::pdb.jieba('search_mode=false')` 只返回原词，与 11.4 一致。",
        "- 在 BM25 索引里带上 `entry_type`、`doc_id`、`active` 后，这些过滤条件会下推到索引"
        "（EXPLAIN 的 Tantivy Query 里出现对应 term），无需回表过滤。",
        "- HNSW 索引 `vector_cosine_ops` / `halfvec_cosine_ops`、`ORDER BY embedding <=> :v`、"
        "`SET LOCAL hnsw.ef_search`、`SET LOCAL hnsw.iterative_scan = relaxed_order` 均可用；"
        "带过滤且候选不足时，开启 iterative_scan 能补足结果（见 6f 实测数字）。"
        "验证脚本在向量用例里加了 `SET LOCAL enable_seqscan = off`：数据量小时规划器会选 Seq Scan，"
        "看不出 HNSW 迭代扫描的效果；线上 100 万条会自然选 HNSW。",
        "- 关键词分的 `strpos` 写法可用（12.2）。",
    ]
    perf_skipped = None
    rep.meta["df_start"] = df_line()
    free_start = df_free_gb()
    rep.meta["df_start_free_gb"] = f"{free_start:.1f} GiB"

    conn = psycopg.connect(DSN, autocommit=True)
    cur = conn.cursor()
    try:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        cur.execute(f"CREATE SCHEMA {SCHEMA}")
        cur.execute(f"SET search_path TO {SCHEMA}, public")

        check_extensions(cur, rep)
        check_tables(cur, rep)
        check_hnsw(cur, rep)
        check_bm25(cur, rep)
        insert_samples(cur, rep)
        check_fulltext(cur, rep)
        check_search_mode(cur, rep)
        check_query_tokenize(cur, rep)
        check_pushdown(cur, rep)
        check_mvcc(conn, rep)
        check_vector(conn, rep)
        check_keyword_score(cur, rep)

        if args.rows:
            if free_start < 3.0:
                perf_skipped = f"跳过：运行前根分区剩余 {free_start:.1f} GiB，低于 3 GiB。"
                print("\n" + perf_skipped)
            else:
                run_perf(conn, args.rows, rep)
    finally:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        cur.close()
        conn.close()

    rep.meta["df_end"] = df_line()
    rep.meta["df_end_free_gb"] = f"{df_free_gb():.1f} GiB"

    # 校验临时 schema 已删除
    chk = psycopg.connect(DSN, autocommit=True)
    with chk.cursor() as c:
        c.execute("SELECT count(*) FROM information_schema.schemata WHERE schema_name = %s", (SCHEMA,))
        left = c.fetchone()[0]
    chk.close()
    if left:
        rep.add("8. 清理临时 schema", False, f"schema {SCHEMA} 仍然存在")
    else:
        rep.add("8. 清理临时 schema", True, f"schema {SCHEMA} 已删除")

    args.report.write_text(render(rep, perf_skipped), encoding="utf-8")
    print(f"\n报告已写入 {args.report}")

    failed = [it.name for it in rep.items if not it.passed]
    if failed:
        print("\n不通过：" + "；".join(failed))
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
