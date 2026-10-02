import msgpack, glob, os, json
f = [x for x in glob.glob("raw/wxtwj/*.pack") if '31fb9901' in x][0]
raw=open(f,'rb').read(); up=msgpack.Unpacker(raw=False, strict_map_key=False); up.feed(raw); objs=list(up)
schemas = objs[1]
for s in schemas:
    fl = s['promotion_fields']
    print(f"\n===== promotion_type={s['promotion_type']} =====")
    for i,fd in enumerate(fl):
        tag = "FORMULA" if fd['formula'] else "base   "
        print(f"  {i:2} [{tag}] {fd['name']:<26} type={fd['type']}")
# check uniformity: base positions identical across types
print("\n\n=== base-index sets per type ===")
for s in schemas:
    idx=[i for i,fd in enumerate(s['promotion_fields']) if not fd['formula']]
    print(s['promotion_type'], idx, len(idx))
