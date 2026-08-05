# Plan: Add user notification preferences

## 目标

让用户能配置"哪些事件触发推送"和"推送时段"。

## 范围

- 在 user settings 里加 `notification_prefs` JSON 字段
- 默认值：`{events: ["reply", "mention"], quiet_hours: [22, 8]}`
- 改 `SendPush()` helper，按 prefs 过滤

## 实现步骤

1. 改 user model 加字段 + migration
2. 改 settings API 暴露读写
3. 改 push 调度加过滤逻辑
4. 加单测覆盖关键场景

## 测试

- 单元测试 quiet_hours 边界（22:00 / 08:00 / 跨午夜）
- 集成测试 settings 改完立即生效
- 手动验证 3 种事件类型

## 不做

- 不做 web push（仅 app push）
- 不做 per-event 详细配置（粗粒度 events list 即可）
- 不动旧用户的默认值（保持沉默迁移）
