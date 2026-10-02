import msgpack, glob, os, json, datetime, collections
files = sorted(glob.glob("raw/wxtwj/*.pack"), key=os.path.getsize)
for f in files:
    raw=open(f,'rb').read()
    up=msgpack.Unpacker(raw=False, strict_map_key=False); up.feed(raw); objs=list(up)
    schemas, rows = objs[1], objs[2]
    shops=collections.Counter(r[0] for r in rows)
    dates=[r[1] for r in rows if r[1]]
    types=collections.Counter(r[2] for r in rows)
    print("="*90)
    print(os.path.basename(f)[:16], "rows=",len(rows))
    print("  shops:", dict(shops))
    print("  date range:", (min(dates),max(dates)) if dates else None, " distinct:", len(set(dates)))
    print("  ad types:", dict(types))
