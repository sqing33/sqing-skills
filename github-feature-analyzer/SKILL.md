---
name: github-feature-analyzer
description: 分析一个或多个功能在 GitHub 仓库中的实现方式，输出原则优先、证据充分的结论。当用户要求阅读 github.com 仓库、解释实现逻辑、追踪运行行为、比较多个功能实现，或按功能审计陌生代码库时使用。优先采用多 agent 并行分析加父 agent 合并；必要时回退到单 agent。
---

# GitHub 功能实现分析器

这个 skill 用于分析公开 GitHub 仓库中某个功能是如何实现的。它会建立可复现的本地工作区，先理解项目整体，再围绕用户指定的功能输出带证据的机制报告。

## 默认分析方式

默认采用 README 优先：

- 先读仓库 README。
- 提取 3 个项目特征或标志性能力。
- 解释这些特征背后的实现机制，并附少量证据。
- 再进入用户指定功能的专项分析。

默认采用机制优先，而不是文件列表优先：

- 运行时控制流
- 数据流
- 状态与生命周期
- 失败与恢复
- 并发与时序

函数/文件映射只是证据，不是报告中心。

## 按需读取的参考文件

- `references/analysis-method.md`：分析流程和启发式方法。
- `references/report-schema.md`：最终报告结构约束。
- `references/failure-handling.md`：错误分类和停止条件。
- `references/agent-output-schema.md`：子 agent 输出 JSON 约束。

## 必需输入

执行前收集：

- `repo_url`：格式必须是 `https://github.com/{owner}/{repo}`。
- `features`：一个或多个自然语言功能描述。
- `ref`：可选，默认 `main`。
- `depth`：可选，`standard` 默认，或 `deep`。
- `language`：可选，默认 `zh`。
- `agent_mode`：可选，默认 `multi`，也可用 `single` 或 `auto`。
- `max_parallel_agents`：可选，默认 `auto`。
- `reference_lookup`：可选，是否复用历史分析结果。

当前版本拒绝分析私有仓库。

## 路径约定

固定使用项目内路径：

- 存储根目录：`<project-root>/.github-feature-analyzer/`
- 单仓库工作区：`<storage-root>/{owner-repo}/`
- 源码目录：`<workspace>/source/`，每次运行覆盖。
- 产物目录：`<workspace>/artifacts/`，每次运行覆盖。
- 子 agent 结果：`<workspace>/artifacts/subagents/`
- 合并后的子 agent JSON：`<workspace>/artifacts/subagent_results.json`
- 报告文件：`<workspace>/report.md`，追加运行记录，不使用时间戳文件名。
- 历史检索索引：`<storage-root>/.knowledge-index/`
- 检索运行环境：`<skill-root>/.venv-reference/`

不要自动清理下载的仓库。只有用户明确要求时才清理。

## 工作流

### 0. 自动准备依赖

当需要历史分析检索、向量索引或任何非标准库依赖时，优先使用纯 Python 自举脚本。它会在 skill 目录下创建 `.venv-reference`，安装 `numpy`、`sentence-transformers` 等依赖，并在依赖未变化时自动跳过重复安装：

```bash
python3 scripts/bootstrap_deps.py
```

如果需要强制重建环境：

```bash
python3 scripts/bootstrap_deps.py --force
```

`bootstrap_deps.py` 会在 Python 3.12+ 优先使用 `requirements-vector.lock.txt`；更老的 Python 会使用 `requirements-vector.txt` 让 `pip` 自行解析兼容版本。

### 1. 可选：历史分析检索

仅当 `reference_lookup.enabled=true` 时运行。默认使用会自动准备依赖的本地 Python wrapper。

构建或刷新本地向量索引：

```bash
python3 scripts/reference_retrieval_local.py build \
  --storage-root "${STORAGE_ROOT:-<project-root>/.github-feature-analyzer}" \
  --model "${REFERENCE_MODEL:-sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2}"
```

查询历史报告：

```bash
python3 scripts/reference_retrieval_local.py query \
  --storage-root "${STORAGE_ROOT:-<project-root>/.github-feature-analyzer}" \
  --query "$REFERENCE_QUERY" \
  --mode "${REFERENCE_MODE:-semi}" \
  --format markdown \
  --refresh auto
```

保留 `scripts/reference_retrieval_uv.sh` 作为 UV 专用路径，但普通 Python 环境不需要先安装 UV。

### 2. 准备工作区

```bash
python3 scripts/prepare_workspace.py \
  --repo-url "$REPO_URL" \
  --ref "${REF:-main}"
```

使用脚本返回的 JSON 路径继续后续命令。

