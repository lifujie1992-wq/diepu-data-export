import msgpack, glob, os, json, datetime
files = sorted(glob.glob("raw/wxtwj/*.pack"))
print(f"{'file(shorthash)':20} {'ver':>4} {'#schemas':>8} {'rows':>9} {'fieldcount':>10}  version_ts")
results={}
for f in files:
    raw = open(f,'rb').read()
    try:
        up = msgpack.Unpacker(raw=False, strict_map_key=False); up.feed(raw); objs=list(up)
    except Exception as e:
        print(os.path.basename(f)[:16], "PARSE FAIL", e); continue
    ver, schemas, rows = objs[0], objs[1], objs[2]
    flens = [len(s.get('promotion_fields',[])) for s in schemas]
    ts = int(os.path.basename(f).rsplit('_',1)[1].split('.')[0])
    ts_s = datetime.datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d %H:%M')
    print(f"{os.path.basename(f)[:16]:20} {ver:>4} {len(schemas):>8} {len(rows):>9} {str(flens):>10}  {ts_s}")
    results[f]=(ver,schemas,rows)
# schema detail for the biggest
big = max(results, key=lambda k: len(results[k][2]))
ver,schemas,rows = results[big]
print("\n=== biggest pack:", os.path.basename(big)[:16], "rows:", len(rows))
for s in schemas:
    fields=s['promotion_fields']
    print(f"\n-- promotion_type={s['promotion_type']}  fields={len(fields)}")
    for i,fl in enumerate(fields):
        print(f"   {i:2} {fl['name']:<28} type={fl['type']} formula={fl['formula'][:40]!r} agg={fl['aggregate_formula'][:30]!r}")
    break
print("\nrow length distribution:", sorted({len(r) for r in rows}))
print("sample row:", json.dumps(rows[0], ensure_ascii=False))
