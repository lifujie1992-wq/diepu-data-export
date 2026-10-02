import msgpack, glob, os, collections
f=[x for x in glob.glob("raw/wxtwj/*.pack") if '31fb9901' in x][0]
raw=open(f,'rb').read(); up=msgpack.Unpacker(raw=False, strict_map_key=False); up.feed(raw); objs=list(up)
rows=objs[2]
print("=== sample rows (big pack) ===")
for r in rows[:3]+rows[100000:100003]+rows[-3:]:
    print(r)
print("\n=== value types per column ===")
tc=[collections.Counter() for _ in range(15)]
for r in rows[:200000]:
    for i,v in enumerate(r): tc[i][type(v).__name__]+=1
for i,c in enumerate(tc): print(f"  col{i}: {dict(c)}")
print("\n=== distinct 淘宝ID (col3) in big pack ===", len({r[3] for r in rows}))
print("=== distinct dates ===", len({r[1] for r in rows}))
print("=== distinct shops ===", {r[0] for r in rows})
