# 蝶普电商运营管理平台（淘宝版）— 本地数据导出

> **完整方法论请看 [METHOD.md](./METHOD.md)** —— 包含 App 架构、两条提取路径、
> UI 自动化的全部坑与解法、以及换 Intel 机器后的建议路线。
> **命令级的完整逆向过程在附录 A。** 本文件只讲推广数据那条。

## 这是什么

对 macOS 客户端「蝶普电商运营管理平台（淘宝版）」做数据提取的方法与工具集。

* **不修改 App、不注入代码**（本机环境限制见 [METHOD.md §6](./METHOD.md)）
* 走两条路：**解析本地 msgpack 缓存** + **AX API 自动化 App 自带导出**
* 产出两个 SQLite：推广数据（店铺×日期×广告类型×淘宝ID）和产品数据（产品×日期，201 列）

> ⚠️ **仅供自己在自己已授权的账号/机器上导出自己的数据。**
> 仓库内**不含任何真实数据、凭据或门店名**，全部已脱敏。

## 快速开始

```bash
git clone <this-repo> && cd <repo>

# 建虚拟环境（--user 装包会被 PEP668 拦，必须 venv）
python3 -m venv .venv
./.venv/bin/pip install msgpack openpyxl \
    pyobjc-framework-Quartz pyobjc-framework-ApplicationServices

# ---- 方法一：推广数据（离线，App 不用启动）----
# 先把 App 的本地缓存拷出来（路径是 App 的「可执行文件旁 /cache」）
cp -a "/Applications/蝶普电商运营管理平台（淘宝版）.app/Contents/MacOS/cache/wxtwj" raw/
./.venv/bin/python build_sqlite.py            # → 蝶普数据.sqlite

# ---- 方法二：产品数据（需要 App 已启动并登录）----
./run_range.sh 2026-09-01 2026-09-30          # → xlsx/ + 产品数据.sqlite
```

**跑方法二时不要碰 App** —— 它靠前台 UI 操作，抢焦点会失败。

### 网络受限时的克隆 / 推送

在中国大陆直连时 `github.com:443` 常被墙（`api.github.com` 和 `codeload.github.com` 一般能通）。
如果 `git clone` / `git push` 卡住：

```bash
# 拉取：走 codeload 下 tarball
curl -L -o repo.tar.gz \
  https://codeload.github.com/lifujie1992-wq/diepu-data-export/tar.gz/refs/heads/main
tar xzf repo.tar.gz && mv diepu-data-export-main diepu-export

# 推送：走 GitHub Git Data API（绕开 github.com:443）
./push.sh
```

## 仓库结构

```
METHOD.md                 ★ 方法论正文（10 节）+ 附录 A 完整逆向过程（命令级）
README.md                 本文件
cicada_client_api.txt     App 客户端 CicadaBeetleClient 全部 124 个方法签名

build_sqlite.py           .pack(MessagePack) → 推广 SQLite
build_product_db.py       多 xlsx 合并 → 产品×日期 SQLite（按 产品ID+日期 去重）
xlsx_to_sqlite.py         通用 xlsx → SQLite
diepu_export.py           ★ UI 自动化跑「导出数据表」
run_range.sh              起止日期 → 导出 + 入库 一条龙

ax.py                     AX 命中测试 / AXPress（UI 自动化的核心）
uiclick.sh / uipress.sh   命令行点击/按压封装（带重试）
click.py / click2.py      Quartz 真实鼠标事件（本机权限受限，留作参考）

frida/                    为 Intel 机器准备的 Frida hook（Apple Silicon 上跑不了）
  hook_http.js              qtng HTTP 层 hook agent
  hook_http.py              注入 host
  lldb_*.py                 lldb 版本 —— ⚠️ Rosetta 上 detach 会让 App 崩，勿用
```

## 数据产物

| 文件 | 说明 |
|------|------|
| `蝶普数据.sqlite` | 推广数据（648,827 行，9 个店铺）—— 来自本地缓存 |
| `产品数据.sqlite` | **产品×日期 数据** —— 由 App 导出的 xlsx 转换而来，见下方「产品维度数据」 |
| `raw/wxtwj/` | 从 App 里原样拷贝出来的 `.pack` 原始缓存文件（不做任何修改，便于复核） |
| `xlsx/` | App 导出的 xlsx（还原素材，已 gitignore） |

## 数据来源（怎么找到的）

App 是 Qt5 写的 x86_64 Mach-O（`Contents/MacOS/cicada`），是个**瘦客户端**：

- 没有本地数据库（`lsof` 确认运行中进程没有打开任何 SQLite 文件）
- 数据全部走 HTTP API：`/api/v3/wxtwj_msgpack/download/`、`/api/v3/wxtwj_aggregate/download/` 等
- 唯一的本地数据缓存是 `Contents/MacOS/cache/`（相对可执行文件路径）

