#!/usr/bin/env python3
"""Build a SQLite database from the 蝶普 (cicada) local msgpack cache."""
import msgpack, glob, os, sqlite3, sys, datetime, collections

SRC   = "raw/wxtwj"
OUT   = "蝶普数据.sqlite"
ADT   = {1:"直通车", 2:"魔方", 3:"万相台", 4:"全站推广"}
METRICS = [
    ("total_orders",   "总成交笔数"),
    ("clicks",         "点击量"),
    ("cost",           "花费"),
    ("impressions",    "展现量"),
    ("total_gmv",      "总成交金额"),
    ("total_cart",     "总购物车数"),
    ("total_fav",      "总收藏数"),
    ("direct_gmv",     "直接成交金额"),
    ("indirect_gmv",   "间接成交金额"),
    ("direct_cart",    "直接购物车数"),
    ("direct_orders",  "直接成交笔数"),
]

if os.path.exists(OUT): os.remove(OUT)
db = sqlite3.connect(OUT)
c = db.cursor()
c.executescript("""
PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
CREATE TABLE ad_types (ad_type INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE field_meta (
  position INTEGER PRIMARY KEY, column_name TEXT NOT NULL,
  cn_name TEXT NOT NULL, source_field TEXT
);
CREATE TABLE pack_files (
  id INTEGER PRIMARY KEY, file_name TEXT NOT NULL, file_size INTEGER,
  version INTEGER, server_version_ts INTEGER,
  server_version_time TEXT, rows INTEGER, created_at TEXT
);
CREATE TABLE promotion_daily (
  shop_name TEXT NOT NULL,
  date TEXT NOT NULL,
  ad_type INTEGER NOT NULL REFERENCES ad_types(ad_type),
  taobao_id TEXT NOT NULL,
  total_orders INTEGER, clicks INTEGER, cost INTEGER, impressions INTEGER,
  total_gmv INTEGER, total_cart INTEGER, total_fav INTEGER,
  direct_gmv INTEGER, indirect_gmv INTEGER, direct_cart INTEGER,
  direct_orders INTEGER,
  src_pack_id INTEGER REFERENCES pack_files(id)
);
CREATE VIEW promotion_daily_full AS
SELECT p.*, a.name AS ad_type_name,
       CASE WHEN p.cost>0 THEN ROUND(p.total_gmv*1.0/p.cost,4) END AS roi,
       CASE WHEN p.cost>0 THEN ROUND(p.direct_gmv*1.0/p.cost,4) END AS direct_roi,
       CASE WHEN p.cost>0 THEN ROUND(p.indirect_gmv*1.0/p.cost,4) END AS indirect_roi,
       CASE WHEN p.impressions>0 THEN ROUND(p.clicks*1.0/p.impressions,6) END AS ctr,
       CASE WHEN p.clicks>0 THEN ROUND(p.total_orders*1.0/p.clicks,6) END AS cvr,
       CASE WHEN p.impressions>0 THEN ROUND(p.cost*1000.0/p.impressions,4) END AS cpm,
       CASE WHEN p.clicks>0 THEN ROUND(p.cost*1.0/p.clicks,4) END AS cpc,
       CASE WHEN p.taobao_id LIKE '1%' THEN p.taobao_id END AS hmm_id
FROM promotion_daily p JOIN ad_types a USING(ad_type);
CREATE VIEW shop_daily AS
SELECT shop_name, date, ad_type,
       SUM(total_orders) total_orders, SUM(clicks) clicks, SUM(cost) cost,
       SUM(impressions) impressions, SUM(total_gmv) total_gmv,
       SUM(total_cart) total_cart, SUM(total_fav) total_fav,
       SUM(direct_gmv) direct_gmv, SUM(indirect_gmv) indirect_gmv,
       SUM(direct_cart) direct_cart, SUM(direct_orders) direct_orders
FROM promotion_daily GROUP BY shop_name, date, ad_type;
""")

c.executemany("INSERT INTO ad_types VALUES (?,?)", sorted(ADT.items()))
c.execute("""INSERT INTO field_meta(position,column_name,cn_name,source_field) VALUES (0,'shop_name','店铺名称','店铺名称')""")
c.execute("INSERT INTO field_meta VALUES (1,'date','日期','日期')")
c.execute("INSERT INTO field_meta VALUES (2,'ad_type','广告类型','广告类型')")
c.execute("INSERT INTO field_meta VALUES (3,'taobao_id','淘宝ID','淘宝ID')")
for i,(col,cn) in enumerate(METRICS, start=4):
    c.execute("INSERT INTO field_meta VALUES (?,?,?,?)", (i,col,cn,f"无界|<广告类型>|{cn}"))

total = 0
schema_json = None
for path in sorted(glob.glob(os.path.join(SRC,"*.pack")), key=os.path.getsize):
    raw = open(path,'rb').read()
    up = msgpack.Unpacker(raw=False, strict_map_key=False); up.feed(raw)
    ver, schemas, rows = list(up)
    if schema_json is None:
        schema_json = schemas
    ts = int(os.path.basename(path).rsplit('_',1)[1].split('.')[0])
    ts_s = datetime.datetime.fromtimestamp(ts, datetime.UTC).strftime('%Y-%m-%d %H:%M:%S UTC')
    c.execute("INSERT INTO pack_files(file_name,file_size,version,server_version_ts,server_version_time,rows,created_at)"
              " VALUES (?,?,?,?,?,?,datetime('now'))",
              (os.path.basename(path), os.path.getsize(path), ver, ts, ts_s, len(rows)))
    pid = c.lastrowid
    payload = [tuple(r[:4]) + tuple(r[4:15]) + (pid,) for r in rows]
    c.executemany("INSERT INTO promotion_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", payload)
    total += len(rows)
    print(f"  {os.path.basename(path)[:16]} rows={len(rows):>7} pid={pid}")

# store the full original field/formula definition captured from the server
c.execute("CREATE TABLE schema_definition (promotion_type INTEGER, position INTEGER, name TEXT, type INTEGER, formula TEXT, aggregate_formula TEXT)")
for s in schema_json:
    for i,f in enumerate(s['promotion_fields']):
        c.execute("INSERT INTO schema_definition VALUES (?,?,?,?,?,?)",
                  (s['promotion_type'], i, f['name'], f['type'], f['formula'], f['aggregate_formula']))

c.executescript("""
CREATE INDEX idx_promo_shop_date ON promotion_daily(shop_name, date);
CREATE INDEX idx_promo_date ON promotion_daily(date);
CREATE INDEX idx_promo_adtype ON promotion_daily(ad_type);
CREATE INDEX idx_promo_tid ON promotion_daily(taobao_id);
ANALYZE;
""")
db.commit()

print(f"\nrows loaded: {total}")
for q,label in [("SELECT COUNT(*) FROM promotion_daily","promotion_daily"),
                ("SELECT COUNT(DISTINCT shop_name) FROM promotion_daily","shops"),
                ("SELECT COUNT(DISTINCT date) FROM promotion_daily","dates"),
                ("SELECT COUNT(DISTINCT taobao_id) FROM promotion_daily","taobao_ids")]:
    print(f"  {label}: {c.execute(q).fetchone()[0]}")
print("\ndate range:", c.execute("SELECT MIN(date),MAX(date) FROM promotion_daily").fetchone())
print("\nintegrity:", c.execute("PRAGMA integrity_check").fetchone()[0])
db.close()
print("written:", os.path.abspath(OUT), os.path.getsize(OUT), "bytes")
