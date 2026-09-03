# 训练日志微站点

这是 `training-calendar.html` 的 Runtime Data 迁移版本，部署 slug 默认为
`training-log`。

## 数据迁移

- 原 HTML 的 `embeddedPlan` 已完整迁移到 `data/training-plan.json`，共 40 天。
- `microsite.json` 将该文件声明为 `training-plan` 的首次 seed。
- seed 仅在服务器尚不存在该 Runtime Document 时执行；重新部署不会覆盖线上保存的数据。
- 浏览器通过 `/_microsite/sdk/v1.js` 读取和保存，写入仍需站点 owner 的登录会话。

## 分享行为

“分享本月”生成以下形式的公网链接：

```text
/sites/training-log/?month=2026-08&view=share
```

只读链接固定显示生成时所查看的月份，隐藏编辑和月份导航；即使绕过前端，Runtime Data
写入接口仍会校验 owner 登录会话。该链接是实时视图，owner 后续更新会反映到分享页。

## 部署

```bash
python3 ~/.codex/skills/microsite-container/scripts/deploy.py manifest \
  --dir examples/training-log --entrypoint index.html

python3 ~/.codex/skills/microsite-container/scripts/deploy.py deploy \
  --slug training-log \
  --title "训练日志" \
  --source-dir examples/training-log \
  --publish-dir examples/training-log \
  --entrypoint index.html
```