```
Contents/MacOS/cache/
├── wxtwj/      ← 推广数据（万相台无界），10 个 .pack，MessagePack 格式  ← 本库的数据源
└── products/   ← 商品图片缓存，64 个 .webp（2.3 MB），按日期分目录
```

## `.pack` 文件格式（逆向结果）

每个 `.pack` 是一个 MessagePack 对象流，解出来是 3 个对象：

```
[0] int     版本号（恒为 1）
[1] array   4 个 schema，每个 { promotion_type, promotion_fields[] }
[2] array   数据行，每行 15 个值
```

`promotion_fields` 每个字段形如 `{name, type, formula, aggregate_formula}`，
共 24 个字段，其中：

- **下标 0–14 是存储字段** → 对应每行的 15 个值
- **下标 15–23 是公式字段**（ROI / 点击率 / CPM / 加购成本…），服务端只下发公式定义，不逐行存储

4 套 schema 的字段结构完全一致，只是同一个指标带了不同前缀：

| promotion_type | 广告类型 | 指标名前缀 |
|---|---|---|
| 1 | 直通车 | `无界\|直通车*` |
| 2 | 魔方 | `无界\|魔方*` |
| 3 | 万相台 | `无界\|万相台*` |
| 4 | 全站推广 | `无界\|全站推广*` |

原始公式定义已完整保存在 `schema_definition` 表里。

## 数据库结构

```
ad_types           广告类型字典（1 直通车 / 2 魔方 / 3 万相台 / 4 全站推广）
pack_files         10 个 .pack 的来源、大小、服务端版本时间戳、行数
promotion_daily    ★ 主表：648,827 行
field_meta         列名 ↔ 中文原始字段名 对照（可追溯）
schema_definition  服务端下发的完整 24 字段定义 + 公式（可追溯）
_meta              来源、粒度、单位说明、覆盖率说明
promotion_daily_full 视图：主表 + 广告类型名 + 派生指标（ROI/CTR/CVR/CPM/CPC）
shop_daily         视图：按 店铺 + 日期 + 广告类型 汇总
```

### `promotion_daily` 列

数据粒度：**一行 = 店铺名称 + 日期 + 广告类型 + 淘宝ID**

| 列 | 中文原名 | 说明 |
|---|---|---|
| `shop_name` | 店铺名称 | |
| `date` | 日期 | `YYYY-MM-DD` |
| `ad_type` | 广告类型 | → `ad_types` |
| `taobao_id` | 淘宝ID | 直通车/魔方里的推广单元 ID |
| `total_orders` | 总成交笔数 | |
| `clicks` | 点击量 | |
| `cost` | 花费 | ⚠️ 单位见下 |
| `impressions` | 展现量 | |
| `total_gmv` | 总成交金额 | ⚠️ 单位见下 |
| `total_cart` | 总购物车数 | |
| `total_fav` | 总收藏数 | |
| `direct_gmv` | 直接成交金额 | ⚠️ 单位见下 |
| `indirect_gmv` | 间接成交金额 | ⚠️ 单位见下 |
| `direct_cart` | 直接购物车数 | |
| `direct_orders` | 直接成交笔数 | |
| `src_pack_id` | — | 追溯回 `pack_files` |

## ⚠️ 关于单位（未确证，请勿直接当作「元」）

所有指标**按 App 下发原值原样存储，没有做任何缩放**。
App 界面上的金额带 2 位小数和「元」后缀（实测主页显示 `2,110.07 元`），
所以金额列（`cost` / `total_gmv` / `direct_gmv` / `indirect_gmv`）
**很可能是 ×100 的整数（分）**，但这一点**没有跟服务端口径对齐验证过**。

用原始值算出来的 CPM、CPC 偏高约 1 个数量级，也不完全自洽，所以按「分」理解也存疑。
在用这些数字做决策之前，建议先跟 App 界面上同一店铺同一天的显示值核对一次。

## 覆盖范围

- 日期跨度：**2026-03-24 ~ 2026-10-01**
- 店铺：**9 个**（门店名已脱敏，见下）
- 广告类型：直通车 625,963 行 / 魔方 22,700 行 / 全站推广 164 行（万相台无本地数据）
- 数据完整性：`(店铺,日期,广告类型,淘宝ID)` 无重复，`PRAGMA integrity_check` = ok

按店铺汇总（原始值，**门店名与金额已脱敏**）：

```
店铺      raw_cost      raw_gmv       roi
店铺 A    <redacted>    <redacted>    27.56
店铺 B    <redacted>    <redacted>    24.11
店铺 C    <redacted>    <redacted>    15.61
...（共 9 个店铺）
```

## 产品维度数据（产品 × 日期）

### 结论：本地磁盘上没有产品数据

翻遍整机只有 64 张商品图缓存（`cache/products/<日期>/<n>_<hash>.webp`，800×800），
**没有产品行数据文件**。产品数据是 App 运行时从服务端取、只放内存的。

判断依据：`lsof` 显示运行中的 cicada 进程没有打开任何产品数据文件；
App 刷新时只写了 `.webp` 图片和 `wxtwj/*.pack`。

