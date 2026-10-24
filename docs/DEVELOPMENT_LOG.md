# Development Log — TraceWire

> 开发日志：记录从 0 到交付的每一步（决策、实现、bug、验证）。
> 独立原创实现 open-source-ideas/ideas#352 的开源构想，未复制任何现成项目代码。

## 2026-10-21（本次会话）

### 阶段 0 — 需求与选型
- 选定 #352 构想：便捷追踪器 + 事后调试器（时间旅行调试方向）。
- 技术选型：Python 3.8+，零第三方依赖（仅标准库）。
  - 插桩：`sys.settrace`（兼容面最广，实现清晰；`sys.monitoring` 留作优化项）。
  - 格式：自定义二进制（魔数+版本+字符串表+delta 事件流+zlib）。
  - 查看器：CLI 文本时间线 + 单文件 HTML/SVG 交互页面（无外链）。
- 项目命名 **TraceWire**（执行轨迹即一条 wire），包名 `tracewire`。

### 阶段 1 — 核心库（core.py）
- 实现：StringTable（字符串驻留）、任意精度 varint、zigzag 有符号 varint、
  7 标签值编码（None/bool/int/float/str/bytes/repr 回退）、
  LineEvent/CallEvent/ReturnEvent、TraceWriter/TraceReader。
- **Bug #1（64 位截断）**：`_varint` 对 `10**20` 等大整数做 `& 0xFFFFFFFFFFFFFFFF` 截断，
  值损坏。修复：去掉掩码，改为纯任意精度循环。
- **Bug #2（zigzag 公式错）**：`(n << 1) ^ (n >> 1)` 对正数产生错误编码（99→247→-124），
  行号 delta 与整数值同时损坏。修复：Google 风格 zigzag `(n<<1) if n>=0 else ((-n)<<1)-1`，
  解码端 `(u>>1) ^ -(u&1)` 本就正确。
- **Bug #3（编解码基线不一致）**：编码端首事件基线取 `ev.line`，解码端取 0，首行号 +1 错位。
  修复：两端统一基线 0（首事件编码绝对行号）。
- 测试：`tests/test_core.py` 全绿（round-trip、值编解码、200 事件 delta 体积 <8KB）。

### 阶段 2 — 追踪器（tracer.py）
- 实现：`Tracer`（include/exclude glob 过滤、每帧快照基线、line/call/return/exception 钩子）
  与 `record()`（编译执行脚本、捕获 SystemExit、恢复 sys.path）。
- **Bug #4（快照基线被污染）**：`_snapshot` 把基线更新为"变化子集"，导致无变化变量（如 `n`）
  在后续控制点丢失。修复：基线始终保存完整 `f_locals`，只返回变化子集给写入端。

### 阶段 3 — 标记与集合（markers.py）
- 实现：`Marker`（source/line/function/variables/changed/types/evtype）、
  `TraceModel`（回放折叠 delta 重建全量状态）、`MarkerSet`（谓词表达式/函数两种形式）、
  `SetStore`（JSON 持久化 memoize）。
- **Bug #5（谓词表达式未绑定 marker）**：exec 求值 `marker.function == ...` 时 NameError。
  修复：编译为 `lambda marker: (expr)` 求值。
- **Bug #6（递归帧串扰）**：按 `func_id` 单键存帧，内层 CALL 覆盖外层、RETURN pop 外层，
  外层标记丢失 `n`。修复：编解码与模型全部改用 `(func_id, depth)` 键 + 每函数帧栈
  （`TraceWriter._payload`、`TraceReader._parse_events`、`TraceModel._build` 三处对称改造）。
- **决策**：CALL 事件携带 `frame.f_locals` 作为入口状态，否则参数（从不变化的 `n`）永远缺失。

### 阶段 4 — 查看器（cli.py + html_viewer.py）
- CLI：`record / dump(--index) / sets(--name --predicate --hidden --important --list) / html`。
- HTML 查看器：SVG wire（可见标记连线、隐藏集合标记简化为直段+小灰点）、点击检查器、
  集合面板（visible 勾选实时重建、important 前后跳转）、源码面板按行高亮。
- **Bug #7（BrokenPipe）**：`dump | head` 时管道关闭抛异常。修复：main 捕获 BrokenPipeError。
- 验证：HTML 内嵌 JSON 用 Python `json.loads` 校验通过；JS 语法经 node 校验通过；
  用 DOM 桩在 node 中实际运行 `renderSource/renderSets/rebuild/select` + 可见性切换，
  冒烟通过（81 markers、2 sets、选中 marker #40 检查器正确渲染）。

### 阶段 5 — 端到端验证
- `python3 -m tracewire.cli record examples/demo_program.py -o demo.trace` → 128 事件，2.7KB；
- `sets` 建 recursion（52/81）与 big_total（4/81，important）→ 写入 `.sets.json`；
- `dump` 时间线正确（函数:行 + 变化变量数 + 集合标记）；
- `dump --index 40` 检查器输出全量状态 `{limit=4, total=9*, i=3, step=1}`；
- `tests/test_core.py` + `tests/test_e2e.py` 全部通过（7 个测试函数）。

