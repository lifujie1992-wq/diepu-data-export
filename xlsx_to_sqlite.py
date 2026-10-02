#!/usr/bin/env python3
"""把蝶普「导出数据表」产出的 xlsx 转成 SQLite。

用法:
    ./.venv/bin/python xlsx_to_sqlite.py 导出营收数据表2026-10-01.xlsx [更多.xlsx ...]
    ./.venv/bin/python xlsx_to_sqlite.py --db 产品数据.sqlite xlsx/*.xlsx
"""
import sys, os, sqlite3, glob, datetime, re
import openpyxl

TAB = "product_daily"


def q(name: str) -> str:
    """SQLite 标识符引用（列名是中文/含 | 时必需）"""
    return '"' + name.replace('"', '""') + '"'


def norm(v):
    if isinstance(v, datetime.datetime):
        return v.strftime("%Y-%m-%d") if (v.hour, v.minute, v.second) == (0, 0, 0) else v.isoformat(sep=" ")
    if isinstance(v, datetime.date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def main():
    args = sys.argv[1:]
    db_path = "产品数据.sqlite"
    if args and args[0] == "--db":
        db_path, args = args[1], args[2:]
    files = []
    for a in args:
        files.extend(sorted(glob.glob(a)))
    if not files:
        print("no xlsx given"); return 1

    db = sqlite3.connect(db_path)
    c = db.cursor()
    c.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")

    created = False
    total = 0
    for path in files:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for ws in wb.worksheets:
            it = ws.iter_rows(values_only=True)
            try:
                raw_hdr = [str(h).strip() if h is not None else "" for h in next(it)]
            except StopIteration:
                continue
            # 裁掉尾部空列名（导出文件会带一批 None 列表头）
            while raw_hdr and raw_hdr[-1] == "":
                raw_hdr.pop()
            header = [h if h else f"col{i}" for i, h in enumerate(raw_hdr)]
            if not created:
                # schema 变了就重建（旧表列数不一致会插不进去）
                existing = [r[1] for r in c.execute(f"PRAGMA table_info({TAB})")]
                if existing and existing != header:
                    print(f"  [!] 旧表 {len(existing)} 列 != 新表 {len(header)} 列，重建")
                    c.execute(f"DROP TABLE IF EXISTS {TAB}")
                cols_sql = ", ".join(f"{q(h)}" for h in header)
                c.execute(f"CREATE TABLE IF NOT EXISTS {TAB} ({cols_sql})")
                c.execute("CREATE TABLE IF NOT EXISTS column_map (position INTEGER PRIMARY KEY, column_name TEXT, source_file TEXT)")
                c.executemany("INSERT OR REPLACE INTO column_map VALUES (?,?,?)",
                              [(i, h, os.path.basename(path)) for i, h in enumerate(header)])
                c.execute("""CREATE TABLE IF NOT EXISTS import_log
                             (file_name TEXT PRIMARY KEY, sheet TEXT, rows INTEGER, columns INTEGER, imported_at TEXT,
                              size_bytes INTEGER)""")
                created = True

            placeholders = ", ".join("?" * len(header))
            insert = f"INSERT OR REPLACE INTO {TAB} VALUES ({placeholders})"
            buf, n = [], 0
            for row in it:
                row = list(row) + [None] * (len(header) - len(row))
                buf.append(tuple(norm(v) for v in row[:len(header)]))
                if len(buf) >= 2000:
                    c.executemany(insert, buf); n += len(buf); buf.clear()
            if buf:
                c.executemany(insert, buf); n += len(buf)
            c.execute("INSERT OR REPLACE INTO import_log VALUES (?,?,?,?,?,?)",
                      (os.path.basename(path), ws.title, n, len(header),
                       datetime.datetime.now().isoformat(timespec="seconds"), os.path.getsize(path)))
            total += n
            print(f"  {os.path.basename(path)} [{ws.title}] rows={n} cols={len(header)}")
        wb.close()

    # 索引：日期 + 产品，便于按「每天的品维度」查询
    have = {r[1] for r in c.execute(f"PRAGMA table_info({TAB})")}
    for want, idx in [("日期", "idx_pd_date"), ("产品ID", "idx_pd_pid"), ("货号", "idx_pd_sku"), ("店铺", "idx_pd_shop")]:
        if want in have:
            c.execute(f"CREATE INDEX IF NOT EXISTS {idx} ON {TAB}({q(want)})")
    c.execute("ANALYZE")
    db.commit()
    print(f"\ntotal rows imported: {total}")
    print("db:", os.path.abspath(db_path), os.path.getsize(db_path), "bytes")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
