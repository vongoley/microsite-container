# 训练日志微站点

这是 `training-calendar.html` 的 Runtime Data 迁移版本，部署 slug 默认为
`training-log`。

## 数据迁移

- 仓库只包含空的 `training-plan.seed.json` 初始化模板，不包含个人训练记录。
- `microsite.json` 将空模板声明为 `training-plan` 的首次 seed。
- Runtime Data Schema v5 支持每个部位记录多条训练项目、部位专属候选与自由填写，并以可选 `locked` 字段持久化日期锁定状态；同时兼容此前版本的数据。
- seed 仅在服务器尚不存在该 Runtime Document 时执行；重新部署不会覆盖线上保存的数据。
- 浏览器通过 `/_microsite/sdk/v1.js` 读取和保存，写入仍需站点 owner 的登录会话。

## 分享行为

“分享本月”生成以下形式的公网链接：

```text
/share/<服务端生成的凭证>/?month=2026-08&view=share
```

分享链接由服务端授权，限定对应月份，隐藏编辑和月份导航；即使绕过前端，Runtime Data
写入接口仍会校验 owner 登录会话。该链接是实时视图，owner 后续更新会反映到分享页。

## 部署

```bash
python3 ~/.codex/skills/microsite-container/scripts/deploy.py manifest \
  --dir . --entrypoint index.html

python3 ~/.codex/skills/microsite-container/scripts/deploy.py deploy \
  --slug training-log \
  --title "训练日志" \
  --source-dir . \
  --publish-dir . \
  --entrypoint index.html
```

## 本次本地迭代

源码恢复自线上部署 `dep_81bde0be711f473d82f24fc45f1f5293`。
此目录为当前站点源码，`examples/training-log` 是较早的迁移示例。

- 重量支持两位小数；组数、次数保持整数。
- 拖动同一训练部位内的序号排序，也支持聚焦序号后按上下方向键。
- 每项训练支持 `note` 备注，移动端通过备注图标编辑。
- Schema 版本升级至 6，保留旧记录兼容性；上线时需一并部署 schema。
- 使用 `python3 -m http.server 8001 --bind 127.0.0.1 --directory sites/training-log`
  可启动本地验收。本地模式仅将编辑保存到浏览器，不写入线上数据。

部署当前版本请将上述命令中的 `examples/training-log` 替换为 `sites/training-log`。空 seed 仅初始化不存在的文档，不覆盖服务器已有训练数据。

## 2026-09-08 编辑修复

- 已拉取并核对线上源快照 `dep_6d3ce7754a794a42a8f2963862e09ce1`；保留仓库中后续登录与分享修复。
- 移动端备注仅在切换桌面布局时关闭，不再因键盘引起的窗口高度变化关闭。
- 点击每行重量后的 Kg / Lbs 可切换录入单位，数字不换算；旧记录默认为 Kg。单位随记录保存，锁定后显示。
- Runtime Data Schema 升级至 7，部署时需同步发布 schema 和 microsite.json。
- 浏览器回归：启动上述本地服务，安装 Playwright 后运行 `node --test tests/js/training-log.test.cjs`（默认使用 Edge，可通过 PLAYWRIGHT_CHANNEL 指定浏览器）。覆盖移动高度变化、备注持久化、单位切换与锁定、桌面展示。
