---
name: html-container
description: |
  HTML Container 上传工具。将本地 HTML/Markdown 文件上传到服务器进行托管和分享。
  支持上传新页面、替换已有页面、列出所有页面、删除页面。
  通过 slug 标识页面，同一 slug 重复上传会自动覆盖旧版本。
  支持 .html 和 .md 文件，markdown 会自动渲染。

  Triggers when user mentions:
  - "上传HTML", "上传html", "upload html", "发布HTML", "发布html"
  - "上传markdown", "上传md", "upload markdown"
  - "html-container", "HTML Container"
  - "分享页面", "分享HTML", "托管HTML", "托管html"
  - "部署到网站", "发布到网站", "推送HTML", "推送文件"
---

## 初始化检查

在处理任何上传任务之前，先运行：

```bash
python3 ~/.claude/skills/html-container/scripts/upload.py check
```

根据输出的 `status` 字段：

### status = "ok"
凭据有效，直接执行用户任务。

### status = "missing_config"
凭据未配置。通过 AskUserQuestion 向用户收集：
- API Key（从服务器 .env 中获取）

收到后写入凭据文件：
```bash
mkdir -p ~/.config/html-container
printf 'API_KEY=%s\nBASE_URL=%s\n' 'KEY_VALUE' 'https://html.orcacalf.site' \
  > ~/.config/html-container/credentials.env
```

写入后再次运行 `upload.py check` 验证。

### status = "api_error"
凭据格式正确但 API 调用失败，将 `error` 字段告知用户。

---

## 使用方式

### 上传或替换 HTML 文件

```bash
python3 ~/.claude/skills/html-container/scripts/upload.py put \
    --slug "项目名/页面名" \
    --title "页面标题" \
    --file path/to/file.html
```

- `--slug`：页面标识符，支持多级路径（如 `workspace/project/report`）。同一 slug 重复上传会自动替换旧版本。
- `--title`：页面标题，用于管理后台列表展示。可选，默认使用 slug。
- `--file`：要上传的 HTML 文件路径。

输出示例：
```json
{"status": "ok", "slug": "my-project/report", "id": "a1b2c3d4", "url": "https://html.orcacalf.site/view/my-project/report"}
```

### 列出所有页面

```bash
python3 ~/.claude/skills/html-container/scripts/upload.py list
```

输出 JSON 数组，包含所有已上传页面的 id、title、slug、uploaded_at。

### 为已有页面设置/修改 slug

对于历史上传时未指定 slug 的页面，或需要更改 slug 的页面：

```bash
python3 ~/.claude/skills/html-container/scripts/upload.py set-slug \
    --id "页面ID或当前slug" \
    --new-slug "新的slug" \
    --title "可选：更新标题"
```

- `--id`：页面的 ID（如 `6ea8401a`）或当前 slug
- `--new-slug`：要设置的新 slug
- `--title`：可选，同时更新标题

工作原理：下载现有页面内容 → 上传到新 slug → 尝试删除旧页面。
注意：如果旧页面没有 slug（仅有 ID），后端不支持通过 ID 删除，旧页面会残留但不影响使用。

### 删除页面

```bash
python3 ~/.claude/skills/html-container/scripts/upload.py delete --slug "项目名/页面名"
```

---

## Slug 命名建议

推荐使用 `工作区名/报告名` 格式，例如：
- `bi-workspace/monthly-report`
- `app-analysis/user-funnel`
- `destiny-insight/auth-flow`

Slug 仅允许：字母、数字、连字符、下划线、点、斜杠。

---

## 典型场景

### 场景 1：生成分析报告后直接发布

用户让你生成一个 HTML 分析报告，完成后直接上传：

```bash
python3 ~/.claude/skills/html-container/scripts/upload.py put \
    --slug "bi-workspace/user-analysis" \
    --title "用户分析报告" \
    --file ./output/report.html
```

### 场景 2：反复调整 HTML 并更新

用户多次修改同一个 HTML 文件，每次修改后用同一个 slug 上传即可自动覆盖：

```bash
python3 ~/.claude/skills/html-container/scripts/upload.py put \
    --slug "project/dashboard" \
    --title "Dashboard v2" \
    --file ./dashboard.html
```

### 场景 3：查看当前已发布的页面

```bash
python3 ~/.claude/skills/html-container/scripts/upload.py list
```

---

## 凭据说明

| Key | 说明 |
|-----|------|
| `API_KEY` | HTML Container API Key |
| `BASE_URL` | 服务地址，默认 `https://html.orcacalf.site` |

凭据文件路径：`~/.config/html-container/credentials.env`
