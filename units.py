import msgpack, glob, os, collections
S=collections.Counter(); N=collections.Counter()
per=collections.defaultdict(lambda: collections.Counter())
for f in sorted(glob.glob("raw/wxtwj/*.pack")):
    raw=open(f,'rb').read(); up=msgpack.Unpacker(raw=False, strict_map_key=False); up.feed(raw); objs=list(up)
    for r in objs[2]:
        shop,date,at,tid = r[0],r[1],r[2],r[3]
        v=r[4:]  # 11 metrics
        for i,x in enumerate(v):
            S[i]+=x; N[i]+=1
            per[(shop,at)][i]+=x
names=["总成交笔数","点击量","花费","展现量","总成交金额","总购物车数","总收藏数","直接成交金额","间接成交金额","直接购物车数","直接成交笔数"]
print("=== overall sums ===")
for i,n in enumerate(names): print(f"  {n:<10} sum={S[i]:>15,}")
print("\n=== shop/day totals (元 interpretation check) ===")
for (shop,at),c in sorted(per.items()):
    days = 97
    cost=c[2]; gmv=c[4]; imp=c[3]; clk=c[1]
    print(f"{shop[:18]:<18} at={at} cost_raw={cost:>12,} gmv_raw={gmv:>12,} imp={imp:>12,} clk={clk:>10,}  ROI={gmv/cost if cost else 0:.2f} CPM_raw/1000={cost/imp*1000 if imp else 0:.2f} CPC_raw={cost/clk if clk else 0:.2f}")
