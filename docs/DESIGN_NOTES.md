# Design Notes — TraceWire

> 设计思路：从 open-source-ideas/ideas#352 的构想出发，到可运行的实现。
> 本文记录"为什么这么做"，与 `DEVELOPMENT_LOG.md`（做了什么）互补。

## 1. 原始构想 → 实现映射

| 构想原文（#352） | TraceWire 中的落地 |
| --- | --- |
| "在控制点保存软件状态（每个需要追踪的变量）" | `tracewire/tracer.py`：`sys.settrace` 的 `line` 事件即控制点，每次快照**变化**的局部变量 |
| "需要编译器/运行时辅助；只插桩需要的模块和函数" | Python 无需编译器插桩——运行时 `settrace` 钩子即可；`--include/--exclude` 按模块名 glob 过滤（默认只追踪被运行脚本，排除 tracewire 自身） |
| "trace 是 delta 压缩后保存的" | `tracewire/core.py`：自定义二进制格式；行号按函数内差值（zigzag varint）、变量只记录相对上一控制点的变化值、整体 zlib 压缩 |
| "IDE 把执行画成一条带标记点的 wire" | `tracewire/html_viewer.py`：单文件 HTML/SVG 查看器，SVG 折线为 wire，每个控制点一个 marker |
| "点标记点，像调试一样检查状态" | 点击 marker → 检查器显示该点完整局部变量快照 + 类型 + 变化标记 + 异常标记 |
| "标记点集合：手动或谓词；visible / important" | `tracewire/markers.py`：`MarkerSet`；谓词为 Python 表达式或函数；`visible=False` 隐藏该集合标记并简化 wire；`important=True` 提供 ←/→ 前后跳转按钮 |
| "谓词 API：源文件、行、列、函数名、变量→类型、变化变量" | `Marker` 属性：`source/line/function/variables/types/changed`（`col` 留作 best-effort=0，见 §3） |
| "集合可 memoize 到磁盘文件" | `<trace>.sets.json`（`SetStore`，JSON 持久化谓词源码与开关） |
| "共享库体系（格式/绑定/GUI/编辑器插件）" | 采用单 Python 包内模块划分（`core`/`tracer`/`markers`/`html_viewer`），等价于共享库的进程内版 |

## 2. 关键技术决策

### 2.1 为什么用 `sys.settrace` 而不是 AST/字节码插桩或 `sys.monitoring`
- 零依赖、3.8+ 全兼容；AST 插桩会改变行号与 traceback 语义，字节码插桩复杂度高；
- `sys.monitoring`（PEP 669，3.12+）开销更低，但降低兼容面；settrace 对本项目规模足够，
  `Tracer._hook` 结构上可替换为 monitoring 回调。

### 2.2 为什么状态只记"变化值"而查看器能还原"全量"
- 序列化只写 `changed` 子集（体积小）；`TraceModel` 回放时把 delta 折叠进**按函数维护的帧栈**，
  得到每个控制点的全量快照。这是"delta 压缩 + 事后全量检查"的标准组合。
- **关键前提：值必须在"记录那一刻"定格。** 0.1.0 把值编码推迟到 `write()`（程序跑完之后），
  事件里存的是活对象引用，于是每个控制点显示的都是变量的**最终值**，原地 `append` 完全不可见。
  0.2.0 改为记录时调用 `core.freeze_value()`：标量原样保留，其余立刻取 `repr()`（超长截断到
  `MAX_REPR`）。这一步同时消除了"快照别名活对象"和"原地修改不可见"两个问题。

### 2.3 递归安全（本项目最重要的正确性修复）
- 初版按 `func_id` 单键存帧状态：`factorial(3)` 的 CALL 会覆盖外层 `factorial(4)` 的帧，
  内层 RETURN 又把它 pop 掉，外层标记因此丢失变量。
- 修复：**所有帧状态与行号 delta 一律以 `(func_id, depth)` 为键**，CALL 增深、RETURN 减深，
  编解码两端严格对称；递归各层互不污染（`TraceModel` 用每函数帧栈）。

### 2.4 CALL 事件携带入口状态
- 仅靠行事件 delta 无法还原参数：`factorial` 帧内 `n` 从不变化，delta 恒为空。
- 修复：CALL 事件额外携带 `frame.f_locals`（即参数快照），作为该帧的种子状态。

### 2.5 值编码
- 7 个类型标签（None/bool/int/float/str/bytes/repr）；任意对象回退 `repr()` 字符串，
  保证格式零依赖、可确定性回放；大整数走任意精度 varint（zigzag 需正确处理正负号，
  修复过 64 位截断与错误 zigzag 实现两个 bug，见日志）。
- **格式 v2**：变量名改为驻留到 string table（写入端写 id，读端查表），不再每个事件原样内联。
  这要求 `write()` 先构建 payload（构建过程中会 intern 新名字）再写 header；读取端按 id 解析。
  `VERSION = 2`，读到 v1 文件时抛出明确错误而不是误解析。
- 读端现在校验尾部标记 `TWTREND`、payload 长度和每一次越界读取：截断文件会得到
  `TraceFormatError: truncated trace: end marker missing`，而不是静默接受或裸 `zlib.error`。

### 2.6 谓词执行模型
- 表达式形式编译为 `lambda marker: (expr)`；函数形式 `def 任意名(marker): ...` 取片段中唯一的可调用对象
  （多个时要求命名为 `pred`）；
- 命名空间注入 `Marker` 类，不做沙箱（调试工具执行用户自己的谓词是预期行为）。

### 2.7 查看器为什么是单文件 HTML 而非 IDE 插件
- 构想中的 IDE 插件（Kate/Qt Creator/N++/VS）工程量巨大且各有私有 API；
- 单文件 HTML（内嵌数据 + 零外链）可在任何浏览器打开，跨编辑器、跨平台，
  是"IDE 里画 wire"这一交互的最小可验证落地；CLI `dump` 提供终端版。

## 3. 已知边界与后续方向（Roadmap）

- `col`（列）未精确解析：settrace 不提供列信息，需结合 tokenize/行级 AST 才能给出，当前为 0；
- 表达式路径（"currently executed expression path"）：settrace 无法直接暴露，需要 AST 级别的
  per-expression 插桩，属后续工作；
- 超大 trace：当前全量内存构建（记录在内存、读回在内存），超长程序可加流式/分页；
- 容器内容以 `repr()` 定格：`marker.get('items')` 得到的是字符串 `'[1, 2]'` 而非列表。
  这是格式零依赖的代价；谓词里请按字符串比较，或用 `marker.changed` 判断是否变化。
- `repr()` 单值上限 `MAX_REPR = 4000` 字符，超出部分截断并标注原始长度——极端大的结构体
  不会撑爆 trace，但也无法在查看器里完整还原；
- 其他语言：`core.py` 格式与语言无关，可为 JS/Go 等写采集端（对应构想的"编译器插件"）；
- 原生编辑器插件（VS Code/LSP）可作为查看器外壳。

## 4. 目录结构

```
tracewire/
├── tracewire/
│   ├── __init__.py     # 包说明与版本
│   ├── core.py         # trace 文件格式 + delta 压缩编解码（语言无关）
│   ├── tracer.py       # sys.settrace 插桩采集器
│   ├── markers.py      # Marker / TraceModel / MarkerSet / SetStore
│   ├── html_viewer.py  # 单文件交互式 HTML 查看器生成器
│   └── cli.py          # record / dump / sets / html 命令
├── examples/           # demo_program.py + predicates.py 示例
├── tests/              # 核心编解码测试 + 端到端测试
├── pyproject.toml      # 打包与 tracewire 命令入口
└── README.md
```