### 阶段 6 — 交付
- 文档：README.md、docs/DESIGN_NOTES.md（思路）、本日志。
- git：`git init` + 初始提交；LICENSE（MIT）、.gitignore、pyproject.toml（`tracewire` 命令入口）。
- 打包：`tracewire_<日期>.zip`（含项目、本日志、设计思路）。

## 测试覆盖清单（交付时状态）
| 项 | 结果 |
| --- | --- |
| 格式 round-trip（call/line/return） | ✅ |
| 值编码全类型（含大整数、repr 回退） | ✅ |
| delta 压缩体积（200 事件 <8KB） | ✅ |
| 递归帧状态还原（n/sub 各层正确） | ✅ |
| 谓词（表达式/函数/重要集） | ✅ |
| 集合 memoize 持久化 | ✅ |
| HTML 查看器生成 + 数据完整性 + JS 运行时冒烟 | ✅ |
| CLI 全命令端到端 | ✅ |

## 2026-10-25 — 0.2.0：正确性审计与格式 v2

上一版能跑通自己的测试，但测试只覆盖了"正常路径"。这一轮做的是**反着用**：
故意喂可变对象、喂 bytes、喂崩溃脚本、喂截断文件、喂错误谓词，看它会不会骗人。

### 修复的缺陷（每条都有回归测试，见 `tests/test_regressions.py`）

| # | 缺陷 | 影响 | 修复 |
| --- | --- | --- | --- |
| R1 | 快照在 `writer.write()` 时才编码，事件里存的是**活对象引用** | 同一个列表在 5 个控制点全部显示最终值 `[1,2,3,4]`；原地 `append` 完全不可见 | 值在**记录时**冻结（`core.freeze_value`），写入层与采集层都调用；repr 上限 `MAX_REPR=4000` |
| R2 | `traceback.print_exc()` 在 `tracer.stop()` **之前**执行 | 4 行崩溃脚本录出 **32,562 个事件 / 149 个函数**（TextWrapper、Tokenizer、linecache…），用户自己的两行被淹没 | 先 `stop()` 再打印；事件数降到 **9** |
| R3 | 读端从不校验 `TWTREND` 尾部标记 | 截断 1 字节的文件被**静默接受**；截断一半才抛裸 `zlib.error` | 校验尾部 + payload 长度 + 每个越界读取，统一抛 `TraceFormatError` |
| R4 | `record()` 吞掉所有异常 | 被追踪程序失败时 CLI 仍返回 0，CI 无法据此判断 | `writer.exit_code` / `writer.error` + `raise_on_error`；CLI 透传退出码 |
| R5 | 变量名在每个事件里**原样内联**（README 却声称已驻留） | 长变量名 × 上万个事件，白白多占体积 | 格式 v2：变量名驻留为 string-table id；`VERSION=2`，v1 文件明确报错 |
| R6 | `StringTable.intern` 用 `list.index()` | O(n) 查找，且每个事件都要调；8000 个名字 230ms，热路径 10.2µs/次 | 加 `dict` 索引，O(1) |
| R7 | `html` 渲染遇到 `bytes` 变量 | 任何碰过字节串的程序，查看器直接 `TypeError: not JSON serializable` | `_json_safe()` 归一化 |
| R8 | 谓词函数名写死 `pred` | `def my_rule(...)` 报裸 `KeyError: 'pred'` | 取片段里唯一的可调用对象；多个则要求命名为 `pred` |
| R9 | 谓词抛异常被静默吞掉 | `marker.total`（文档里就这么写的）匹配 0 条，看起来像"合法的空集" | 计数 `MarkerSet.skipped`，CLI 明确告警 |
| R10 | `.sets.json` 损坏直接崩 | 之后所有 `dump`/`sets`/`html` 全部失败 | 降级为空 + 告警；写入改 tmp+replace |
| R11 | `dump --index` 越界 | 裸 `IndexError` traceback | 范围检查，退出码 2 |
| R12 | 无 `__main__.py` | `python -m tracewire` 报 "No module named tracewire.__main__" | 补 `__main__.py` |

### 自己改动引入的一个坑（测试抓到了）
把 `--exclude tracewire` 改成"匹配路径中任意目录名"后，Windows 上 `fnmatch` **不区分大小写**，
于是仓库目录 `TraceWire/` 也被当成 tracewire 排除掉——整个程序录出空 trace。
修复：改用 `fnmatchcase`，并且**不再依赖 glob 排除自己**，而是按包的真实路径 `_PKG_DIR` 精确排除。

### 新增
- `tracewire summary <trace> [--json]`：一屏报告（函数/标记点/行范围/异常数/集合命中），供 CI 与 PR 评论使用。
- `python -m tracewire` 可用。
- 测试：4（core）+ 5（e2e）+ 22（regressions）= **31 条**。
