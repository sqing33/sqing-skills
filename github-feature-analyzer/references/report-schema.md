# 报告结构

最终报告必须使用下面三段顶层结构。该结构优先服务人类可读性，同时保留 AI 需要的机制追踪和证据链。

## 1. 项目参数与结构解析

必须包含：

- 仓库 URL
- 请求的 ref
- 实际解析到的 ref
- 实际 commit，若可用
- 生成时间
- 分析深度
- 分析模式：`multi-agent` 或 `single-agent`
- 源码目录
- 索引文件路径
- 子 agent 结果路径，若使用

还要包含仓库结构概览：

- 总文件数
- 已索引文本文件数
- 主要语言分布
- 可能的运行入口
- 主要模块边界
- README 优先说明或 fallback 说明
- 正好 3 个项目特征，每个包含机制解释、置信度和证据引用

## 2. 面向人的功能说明

按输入顺序为每个功能建立一个小节。

每个功能必须包含：

- 功能作用：优先用“用途 + 价值”表达。
- 特殊能力：优先写失败恢复、并发时序等高信号能力。
- 实现想法：结合控制流、数据流、状态生命周期解释为什么这样做。
- `confidence`：`high`、`medium` 或 `low`。
- 2-5 个关键证据引用，格式为 `path:line`。

## 3. 面向 AI 的实现细节与证据链

按输入顺序为每个功能建立一个小节。

每个功能必须包含：

- `Runtime Control Flow`
- `Data Flow`
- `State and Lifecycle`
- `Failure and Recovery`
- `Concurrency and Timing`
- `Key Evidence`
- `Inference and Unknowns`

### Runtime Control Flow

描述 trigger -> dispatch -> execution -> completion。无法确认的连接标记为 `inference`。

### Data Flow

描述关键输入/输出和转换边界，不要写泛泛架构话。

### State and Lifecycle

描述关键状态变化、归属和生命周期事件。

### Failure and Recovery

描述明确的失败处理路径和恢复行为。没有证据时写明这是缺口。

### Concurrency and Timing

描述可观察的并行、排序、队列行为和时序敏感点。不明显时明确低置信度。

### Key Evidence

每条证据包含：

- `path:line`
- 短 snippet

### Inference and Unknowns

列出不确定点和缺失验证。

## 条件块：调用路径分类

只有当功能涉及 SDK、CLI、子 agent 或运行时调用路由时加入。

内容包括：

- 调用路径类型：`SDK`、`CLI`、`Hybrid` 或 `inference`
- 工作目录解析说明：`working_dir`、`current_dir`、`cwd`、容器映射等
- 没有直接证据时明确标记 `inference`

## 全局尾部块

Part 3 末尾加入：

- `Cross-feature Coupling and System Risks`
- 跨功能共享模块耦合风险，若存在
- 全局盲点，例如生成代码、二进制、扫描截断
- 证据置信度较弱的章节

## Deep 模式补充

`depth=deep` 时，每个功能下增加 `Deep Audit`，包含：

- 详细边界检查表
- 性能/并发风险提示
- 失败分支信号
- 涉及模块的测试覆盖信号

不要输出泛泛的深度审计套话。

## 硬规则

- 关键结论必须保持 `path:line` 可追踪。
- 保留 `confidence` 和 `inference` 机制。
- 没证据的说法标记为 `inference`。
- 优先解释机制，不要堆文件/函数清单。
- 保持按运行追加报告的行为。
