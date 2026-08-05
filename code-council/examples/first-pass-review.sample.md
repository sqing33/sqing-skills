# First-pass review (Mcode self-review)

## 总体

改动范围合理，三块各管一摊。代码风格跟项目其他部分一致。

## 发现

### ISS-1 [WARN] — `shouldSend` 里 events 循环没返回值
`internal/push/sender.go` 的 `for _, e := range prefs.Events` 循环里只 `break` 没 return，逻辑是看 `prefs.Events` 里有没有 `event`，但写法不直观。建议改成 `slices.Contains(prefs.Events, event)`。

### ISS-2 [WARN] — QuietHours 跨午夜逻辑反了
当前 `if prefs.QuietHours[0] < prefs.QuietHours[1]` 走"区间内"分支，但用户配置 `quiet_hours: [22, 8]` 是 [start, end] 顺序，start > end 表示跨午夜。逻辑没问题但读起来反直觉，需要明确注释。

### ISS-3 [NIT] — 缺迁移脚本
只改了 model 字段没写 migration SQL，运维侧不知道 default value 怎么填。

## 未尽事项

- 没看到 `UpdateNotifPrefs` 的输入校验（events 列表里的字符串是否白名单）
- 没看到 `getUserNotifPrefs` 的实现，是不是会 N+1
