# 子 Agent 输出结构

每个子 agent 必须输出 JSON。不要输出 Markdown 包裹，不要添加额外解释。

## 顶层结构

```json
{
  "agent_role": "overview | architecture | feature",
  "feature": "功能名称，overview/architecture 可为空",
  "direct_answer": "一句话结论",
  "mechanisms": {
    "runtime_control_flow": "...",
    "data_flow": "...",
    "state_and_lifecycle": "...",
    "failure_and_recovery": "...",
    "concurrency_and_timing": "..."
  },
  "evidence": [
    {
      "path": "relative/file/path.ext",
      "line": 123,
      "snippet": "short code or text snippet",
      "supports": "该证据支持什么结论"
    }
  ],
  "confidence": "high | medium | low",
  "inferences": [
    {
      "claim": "推断内容",
      "basis": "推断依据",
      "missing_evidence": "还缺什么证据"
    }
  ],
  "risks": ["..."],
  "unknowns": ["..."]
}
```

## 要求

- 所有证据必须使用相对路径。
- 行号必须是数字；没有行号时不要伪造，改写入 `unknowns`。
- `mechanisms` 五个字段都要出现；没有证据时写明缺口。
- `confidence` 必须是 `high`、`medium` 或 `low`。
- 不要在 JSON 外输出额外文本。