### 正确路径：用 App 自带导出（已验证可用）

App「产品」页顶栏有 **导出数据表**，导出向导支持**日期范围 + 保存路径**。
实测导出的 xlsx 文件名形如 `导出营收数据表<日期>.xlsx`，落在你选的目录。

导出表的粒度就是 **产品 × 日期**，共 **201 列**：

| 分组 | 列数 | 内容 |
|---|---|---|
| 标识/维度 | 10 | 产品ID、产品标题、货号、类型、运营经理、店铺、运营计划、上架时间、数据包地址、**日期** |
| 流量 | 9 | 访客、搜索访客、手淘搜索访客、手淘推荐访客、商品浏览量、平均停留时长秒、详情页跳出率、访客价值、访客平均价值 |
| 转化/件数 | 11 | 支付件数、真实件数、试用件数、预售件数、净销售件数、支付买家数、总买家数、下单转化率、实际转化率、总转化率、总下单数 |
| 金额 | 12 | 支付额、真实销售额、试用额、净销售额、总下单额、客单价、平均货品成本、货品成本、批发成本、当日标价、吊牌价、吊牌价格总额 |
| 利润 | 8 | 毛利润、纯利润、纯利率、毛利率、强推广预估利润、货品成本占比、办公费用占比、运费占比 |
| 退款 | 13 | 当天退款件数/额/订单数、售后退款额、售中售后退款、已发货退款（件数/额/买家数）、未发货退款（件数/额/买家数）、预估退货件数、预估厂商退回、退款订单数占比、售后退款率 |
| 费用 | 18 | 总广告费用、广告占比、每单广告费用、每单试用成本、总试用成本、每单办公费用、总办公费用、每单运费、总运费、运费险（+总额）、税点（+总额）、天猫抽点（+总额）、试用税点总额、总试用成本及抽点、损耗率 |
| 加购/收藏 | 7 | 加购人数、加购件数、收藏数、真实加购率、总的加购率、真实收藏率、总的收藏率 |
| 试用/预售 | 5 | 试用订单、试用订单占比、预售买家数、预售额、支付买家占比 |
| **无界广告** | **80** | 直通车 / 魔方 / 万相台 / 全站推广 × 20 个指标（总成交笔数、点击量、花费、展现量、总成交金额、总购物车数、总收藏数、直接/间接成交金额、直接购物车数、直接成交笔数、ROI、直接ROI、间接ROI、点击率、点击转换率、CPM、加购成本、直接加购成本、直接加购率） |
| 其它 | 28 | 搜索引导真实买家数/转化率、试用占比、无界人数、总广告ROI/CPM/人数、无界消耗、总广告交易额、广告成交单价、无界总成交笔数、总广告成交、真实订单、搜索转化率、无界曝光量/点击率/成交转化率/总成交金额/ROI/CPM/总加购量/收藏量/点击量、搜索引导买家数、总广告展现量、搜索占比 |

> 注意：本地 `.pack` 里的「无界」数据（直通车/魔方/全站推广，按 淘宝ID 粒度）
> 就是被 App **按 淘宝ID 关联进产品表**、变成上面这 80 列的。
> 两者是同一份广告数据的两种粒度：
> - `蝶普数据.sqlite` = **店铺 × 日期 × 广告类型 × 淘宝ID** 的原始广告粒度（本地缓存，离线可得）
> - `产品数据.sqlite` = **产品 × 日期** 的汇总粒度（需 App 导出）

### 转换方法

```bash
cd ~/diepu-export
# 单个文件
./.venv/bin/python xlsx_to_sqlite.py --db 产品数据.sqlite "xlsx/导出营收数据表2026-10-01.xlsx"
# 多个文件一起（可追加，不重复）
./.venv/bin/python xlsx_to_sqlite.py --db 产品数据.sqlite xlsx/*.xlsx
```

生成的表：

- `product_daily` —— 201 列，列名保持 App 原始中文名（`产品ID`/`日期`/`无界|直通车花费` …），100% 无损
- `column_map` —— 列序号 ↔ 列名 对照
- `import_log` —— 每个 xlsx 的导入记录（行数、列数、大小、时间）
- 索引：`日期` / `产品ID` / `货号` / `店铺`

查询示例（某天某店的产品销售情况）：

```sql
SELECT 产品ID, 货号, 产品标题, 访客, 支付件数, 支付额, 净销售额, 毛利润, 纯利润
FROM product_daily
WHERE 日期 = '2026-09-30' AND 店铺 = '<你的店铺名>'
ORDER BY 支付额 DESC LIMIT 50;
```

## ❗ 这个推广库里**没有**什么

App 是瘦客户端，**商品 / 收入 / 聚合 / 订单 / 货件 / 试单 等数据全在服务端**，本机不落地。
App 主页实测显示「所有店铺产品数 107464」、「当前数据版本号 133223」，
说明服务端还有远超本库体量的数据。

产品维度数据走 App 自带导出获取，见上方「产品维度数据」章节。