### 3. 获取源码

先用 MCP 验证仓库可访问性并探测候选实现文件，再拉取本地源码：

```bash
FETCH_JSON=$(python3 scripts/fetch_repo.py \
  --repo-url "$REPO_URL" \
  --ref "$REF" \
  --source-dir "$SOURCE_DIR" \
  --mode mcp-first \
  --public-only true)
```

脚本会先尝试 archive/API 获取，失败后自动回退到 `git clone`。两种方式都失败时立即停止并报告原因。

### 4. 建立代码索引

```bash
python3 scripts/build_code_index.py \
  --source-dir "$SOURCE_DIR" \
  --output "$ARTIFACTS_DIR/code_index.json"
```

### 5. README 优先 + 功能分析

- 必须先读 README；如果 README 不存在或信息太弱，用索引/代码推断，并标记为 `inference`。
- 必须提取 3 个项目特征，并说明实现机制。
- 然后按用户给出的每个 feature 做机制分析。

### 6. 多 agent 模式

当 `agent_mode=multi`，或 `agent_mode=auto` 且环境可用时，使用父 agent + 分层子 agent：

- 1 个 `overview` 子 agent：仓库概览、入口、边界。
- 1 个 `architecture` 子 agent：运行结构、跨模块依赖、生命周期。
- N 个 `feature` 子 agent：每个功能一个。

并行上限默认是 `min(len(features) + 2, 8)`；如果用户指定 `max_parallel_agents`，使用 `min(requested, features + 2)`。

每个子 agent 必须按 `references/agent-output-schema.md` 输出 JSON，并保存到：

```text
$ARTIFACTS_DIR/subagents/*.json
```

合并子 agent 输出：

```bash
python3 scripts/merge_agent_results.py \
  --input "$ARTIFACTS_DIR/subagents" \
  --output "$ARTIFACTS_DIR/subagent_results.json"
```

渲染最终报告：

```bash
python3 scripts/render_report.py \
  --repo-url "$REPO_URL" \
  --ref "$REF" \
  --resolved-ref "$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("resolved_ref") or "")' <<< "$FETCH_JSON")" \
  --commit-sha "$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("commit_sha") or "")' <<< "$FETCH_JSON")" \
  --source-dir "$SOURCE_DIR" \
  --index-json "$ARTIFACTS_DIR/code_index.json" \
  --subagent-results "$ARTIFACTS_DIR/subagent_results.json" \
  --output "$REPORT_PATH" \
  --depth "${DEPTH:-standard}" \
  --language "${LANGUAGE:-zh}" \
  --feature "feature A" \
  --feature "feature B"
```

### 7. 单 agent 模式

当 `agent_mode=single` 时，跳过子 agent 合并，直接运行 `render_report.py`，不传 `--subagent-results`。

如果用户要求多 agent，但当前环境不可用，先询问用户：

- 继续使用单 agent。
- 停止并等待多 agent 能力。
- 调整环境后重试。

## 深度策略

默认使用 `standard`。用户明确说“超深度”“深度审计”“全面审计”“deep dive”时，切换为 `deep`。

`deep` 模式要增加：

- 更详细的边界检查。
- 性能/并发风险信号。
- 失败分支信号。
- 相关模块测试覆盖信号。

不要生成泛泛的深度审计套话。

## 输出要求

最终报告遵循 `references/report-schema.md`。顶层分为三部分：

1. 项目参数与结构解析
2. 面向人的功能说明
3. 面向 AI 的实现细节与证据链

每个关键结论都必须：

- 至少给出一个 `file:line` 证据。
- 没有直接证据时标记为 `inference`。
- 保留 `confidence` / `inference` 机制。
- 优先解释机制，而不是堆路径。

当功能涉及 agent、子 agent、SDK、CLI 或运行时调用路径时，额外说明：

- 调用路径类型：`SDK`、`CLI`、`Hybrid` 或 `inference`。
- 工作目录解析依据，例如 `working_dir`、`current_dir`、`cwd` 或容器映射。
- 没有直接证据时明确标记为 `inference`。

## 返回给用户

完成后返回：

- resolved ref 和 commit。
- README 优先得到的 3 个项目特征及实现机制摘要。
- 每个功能的机制优先说明和行级证据。
- 边界、风险和未知点。
- 最终报告路径。

## 失败策略

遵循 `references/failure-handling.md`。遇到以下情况硬停止：

- `repo_url` 无效或不是 `github.com`。
- 私有仓库。
- archive/API 和 git clone 都失败。
- `features` 为空。
- 历史检索开启时，向量依赖、模型加载、索引损坏或 UV 环境失败。
