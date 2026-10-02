#!/usr/bin/env python3
"""把多个「导出数据表」xlsx 合并成 SQLite（产品 × 日期 粒度）。

设计:
  product_daily   有日期的行，唯一键 (产品ID, 日期)   —— 重复导入自动覆盖
  product_master  无日期的行，唯一键 产品ID          —— 产品维度信息
  import_log      每个 xlsx 的导入记录
  column_map      列序号 ↔ 列名

用法:
  build_product_db.py --db 产品数据.sqlite xlsx/*.xlsx
"""
import argparse
import datetime
import glob
import os
import sqlite3
import sys

import openpyxl


def q(name):
    return '"' + str(name).replace('"', '""') + '"'


def norm(v):
    if isinstance(v, datetime.datetime):
        if (v.hour, v.minute, v.second) == (0, 0, 0):
            return v.strftime("%Y-%m-%d")
        return v.isoformat(sep=" ")
    if isinstance(v, datetime.date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="产品数据.sqlite")
    ap.add_argument("files", nargs="+")
    a = ap.parse_args()

    files = []
    for f in a.files:
        files.extend(sorted(glob.glob(f)))
    if not files:
        print("no input"); return 1

    db = sqlite3.connect(a.db)
    c = db.cursor()
    c.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")

    created = False
    total_daily = total_master = 0

    for path in files:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for ws in wb.worksheets:
            it = ws.iter_rows(values_only=True)
            try:
                raw = [str(h).strip() if h is not None else "" for h in next(it)]
            except StopIteration:
                continue
            while raw and raw[-1] == "":
                raw.pop()
            header = [h if h else f"col{i}" for i, h in enumerate(raw)]
            cols = {h: i for i, h in enumerate(header)}
            if "产品ID" not in cols or "日期" not in cols:
                print(f"  skip {os.path.basename(path)}: 缺少 产品ID/日期"); continue

            if not created:
                c.execute(f"CREATE TABLE IF NOT EXISTS product_daily ({', '.join(q(h) for h in header)})")
                c.execute(f"CREATE TABLE IF NOT EXISTS product_master ({', '.join(q(h) for h in header)})")
                # 唯一键必须在写入前就存在，否则 INSERT OR REPLACE 去不了重
                c.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_daily ON product_daily(产品ID, 日期)")
                c.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_master ON product_master(产品ID)")
                for col, idx in [("日期", "idx_pd_date"), ("产品ID", "idx_pd_pid"),
                                 ("货号", "idx_pd_sku"), ("店铺", "idx_pd_shop")]:
                    c.execute(f"CREATE INDEX IF NOT EXISTS {idx} ON product_daily({q(col)})")
                c.execute("CREATE INDEX IF NOT EXISTS idx_pm_shop ON product_master(店铺)")
                c.execute("CREATE TABLE IF NOT EXISTS column_map (position INTEGER PRIMARY KEY, column_name TEXT)")
                c.executemany("INSERT OR REPLACE INTO column_map VALUES (?,?)", list(enumerate(header)))
                c.execute("""CREATE TABLE IF NOT EXISTS import_log
                             (file_name TEXT, sheet TEXT, daily_rows INTEGER, master_rows INTEGER,
                              columns INTEGER, size_bytes INTEGER, imported_at TEXT,
                              PRIMARY KEY (file_name, sheet))""")
                created = True

            daily_sql = f"INSERT OR REPLACE INTO product_daily VALUES ({', '.join('?'*len(header))})"
            master_sql = f"INSERT OR REPLACE INTO product_master VALUES ({', '.join('?'*len(header))})"
            bd, bm, nd, nm = [], [], 0, 0
            for row in it:
                vals = list(row) + [None] * (len(header) - len(row))
                vals = tuple(norm(v) for v in vals[:len(header)])
                if vals[cols["日期"]] is None:
                    bm.append(vals); nm += 1
                    if len(bm) >= 2000:
                        c.executemany(master_sql, bm); bm.clear()
                else:
                    bd.append(vals); nd += 1
                    if len(bd) >= 2000:
                        c.executemany(daily_sql, bd); bd.clear()
            if bd:
                c.executemany(daily_sql, bd)
            if bm:
                c.executemany(master_sql, bm)
            c.execute("INSERT OR REPLACE INTO import_log VALUES (?,?,?,?,?,?,?)",
                      (os.path.basename(path), ws.title, nd, nm, len(header),
                       os.path.getsize(path), datetime.datetime.now().isoformat(timespec="seconds")))
            total_daily += nd; total_master += nm
            print(f"  {os.path.basename(path)} [{ws.title}] daily={nd} master={nm} cols={len(header)}")
        wb.close()

    # 索引已在建表时创建（写入前），这里只做统计
    cd = {r[1] for r in c.execute("PRAGMA table_info(product_daily)")}
    cm = {r[1] for r in c.execute("PRAGMA table_info(product_master)")}
    if "产品ID" in cm and not cm <= cd:
        pass
    c.execute("ANALYZE")
    db.commit()

    print(f"\n本次导入 daily={total_daily} master={total_master}")
    print("  库内 daily:", c.execute("SELECT COUNT(*) FROM product_daily").fetchone()[0],
          " master:", c.execute("SELECT COUNT(*) FROM product_master").fetchone()[0])
    r = c.execute("SELECT MIN(日期), MAX(日期), COUNT(DISTINCT 日期) FROM product_daily").fetchone()
    print("  日期跨度:", r[0], "~", r[1], " 天数:", r[2])
    db.close()
    print("db:", os.path.abspath(a.db), os.path.getsize(a.db), "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
