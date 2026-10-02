import msgpack, glob, os, collections
tot=0; allkeys=set(); ids=collections.Counter()
for f in sorted(glob.glob("raw/wxtwj/*.pack")):
    raw=open(f,'rb').read(); up=msgpack.Unpacker(raw=False, strict_map_key=False); up.feed(raw)
    objs=list(up); rows=objs[2]; tot+=len(rows)
    for r in rows:
        allkeys.add((r[0],r[1],r[2],r[3]))
        ids[(r[0],r[2])]+=1
print("total rows:", tot)
print("distinct (shop,date,adtype,taobao_id) keys:", len(allkeys))
print("=> duplicate rows:", tot-len(allkeys))
print("\nper shop/adtype distinct 淘宝ID counts:")
for k in sorted(ids): print("  ", k, ids[k])
