# 蝶普电商运营管理平台（淘宝版）— 数据提取方法

> 本文件是**方法论交接文档**。目标：把 App 里的数据搞成本地 SQLite。
> 环境：macOS / Apple Silicon（App 是 x86_64，跑在 Rosetta 下）。

---

## 0. TL;DR

| 目标数据 | 方法 | 状态 |
|---|---|---|
| **推广数据**（万相台无界，店铺×日期×广告类型×淘宝ID） | 直接解析 App 本地 msgpack 缓存 | ✅ 完成，离线可得 |
| **产品数据**（产品×日期，201 列销售/利润/退款/广告） | 用 AX API 自动化 App 自带「导出数据表」→ xlsx → SQLite | ✅ 管道打通 |
| 商品/收入/订单/聚合等其它模块 | 服务端，本地无缓存；但已定位到协议层入口（§7） | ⚠️ 待 Intel 机器继续 |

> **完整逆向过程（命令级）与踩坑记录见 [附录 A](#附录-a完整逆向过程命令级)。**

---

## 1. 目标 App 的架构（逆向结论）

```
/Applications/蝶普电商运营管理平台（淘宝版）.app/
└── Contents/MacOS/
    ├── cicada                 ← 主程序，Mach-O x86_64，未签名，Qt5
    └── cache/                 ← 唯一的本地数据目录（路径相对可执行文件！）
        ├── wxtwj/*.pack       ← 推广数据，MessagePack，10 个文件 51MB
        └── products/**.webp   ← 商品图缓存，64 张
```

**关键判定：这是瘦客户端。**

> 这一节的每一条结论都附了**推导过程和命令**，见 **[附录 A](#附录-a完整逆向过程命令级)**。

- `lsof -p <cicada>` → 运行中**不打开任何 SQLite 文件**，本地没有数据库
- 数据全部走 HTTP：`/api/v3/wxtwj_msgpack/{version/query,download}/`、
  `/api/v3/wxtwj_aggregate/download/`、`/api/v1/mac_update/` 等
- 网络层用 `qtng::HttpSession` / `HttpRequest` / `HttpResponse`（qtnetworkng，静态链接进主程序），
  业务层叫 `CicadaBeetleClient`（蝉/甲虫/昆虫，命名全是虫）
- `~/Library/Preferences/com.gigacores.cicada.plist` 是唯一的其它落地文件

**日志里不要被 `QSQLiteDriver` 字符串骗了** —— 那只是 Qt SQL 模块名被静态链进来，
没有 sqldrivers 插件、也没有 QtSql framework，运行时一个 DB 都不开。

---

## 2. 方法一：本地缓存 → SQLite（推广数据）✅

### 2.1 `.pack` 格式（逆向结果）

每个 `.pack` 是 **MessagePack 对象流**（多个对象拼接，不是单个对象）。
`msgpack.unpackb()` 会报 `ExtraData` —— **这不是格式错误**，换 `msgpack.Unpacker` 迭代：

```
[0] int      版本号（恒为 1）
[1] array    4 个 schema: { promotion_type, promotion_fields[] }
[2] array    数据行，每行 15 个值
```

`promotion_fields` 每个字段是 `{name, type, formula, aggregate_formula}`，共 **24 个**：

- **下标 0–14 = 存储字段** → 正好对上每行 15 个值
- **下标 15–23 = 公式字段**（ROI/点击率/CPM/加购成本…）→ 服务端只下发公式，不逐行存储

> 通用技巧：**字段数 > 行宽时，差值通常就是服务端公式字段**。
> 用 `formula` 是否为空来切分，能自动算出真实列数。

4 套 schema 结构完全一致，只是指标名带不同前缀：

| promotion_type | 广告类型 | 前缀 |
|---|---|---|
| 1 | 直通车 | `无界\|直通车*` |
| 2 | 魔方 | `无界\|魔方*` |
| 3 | 万相台 | `无界\|万相台*` |
| 4 | 全站推广 | `无界\|全站推广*` |

### 2.2 产物

`build_sqlite.py` → `蝶普数据.sqlite`（**648,827 行 / 9 店铺 / 2026-03-24~2026-10-01**）

```
promotion_daily      主表：店铺+日期+广告类型+淘宝ID+11 个指标
promotion_daily_full 视图：+ 派生 ROI/CTR/CVR/CPM/CPC
shop_daily           视图：店铺+日期+广告类型 汇总
pack_files           来源文件/版本时间戳/行数（可追溯）
field_meta           列名 ↔ 中文原名
schema_definition    服务端下发的完整 24 字段定义 + 公式
_meta                来源/粒度/单位/覆盖率说明
```

### 2.3 ⚠️ 未决问题：推广数据的金额单位

指标列**按 App 下发原值原样存储，没做缩放**。
用原始值算出的 CPM/CPC 偏高约一个数量级且不完全自洽。
界面显示 2 位小数 + 「元」→ 很可能是 ×100（分），**但未与服务端口径对齐验证**。
已在 `_meta.units_note` 明确标注，**别直接当元用**。

---

## 3. 方法二：UI 自动化跑「导出数据表」（产品数据）✅

### 3.1 为什么用这条

| 路径 | 结论 |
|---|---|
| 本地磁盘找产品数据 | ❌ 一行都没有，只有 64 张商品图 |
| 写纯 Python 客户端打 API | ❌ 服务器拒绝一切非 App 的 TLS 客户端（见 §5） |
| Frida 注入 | ❌ Frida 17/16 的 macOS agent **只有 arm64/arm64e**，注入不了 Rosetta x86_64 |
| lldb 附加 | ⚠️ 能 attach、能下断点，但 **detach 会踩坏 Rosetta 的 dyld 惰性绑定 stub → App 崩溃**（见 §6）|
| **AX API 自动化自带导出** | ✅ 不侵入、不崩、可重复 |

### 3.2 完整流程

```
激活 cicada → AXPress「导出数据表」(213,63)
  → 等向导窗口出现（800×574）
  → AXSetAttributeValue 写「日期」和「保存路径」
  → ★ Tab + Return 提交（关键！见 §4.1）
  → AXPress「下一步」
  → 轮询文件大小直到稳定（~2 天约 156s，文件 40MB）
  → AXPress「完成」+ 关掉「导出成功。是否打开？」（点 否N）
```

脚本：`diepu_export.py`

```bash
./.venv/bin/python diepu_export.py --date "2026-09-14/2026-09-15" --out /path/out.xlsx
```

### 3.3 向导里的坐标（相对向导窗口左上角）

| 元素 | 偏移 |
|---|---|
| 日期输入框 | (+373, +166) |
| 保存路径输入框 | (+373, +193) |
| 下一步(N) | (+707, +548) |
| 完成(F) | (+714, +556) |
| 确认框「否N」 | 窗口 217×127，(+122, +95) |

其余锚点（屏幕绝对坐标，App 主窗口固定在 (0,25) 1440×817）：
`导出数据表` = (213, 63)，主窗口尺寸 1440×817，向导窗口 800×574 出现在 (310,134)。

### 3.4 导出数据的形态（重要）

- **列数：201**（文件头会多出 9 个空列表头，读的时候要裁掉）
- **粒度：产品 × 日期** —— 唯一键是 `(产品ID, 日期)`
- 某个产品在范围内某天有数据 → 一行；没数据的产品 → 一行且 `日期 = None`
- 实测：107,464 个产品；2 天范围 = 128,878 行（每产品最多 2 行）
- 文件规模：单日 12.8MB / 2 天 40MB

**列分组**（201 列）：

| 分组 | 列数 | 例子 |
|---|---|---|
| 标识/维度 | 10 | 产品ID、产品标题、货号、类型、运营经理、店铺、运营计划、上架时间、数据包地址、**日期** |
| 流量 | 9 | 访客、搜索访客、手淘搜索/推荐访客、商品浏览量、平均停留时长秒、详情页跳出率、访客价值 |
| 转化/件数 | 11 | 支付件数、真实件数、试用件数、预售件数、净销售件数、支付买家数、总买家数、下单转化率… |
| 金额 | 12 | 支付额、真实销售额、试用额、净销售额、总下单额、客单价、货品成本、批发成本、当日标价、吊牌价… |
| 利润 | 8 | 毛利润、纯利润、纯利率、毛利率、强推广预估利润、货品成本占比、办公费用占比、运费占比 |
| 退款 | 13 | 当天退款件数/额/订单数、售后退款额、售中售后退款、已发货退款、未发货退款、预估退货件数… |
| 费用 | 18 | 总广告费用、广告占比、每单广告费用、税费/天猫抽点、运费、运费险、办公费用… |
| 加购/收藏 | 7 | 加购人数/件数、收藏数、真实加购率、各种收藏率 |
| 试用/预售 | 5 | 试用订单、试用订单占比、预售件数/买家数/额、支付买家占比 |
| **无界广告** | **80** | 直通车 / 魔方 / 万相台 / 全站推广 × 20 个指标 |
| 其它 | 28 | 搜索引导、总广告 ROI/CPM、无界消耗/曝光/点击、已发货退款买家数… |

> **两条方法的连接点**：本地 `.pack` 里那份按 `淘宝ID` 粒度的推广数据，
> 就是被 App **按 淘宝ID join 进产品表**、变成上面那 80 列广告列的。
> 同一份广告数据的两种粒度。

**单位**：这个导出里的金额是**真实元值带小数**（如 `770995.11`），不存在 §2.3 的缩放问题。

### 3.5 入库

`build_product_db.py` —— 多文件合并，**按 (产品ID, 日期) 唯一键去重**：

```bash
./.venv/bin/python build_product_db.py --db 产品数据.sqlite xlsx/*.xlsx
```

```
product_daily    有日期的行，唯一索引 (产品ID, 日期) —— 重复导入自动覆盖
product_master   无日期的行，唯一索引 产品ID —— 产品维度信息（79,105 行）
import_log       每个 xlsx 的导入记录
column_map       列序号 ↔ 列名（列名保留 App 原始中文名，100% 无损）
```

> ⚠️ **唯一索引必须在写入前就创建**，否则 `INSERT OR REPLACE` 去不了重。

查询示例：

```sql
SELECT 日期, COUNT(DISTINCT 产品ID) 产品数, SUM(访客) 访客,
       SUM(支付件数) 支付件数, SUM(支付额) 支付额, SUM(净销售额) 净销售额
FROM product_daily GROUP BY 日期 ORDER BY 日期;
```

---

## 4. 踩过的坑（这部分最值钱）

### 4.1 ★ AXValue 写入 ≠ 提交（本次最大的坑）

用 `AXUIElementSetAttributeValue(field, "AXValue", ...)` 写「日期」输入框，
**回读是成功的、界面上也显示新值**，但 App 内部状态没更新 —— 导出时仍然用旧日期。

原因：Qt 的 `QLineEdit` 只在 **focus-out 或 Return** 时才发 `editingFinished`，
App 监听的是那个信号，不是控件文本。

**解法**：写完值后补发 `Tab` + `Return`：

```python
osascript -e 'tell application "System Events" to key code 48'   # Tab
osascript -e 'tell application "System Events" to key code 36'   # Return
```

**验证方式**：设不同日期导出，比较输出内容。修之前两次导出**行数/日期分布/字节数完全一样**；
修之后 09-15 那次从 12.8MB 变成 23MB、有数据的行从 10,747 变成 28,359。

> 教训：**自动化里"写进去了"和"生效了"是两件事，一定要用输出验证。**

### 4.2 AX 命中测试只认前台 App

`AXUIElementCopyElementAtPosition(systemWide, x, y)` 返回的是**最前台 App**在该点的元素。

- 如果 cicada 不是前台，命中到的是别的 App（或直接报 `-25204`）
- `System Events` 的 `click at` 内部就是做这个命中测试 → 所以它
  **在前台 App 里找不到元素时报 `-25204`（类型错误）**，而不是去点那个坐标

**解法**：每次操作前先激活：

```applescript
tell application "System Events" to tell process "cicada" to set frontmost to true
```

`open -a "<app>"` 也可以，但 python 里调 `NSRunningApplication.activateWithOptions_` **无效**。

### 4.3 Quartz `CGEventPost` 会被静默丢弃

从终端里的 python 投递 `CGEventPost(kCGHIDEventTap, ...)`，
坐标、次数都对，**App 完全没反应**（负责投递的进程没有辅助功能权限）。
偶尔能成功一次，不可依赖。**不要走这条路。**

### 4.4 侧边栏导航不是 AX 元素

侧边栏（主页/产品/数据透视/趋势分析/万相相界/试用订单/货品供应/店铺配置）在 AX 里
**hit-test 只返回 `AXWindow`**，`AXPress` 等于对窗口空按一下 → 切不了页。
`AXChildren` 也枚举不出来（顶层 `kids=0`，Qt 只暴露了部分控件）。

**结论**：**能自动化的只有已经暴露成 `AXButton` 的元素**（例如工具栏的
`新建产品/导入数据表/导出数据表/用户手册/更新详解`、向导里的按钮、确认框按钮）。

> 如果以后要切页面：需要另外想办法（真实鼠标事件需要先给终端授予辅助功能权限并验证）。

### 4.5 `AXPress` 的返回值不可信

`AXUIElementPerformAction(el, "AXPress")` 经常返回 `-25204`（类型错误），
**但按钮实际被按下了**。判断是否生效要看副作用（窗口是否出现、文件是否写出），不要看返回值。

### 4.6 截图坐标的坑

- `screencapture -l <windowid>` 抓单个窗口，**能绕开窗口层叠**（比 `-x` 全屏截图可靠得多）
- 但 `-l` 的输出**裁掉了标题栏**，所以图像 y 坐标 ≠ 屏幕 y 坐标（差约 26pt）
- 不要用截图量坐标，**优先用 AX 返回的 `AXPosition`/`AXSize`**（权威）

### 4.7 前台被终端抢走

跑命令时终端会保持前台。要么在**同一个进程内**先激活 App 再做 AX 操作，
要么把终端窗口挪到角落。

---

## 5. 为什么"写纯 Python 客户端"这条路走不通

同机器、同源 IP、同目标，**只有 cicada 自己能连上 API**：

| 探测 | 结果 |
|---|---|
| DNS | `<客户实例>.ai-pi.com` → `<服务器IP>`（真实公网 IP） |
| TCP 443 | ✅ 可连 |
| `openssl s_client` 各变体（tls1.2/1.3/指定 cipher/ALPN h2/http1.1/no_ticket） | ❌ 全部被 RST（`errno=54`） |
| 明文 HTTP | ❌ 无响应 |
| 8443 端口 | ❌ 超时 |
| 服务器行为 | 443 上**沉默等客户端先说话** → 是 TLS 类握手 |
| plist 内容 | **没有任何证书或 token** → 认证只有用户名 + 密码 |

结论：服务器在按 **TLS 指纹（JA3）或 ClientHello 细节**筛客户端。

**顺带拿到的信息**（`com.gigacores.cicada.plist`）：

- 用户名 `<用户名·已脱敏>`
- `login.epassword` = zlib 压缩（前 4 字节是长度前缀），解出明文密码
- 内嵌证书（在二进制里，做 pin）：
  - `C=CN, ST=Fujian, L=Xiamen, O=GigaCores Ltd., OU=dev, CN=ai-pi.com`（CA:TRUE，有效至 2030）
  - `CN=config.ai-pi.com`（由 `GigaCores Root CA` 签发）
- **二进制里没有私钥** → 不是 mTLS

> Shadowrocket 曾开着 TUN（fake-IP `198.18.0.0/15`，`MacPacketTunnel.appex`），
> 代理端口 `127.0.0.1:1082` 对 google/baidu/xmdiepu.cn 都通 200，**唯独 `*.ai-pi.com` 被拒**。
> 关掉 TUN 后仍是同样结果 → 不是 Shadowrocket 拦，是服务器拦。

---

## 6. 为什么 lldb 和 Frida 都不行（Apple Silicon 特有）

App 是 x86_64，在 Apple Silicon 上跑 **Rosetta**。

### Frida
```
frida 17.11.0: frida-agent.dylib = arm64 + arm64e（无 x86_64 切片）
frida 16.7.19: 同样
→ attach 报 "unable to read from process memory" / "process is dead"
```

### lldb
**能** attach 到 Rosetta 进程、能下断点（21 个全命中），但 **detach 会让 App 崩**：

```
SIGABRT  ×2  主线程停在 CFRunLoop（空闲态）→ 被 detach 打断
SIGSEGV  ×2  栈里是 DYLD-STUB$$_os_semaphore_create / DYLD-STUB$$QObject::connect
             + Rosetta runtime 帧
             → dyld 惰性绑定 stub 未解析就被执行
```

对照实验：**完全不插桩跑 90 秒，零崩溃** → 崩溃 100% 是调试器 detach 造成的。
**不要对 Rosetta 进程用 lldb。**

---

## 7. 明天换 Intel 机器可以做什么（重点）

Intel Mac 上 App 会**原生跑 x86_64，不走 Rosetta**，上面 §6 的限制全部消失：

| 能力 | Apple Silicon | Intel |
|---|---|---|
| Frida 注入 | ❌ 无 x86_64 agent | ✅ **可用** |
| lldb attach/detach | ❌ 踩坏 dyld | ✅ 应该正常 |
| `DYLD_INSERT_LIBRARIES` 注入自写 dylib | ⚠️ 可行但没试 | ✅ 更简单 |

### 建议的下一步 ①：hook `hugeload_wire`，直接拿产品数据（最优先）

**这是本次最重要的发现。** 符号里藏着产品数据的协议层：

```cpp
hugeload_wire::readSchema(qtng::MsgPackStream&, QList<hugeload_wire::Field>&)
hugeload_wire::readProduct(qtng::MsgPackStream&, QList<hugeload_wire::Field> const&,
                           QList<hugeload_wire::Field> const&, Product&)
hugeload_wire::decodeRow(QList<hugeload_wire::Field> const&, QByteArray const&, QDateTime*)

qtng::MsgPackStream& operator>>(qtng::MsgPackStream&, Product&)
qtng::MsgPackStream& operator>>(qtng::MsgPackStream&, DayIncome&)
```

含义：

1. **产品数据本来就是 msgpack 从服务端下来的**，UI 导出只是个下游消费者
2. 流程是：`readSchema` 拿字段定义（`QList<Field>`）→ `readProduct` 逐行解出 `Product`
3. `decodeRow(字段定义, QByteArray, QDateTime*)` —— 行是**按 schema 逐字段解码的原始字节**，
   客户端自己解释（跟 `.pack` 完全一个思路）
4. **`DayIncome`（每日收入）是独立类型且也有 `operator>>`** ——
   这就是「**每天的品维度的销售情况**」在协议层的对应物

→ **只要 hook `hugeload_wire::readProduct` / `operator>>(MsgPackStream&, DayIncome&)`，
就能直接 dump 每日产品数据，不用点 UI、不用导出 xlsx。**

配合 `CicadaBeetleClient` 的 API 还可以主动触发：

```cpp
loadProductSync(int, bool)
loadProductPromotionsSync(QString const&, QDate const&, QDate const&)   // ← 直接吃日期区间
updateExtendColumns(Product&, DayIncome&, QString const&)              // ← 印证 (Product, DayIncome) 是配对
incomeMinDate()                                                       // ← 问出历史最早日期
```

完整 124 个方法已导出到 **`cicada_client_api.txt`**。

### 建议的下一步 ②：hook HTTP 层，把协议逆出来

如果 ① 不够，退一步到 HTTP 层。已经确认为 Frida 准备好的符号
（`nm -a` 就能拿到，qtng 是静态链接进主程序的）：

```text
__ZN4qtng11HttpSession3getERK7QString                       get(QString const&)
__ZN4qtng11HttpSession3getERK7QStringRK9QUrlQuery           重载
__ZN4qtng11HttpSession4postERK7QStringRK10QByteArray        post(QString, QByteArray)
__ZN4qtng12HttpResponse7setBodyERK10QByteArray              响应体（含 msgpack）
__ZN4qtng12HttpResponse13setStatusCodeEi                    状态码
__ZN4qtng11HttpRequest6setUrlERK4QUrl                       请求 URL
__ZN4qtng11HttpRequest9setMethodERK7QString                 请求方法
```

`frida/hook_http.js` + `frida/hook_http.py` 已经写好（针对这套符号），到 Intel 上直接能跑。

Qt5 容器读取方法（脚本里已实现）：

```
QArrayData { ref(i32) size(i32) alloc(u32)+reserved [pad] offset(i64) data[] }
  QString   : d = *(void**)obj ; size = *(i32*)(d+4) ; data = (char*)d + *(i64*)(d+16)  → UTF-16LE
  QByteArray: 同上，data 按字节读
```

> **成员函数注意 ABI**：`rdi` 是 `this`；如果返回的是**非平凡结构体**（如 `HttpResponse`），
> 还要再多一个隐藏的 sret 指针 → URL 实际在 `rsi` 或 `rdx`。
> 脚本里用了"自动探测哪个寄存器能解出合法 URL"的办法，别硬编码索引。

拿到协议之后就能：
1. 写成**纯 Python 客户端**（如果服务器还拦，就用同一台机器上的 App 进程代发）
2. 或者继续用 Frida 驱动 App 自己取数，写成 SQLite —— 真正的定时自动化

### 建议的下一步 ③：其它值得顺手试的

- **侧边栏导航**：Intel 上 Quartz 事件权限问题一样存在，但可以用真实鼠标事件 + 给终端授权辅助功能，或者用 `cliclick`
- **数据透视 / 趋势分析页**：可能能出"产品×日期"的透视表，值得看一眼
- **导出耗时**：2 天 ≈ 156s，时间大致跟输出行数成正比。全量历史（如 192 天）可能几个 GB、几小时

---

## 8. 文件清单

```
~/diepu-export/
├── METHOD.md                 ← 本文件
├── README.md                 ← 早期说明（推广数据部分）
│
├── 蝶普数据.sqlite            ← 推广数据 648,827 行 / 132MB（方法一）
├── 产品数据.sqlite            ← 产品×日期 56,892 行 / 96MB（方法二，含 2 天样本）
│
├── build_sqlite.py           ← .pack(msgpack) → 推广 SQLite
├── cicada_client_api.txt     ← ★ CicadaBeetleClient 全部 124 个方法（明天的藏宝图）
├── xlsx_to_sqlite.py         ← 通用 xlsx → SQLite
├── build_product_db.py       ← 多 xlsx 合并 → 产品×日期 SQLite（按 产品ID+日期 去重）
├── diepu_export.py           ← ★ UI 自动化跑「导出数据表」
├── run_range.sh              ← 起止日期 → 导出 + 入库 一条龙
│
├── ax.py                     ← AX 命中测试 / AXPress / 祖先链诊断
├── uiclick.sh / uipress.sh   ← 命令行点击/按压封装（带重试）
├── click.py / click2.py      ← Quartz 真实鼠标事件（本会话被丢弃，留作参考）
│
├── frida/                    ← 为 Intel 机器准备的 Frida hook（本机跑不了）
│   ├── hook_http.js          ← qtng HTTP 层 hook agent
│   ├── hook_http.py          ← 注入 host
│   ├── lldb_cap.py           ← lldb 版本（⚠️ 会崩，勿在 Rosetta 上用）
│   └── lldb_driver.py        ← lldb 异步驱动（⚠️ 同上）
│
├── raw/wxtwj/                ← 从 App 原样拷出的 10 个 .pack（备查）
├── xlsx/                     ← 导出的 xlsx
└── logs/                     ← 导出/入库日志
```

---

## 9. 复现步骤

```bash
cd ~/diepu-export

# ---- 方法一：推广数据（离线，不需要 App 运行）----
cp -a "/Applications/蝶普电商运营管理平台（淘宝版）.app/Contents/MacOS/cache/wxtwj" raw/
./.venv/bin/python build_sqlite.py            # → 蝶普数据.sqlite

# ---- 方法二：产品数据（需要 App 在运行且已登录）----
# 1) 启动 App 并确认已登录
# 2) 跑指定日期范围的导出（会自己驱动 UI）
./run_range.sh 2026-09-01 2026-09-30          # → xlsx/ + 产品数据.sqlite

# 单次导出（不自动入库）
./.venv/bin/python diepu_export.py --date "2026-09-14/2026-09-15" --out "$PWD/xlsx/demo.xlsx"

# 多个 xlsx 合并入库（重复日期自动覆盖）
./.venv/bin/python build_product_db.py --db 产品数据.sqlite xlsx/*.xlsx
```

**运行方法二时不要碰 App** —— 它是靠前台 UI 操作的，抢焦点会失败。

---

## 10. 还没解决的

1. **Sidebar 导航**：无法自动化切到 数据透视 / 趋势分析 / 万相无界 等页
   （这些侧边栏项不是 AX 元素，见 §4.4）
2. **服务端其它模块**：商品/收入/订单/聚合数据本地无缓存。
   已在符号里定位到协议层入口（`hugeload_wire` / `DayIncome`，见 §7），待 Intel 机器实现
3. **推广数据单位**：金额列疑似 ×100（分），未与服务端验证（§2.3）
4. **全量历史**：只验证到 2 天范围（≈156s / 40MB）；长范围（如 192 天）的时间和体积待评估
5. **纯 Python 客户端**：被服务器 TLS 指纹挡住（§5），需要 Frida 或换 Intel 机器才能继续
6. **App 稳定性**：本会话里被 lldb 弄崩过多次（§6）。**不要再对 Rosetta 进程用 lldb。**

---

# 附录 A：完整逆向过程（命令级）

> 这一节记录**怎么一步步得出上面那些结论的**，包括走过的弯路。
> 目标是明天在 Intel 机器上能直接从中间接着做，不用重走一遍。

## A.0 起点

任务：把 App 里的数据搞成本地 SQLite。
第一件要做的事不是找数据，而是**判断这个 App 是厚客户端还是瘦客户端** ——
这决定了后面全部路线。

## A.1 静态侦察三连

```bash
BIN="/Applications/蝶普电商运营管理平台（淘宝版）.app/Contents/MacOS/cicada"

file "$BIN"
# Mach-O 64-bit executable x86_64          ← 关键：Intel 产物，在 Apple Silicon 上走 Rosetta

ls -la "$BIN"        # 12,945,208 字节（13MB，单文件）

otool -L "$BIN"
# @rpath/QtCharts、QtDataVisualization、QtWidgets、QtGui、QtCore （全是 5.15.2）
# /usr/lib/libz.1.dylib、libSystem、libc++
# ← 结论1：Qt5 应用
# ← 结论2：只有框架被动态链接，业务代码和网络库都在主程序里（静态链接）
# ← 结论3：没有 QtNetwork、没有 QtSql

codesign -dv --verbose=2 "$BIN"
# code object is not signed at all
# ← 未签名 → 没有 hardened runtime → DYLD_INSERT_LIBRARIES / 调试器注入的门是开的

plutil -p ".../Info.plist"
# CFBundleExecutable = cicada
# NSAllowsArbitraryLoads = 1        ← 允许明文 HTTP，暗示要连自己的服务器
# CFBundleURLSchemes = [diepuMAC]   ← 自定义 URL scheme
# LSMinimumSystemVersion = 10.10，DTSDKName = macosx10.14
```

## A.2 符号抽取与 demangle（本次最大的信息源）

```bash
nm -a "$BIN" | awk '{print $NF}' > /tmp/re.nm.txt     # 17,285 个符号
grep -c 'qtng' /tmp/re.nm.txt                          # 2,765 个 qtng 符号
c++filt < /tmp/re.nm.txt > /tmp/re.dem.txt             # demangle
```

**光靠这个就拿到了整套架构。** 关键发现：

```text
N4qtng13MsgPackStreamE          → 用 MessagePack 做序列化
N4qtng11HttpSessionE            → HTTP 客户端（qtnetworkng 库）
N4qtng11HttpRequestE
N4qtng12HttpResponseE
N4qtng13HttpCookieJarE

CicadaBeetleClient / Private    → 业务客户端（124 个方法，见 A.6）
WxwjTable / WxwjAggregate / WxwjOneTypePromotion / WxwjTemplate
PageProduct / ProductWxwjModel / ProductWxwjFilterModel
ExportExcelWizard / ExportExcelExportPage / ExportExcelSelectPathPage
```

**为什么注意这三个 `qtng::` 类**：主程序只动态链接了 Qt 框架，没有 libcurl/libssl，
所以网络必然走 qtng —— 而 qtng 是静态链进主程序的，**符号全在**。
这意味着：

- 每个 HTTP 请求都会经过 `qtng::HttpSession::get/post` → **从这里 hook 能看到全部协议**
- 每个响应都会进 `qtng::HttpResponse::setBody` → **从这里 hook 能拿到全部原始数据**

## A.3 字符串分析的正确姿势

```bash
strings -a "$BIN" > /tmp/cicada.strings.txt     # 82,128 行
```

> ⚠️ **一定要先落盘再 grep。**
> 直接 `strings ... | grep` 会在管道里丢掉符号边界，而且大输出会污染分析上下文。
> 落盘后可以反复定向 grep，成本极低。

定向 grep 的几个套路：

```bash
# 1) 找 API 端点
grep -a -o -E "/api/v[0-9]+/[A-Za-z0-9_/]+" /tmp/cicada.strings.txt | sort -u
#   /api/v1/mac_update/
#   /api/v3/wxtwj_msgpack/{version/query,download}/
#   /api/v3/wxtwj_aggregate/{download,headers,version/query}/
#   /api/v3/{wxtwj,shoutao,incomes}/batch/upload/
#   /api/v3/order_assistant/check/

# 2) 找域名（注意过滤广告/文档类噪音）
grep -a -o -E "[a-z0-9.-]+\.(cn|com|net|io)" /tmp/cicada.strings.txt | sort | uniq -c | sort -rn
#   <客户实例>.ai-pi.com      ← 每客户一个实例子域，DNS 公开可查
#   localhost.ai-pi.com:8443
#   http://localhost-pdd.ai-pi.com:8443
#   xmdiepu.cn/extensions/download/
#   log.ardisk.cn/matomo/       ← 埋点
#   dk3rl619ch.feishu.cn/docx/...  ← 帮助文档放飞书

# 3) 找缓存路径 —— 这是发现本地数据的关键
grep -a -o -E "cache/[A-Za-z0-9_/%.]+|%1/cache" /tmp/cicada.strings.txt | sort -u
#   %1/cache ─────────────────┐
#   products/                 │  说明缓存根目录是「可执行文件路径 + /cache」
#   products/%1/deadlines/    │  而 products/%1/... 那几个是 API 路径不是目录
#   wxtwj                     ┘
```

**坑：`grep` 会撞进二进制里内嵌的巨型文本块**（XMP/Excel theme XML 有好几 KB 一行），
一匹配就刷屏。加 `-o` 加长度限制，或者 `grep -v` 掉 `xmpmeta|schemeClr|Photoshop`。

## A.4 `.pack` 格式是怎么逆出来的

### 第 1 步：文件类型判错

```bash
file xxx.pack
# apollo a88k COFF executable not stripped   ← 完全误导
```

`file` 认不出来。看头部字节：

```bash
xxd xxx.pack | head -3
# 00000000: 0194 82ae 7072 6f6d 6f74 696f 6e5f 7479  ....promotion_ty
# 00000010: 7065 01b0 7072 6f6d 6f74 696f 6e5f 6669  pe..promotion_fi
```

`82 ae` 后面跟 ASCII 的 `promotion_ty` —— **`0xae` = MessagePack fixstr 长度 14**。
这就是 MessagePack。

### 第 2 步：`ExtraData` 不是错误

```python
msgpack.unpackb(raw)      # → ExtraData: unpack(b) received extra data
```

新手会以为格式不对。实际上 **`ExtraData` = 这是个多对象拼接流**，不是单个对象。
换成迭代器：

```python
up = msgpack.Unpacker(raw=False, strict_map_key=False)
up.feed(raw)
objs = list(up)
```

### 第 3 步：读出结构

```
objs[0] = 1                    # int 版本号
objs[1] = [4 个 dict]          # 4 套 schema: {promotion_type, promotion_fields[]}
objs[2] = [N 行]               # 数据行
```

### 第 4 步：列数对不上 → 推断出「公式字段」

schema 有 **24 个字段**，但每行只有 **15 个值**。差 9 个。

看字段定义：每个字段是 `{name, type, formula, aggregate_formula}`。
数一下 **`formula` 非空的正好 9 个**（ROI / 直接ROI / 间接ROI / 点击率 / 点击转换率 /
CPM / 加购成本 / 直接加购成本 / 直接加购率）。

**结论：服务端只下发公式定义，不逐行存储派生列。**
下标 0–14 = 存储字段，15–23 = 公式字段。正好对上 15。

> 这条经验通用：**字段数 > 行宽时，差值通常就是服务端派生/公式字段**。
> 用 `formula` 是否为空来切分，能自动算出真实列宽。

### 第 5 步：4 套 schema 其实是同一套

```
promotion_type=1 → 无界|直通车*
promotion_type=2 → 无界|魔方*
promotion_type=3 → 无界|万相台*
promotion_type=4 → 无界|全站推广*
```

24 个字段的**位置和语义完全一致**，只是指标名换了前缀 → 可以做成一张宽表。

### 第 6 步：文件名尾号的真身

文件名是 `<128 位十六进制>_<10 位数字>.pack`。

**先做对照实验再下结论**：

```bash
ls | grep -o -E "_[0-9]+\.pack$" | sort -u | wc -l     # 10
ls | grep -o -E "_[0-9]+\.pack$" | sort | uniq -c      # 每个都出现 3 次（共 30 个文件）
md5 <同尾号的两个文件>
# a71080b1675ee1fa7d0a3cbef9f3f218
# a71080b1675ee1fa7d0a3cbef9f3f218   ← 内容完全相同
```

- 尾号 10 位数字 → 转成时间戳正好合理（如 `1790816610` = 2026-10-01）→ **是服务端版本时间戳**
- 128 位十六进制 = SHA-512 长度，**但内容相同的文件前缀不同** → **前缀不是文件内容哈希**
  （大概是 `hash(店铺+下载会话)` 之类）
- 同一版本被下了 3 次

> ★ **教训：不要去 dedup 文件名前缀。** 要么按内容哈希，要么按 `(店铺, 版本尾号)`。
> 本次建库时是按 `(店铺,日期,广告类型,淘宝ID)` 业务主键去重的，正好绕开了这个坑。

## A.5 证书与凭据提取

### 从二进制里抠 PEM

```bash
# 证书是明文存在二进制里的
awk '/BEGIN CERTIFICATE/,/END CERTIFICATE/' /tmp/cicada.strings.txt > /tmp/pems.txt
grep -c "BEGIN CERTIFICATE" /tmp/pems.txt      # 2 个
```

用 Python 精确切块后交给 openssl：

```bash
openssl x509 -in /tmp/pem_0.pem -noout -subject -issuer -dates -ext subjectAltName,basicConstraints
# subject = C=CN, ST=Fujian, L=Xiamen, O=GigaCores Ltd., OU=dev, CN=ai-pi.com
# issuer  = 自己（自签）
# notAfter = Jul 28 2030
# Basic Constraints: critical, CA:TRUE          ← 这是 CA
# 
# openssl x509 -in /tmp/pem_1.pem ...
# subject = CN=config.ai-pi.com
# issuer  = CN=GigaCores, O=GigaCores Root CA
# Extended Key Usage: critical, TLS Web Server Authentication   ← 这是服务器证书
```

**同时确认没有私钥**：

```bash
grep -a -c "BEGIN.*PRIVATE KEY" /tmp/cicada.strings.txt    # 0
```

→ 内嵌的是**用于 pin 的私有 CA**，不是 mTLS 客户端证书。

### 从 plist 里抠凭据

```bash
plutil -p ~/Library/Preferences/com.gigacores.cicada.plist
# login.username    => "<用户名·已脱敏>"
# login.epassword   => {length = 22, bytes = 0x0000000a789c73f58b...}
```

`78 9c` 是 zlib 头，前面 4 字节像是长度前缀。逐个偏移试：

```python
for skip in (0, 1, 2, 4):
    try: print(skip, zlib.decompress(v[skip:]))
    except Exception: pass
# 4 b'<明文密码·已脱敏>'    ← skip=4 成功
```

> 教训：**遇到压缩/加密的短 blob，先试 zlib/gzip 头（78 9c / 1f 8b），再试不同偏移。**
> 厂商经常只做「压缩」就当混淆了。

## A.6 客户端 API 面：`CicadaBeetleClient`（124 个方法）

已导出到 `cicada_client_api.txt`。**这是明天的藏宝图** —— 每个 `*Sync` 方法就是一次服务端调用。

跟你目标直接相关的（按重要性）：

```cpp
// ★ 产品 × 日期的核心
Product/DayIncome 是配对类型（见 A.7）
CicadaBeetleClient::loadProductSync(int, bool)
CicadaBeetleClient::loadProductPromotionsSync(QString const&, QDate const&, QDate const&)
                      // ← 直接吃日期区间！产品级推广数据
CicadaBeetleClient::updateExtendColumns(Product&, DayIncome&, QString const&)
                      // ← 明确告诉你 (Product, DayIncome) 是一对

// 收入 / 订单 / 分析
CicadaBeetleClient::incomeDays()
CicadaBeetleClient::incomeMinDate()          // ← 数据最早日期，能问出历史有多长
CicadaBeetleClient::retrieveIncomeInterpreter(Product const&)
CicadaBeetleClient::loadProductRealTimeOrdersSync(int)
CicadaBeetleClient::loadProductOrdersAnalysisSync(int)
CicadaBeetleClient::loadProductLinkSKUAnalysisSync(int)
CicadaBeetleClient::loadProductLinkSKUColorSizeAnalysisSync(int, QDate const&, QDate const&, ...)
CicadaBeetleClient::loadShopExpensesAggregateSync(QDate const&, QDate const&)

// 店铺/产品清单
CicadaBeetleClient::retrieveAllShopProductsCountSync()    // ← 「107464」就是它
CicadaBeetleClient::searchProductSync(QString const&)
CicadaBeetleClient::searchProductsBySKUCodeSync(QString const&, bool)
CicadaBeetleClient::groupShops()

// 推广（wxtwj）
CicadaBeetleClient::loadProductWxwjSync(QString const&, WxwjType const&, QString const&)
CicadaBeetleClient::getPromotionTypes()
CicadaBeetleClient::getPromotionTypeName(int)
CicadaBeetleClient::tryLoadLatestWxwjSync(bool)            // ← .pack 就是这个下的
CicadaBeetleClient::tryLoadLatestWxwjPromotionSync()
```

## A.7 ★ 产品数据的协议层：`hugeload_wire`

**本次最有价值的发现。** 符号里藏着这个命名空间：

```cpp
hugeload_wire::readSchema(qtng::MsgPackStream&, QList<hugeload_wire::Field>&)
hugeload_wire::readProduct(qtng::MsgPackStream&, QList<hugeload_wire::Field> const&,
                           QList<hugeload_wire::Field> const&, Product&)
hugeload_wire::decodeRow(QList<hugeload_wire::Field> const&, QByteArray const&, QDateTime*)

qtng::MsgPackStream& operator>>(qtng::MsgPackStream&, Product&)
qtng::MsgPackStream& operator>>(qtng::MsgPackStream&, DayIncome&)
```

推出的结论：

1. **产品数据不是走 UI 导出才有的，它本来就是 msgpack 从服务端下来的**
2. 结构是：先 `readSchema` 拿字段定义（`QList<Field>`），再 `readProduct` 逐行解出 `Product`
3. `decodeRow(字段定义, QByteArray, QDateTime*)` —— 行是**按 schema 逐字段解码的 `QByteArray`**，
   换句话说：**服务端只下发列定义 + 原始行数据，客户端自己解释**（跟 `.pack` 一样的思路）
4. **`DayIncome`（每日收入）是独立类型**，也有 `operator>>` →
   这就是「**每天的品维度的销售情况**」在协议层对应的东西

> **明天在 Intel 上，只要 hook `hugeload_wire::readProduct` 或
> `operator>>(MsgPackStream&, DayIncome&)`，就能直接把每日产品数据 dump 出来 —— 
> 不用点 UI、不用导出 xlsx。**

## A.8 动态插桩的三次尝试（以及怎么诊断失败）

### 尝试 1：Frida（直接不可用）

```bash
frida --version                          # 17.11.0
frida-ps -U | grep cicada                # OK，能看到进程
# 但 attach 挂住 → 换成硬超时 + 文件重定向重测：
#   frida 17.11.0 → NotSupportedError: unable to read from process memory
#   frida 16.7.19 → NotSupportedError: process is dead
```

定位：

```bash
ls ~/.cache/frida/frida-*/ 
file ~/.cache/frida/frida-*/frida-agent.dylib
# Mach-O universal binary with 2 architectures: [arm64] [arm64e]
lipo -info ...   # Architectures in the fat file: arm64 arm64e
```

**Frida 17 和 16 的 macOS agent 都只有 arm64/arm64e 切片，没有 x86_64**，
注入 Rosetta 进程当然读不了内存。

> ⚠️ 排查过程中的一个坑：`frida-helper` 子进程会继承 stdout，
> 所以 `frida ... | head` 这种管道会**一直挂着不退出**（head 等不到 EOF）。
> 调试 Frida 一律 `> /tmp/x.log 2>&1` + 外部 kill，不要用管道。

### 尝试 2：lldb（能 attach，但会把 App 搞崩）

```bash
lldb -b -p <pid> -o "image list -o -f" -o "detach" -o "quit"
# Process <pid> stopped
# Architecture set to: x86_64-apple-macosx-.     ← 能调
# exit=0                                          ← 干净
```

于是写了 lldb 脚本按 `qtng::HttpSession::get` / `HttpResponse::setBody` 下断点，
**21 个断点全部命中**，但 App 反复崩溃。查崩溃报告定位到原因：

```bash
python - <<'EOF'   # 解析 .ips（第一行 JSON header + 其余 JSON body）
...
EOF
# 00:25 / 00:28  EXC_CRASH SIGABRT
#   主线程栈停在 __CFRunLoopServiceMachPort（完全空闲）→ 被 detach 打断
#
# 00:36 / 00:40  EXC_BAD_ACCESS SIGSEGV
#   栈里有 DYLD-STUB$$_os_semaphore_create、DYLD-STUB$$QObject::connect
#   + Rosetta 的 runtime 帧
#   → dyld 惰性绑定 stub 还没解析就被执行了
```

**对照实验（关键一步）**：清空崩溃报告目录，然后**完全不插桩**跑 90 秒：

```
t=15s pid=97619 ... t=90s pid=97619      ← 全程存活
期间新崩溃报告: (空)
ESTABLISHED <服务器IP>:443 ×3       ← 正常运行
```

→ **崩溃 100% 是调试器 detach 造成的，App 本身没问题。**

### 尝试 3：AX API 驱动 UI（唯一成功的一条）

见正文 §3。核心是三次错误的排除：

| 尝试 | 现象 | 原因 |
|---|---|---|
| `System Events click at` | `-25204 类型错误` | 内部就是 AX 命中测试，**只在前台 App 里找**；App 不是前台就找不到元素 |
| Quartz `CGEventPost` | 静默丢弃，App 无反应 | 投递进程没有辅助功能权限 |
| `NSRunningApplication.activateWithOptions_` | 无效 | 本会话里不起作用；改用 `osascript ... set frontmost to true` |

**判断「点击到底有没有生效」的方法**：
不要看返回值（`AXPress` 经常返回 `-25204` 但实际按下了），
要看**副作用** —— 窗口有没有出现、文件有没有写出。

## A.9 判定启发式（积累下来的经验）

1. **判断有没有本地数据库：`lsof` 运行中的进程，不要看 strings。**
   `QSQLiteDriver` 这种字符串只要链了 QtSql 就会被编进去，哪怕一个 DB 都不开。

   ```bash
   lsof -nP -p $(pgrep -f "MacOS/cicada") | grep -E "\.db|sqlite"   # 空 → 没有
   ```

2. **Qt 应用的缓存经常锚在可执行文件旁边**（字符串里的 `%1/cache` → `Contents/MacOS/cache`），
   不在 `~/Library`。查 `/Applications/*.app/Contents/MacOS/` 下面。

3. **二进制里的时间戳字段**：`_1790816610` 这种 10 位数字先按 Unix 秒解一遍。

4. **`ExtraData` == 多对象流**，不是格式错误。

5. **字段数 > 行宽 ⇒ 差值通常是服务端派生字段**（用 `formula` 是否为空来切）。

6. **金额单位不要猜。** 存原值 + 在库里写 `_meta` 说明，比按猜测做除法安全得多。

7. **`screencapture -l <windowid>` 能绕开窗口层叠**（比全屏截图可靠），
   但**裁掉了标题栏**，图像坐标 ≠ 屏幕坐标。坐标优先用 AX 的 `AXPosition`/`AXSize`。

8. **UI 自动化里"写进去了"≠"生效了"** —— 必须用输出验证（见 §4.1）。

9. **同一个崩溃要查崩溃报告，不要靠猜。** `.ips` 里 `termination.byProc` 和触发线程栈
   能直接指出是 SIGABRT / SIGSEGV 以及死在哪个 dylib。

10. **Frida 调试不要用管道**（helper 子进程占用 stdout 导致挂起）。

## A.10 工具清单（本机实际用到/装过的）

| 工具 | 用途 | 备注 |
|---|---|---|
| `file` / `otool -L` / `codesign` | Mach-O 静态侦察 | 系统自带 |
| `nm -a` + `c++filt` | **符号抽取与 demangle（本次信息量最大）** | 系统自带 |
| `strings -a` | 字符串分析（先落盘） | 系统自带 |
| `openssl x509` | 解析内嵌证书 | 系统 LibreSSL |
| `plutil` / `plistlib` | 读 plist | 系统自带 |
| `lsof` | 看进程开了哪些文件 / 判断有无本地 DB | 系统自带 |
| `screencapture -l` | 抓单个窗口（绕层叠） | 系统自带 |
| `osascript` / System Events | 激活 App / 键盘事件 | 系统自带 |
| `lldb` | attach、下断点 | ⚠️ Rosetta 上 detach 会崩，见 §6 |
| `frida` 17.11 / 16.7.19 | 动态插桩 | ❌ 本机无 x86_64 agent |
| `pyobjc-framework-Quartz` | 窗口枚举、CGEventPost | venv 内 |
| `pyobjc-framework-ApplicationServices` | **AXUIElement（命中测试 / AXPress / 读写 AXValue）** | venv 内，本次主力 |
| `openpyxl` | 读写 xlsx | venv 内 |
| `msgpack` | 解 `.pack` | venv 内 |

重建 venv：

```bash
cd ~/diepu-export
python3 -m venv .venv
./.venv/bin/pip install msgpack openpyxl pyobjc-framework-Quartz pyobjc-framework-ApplicationServices
```

> 注意：本机 `pip install` 会被 PEP668 拦，**用 venv**。
