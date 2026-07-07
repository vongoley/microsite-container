# HTML Container

轻量级 HTML / Markdown 页面托管与分享服务。上传 `.html` 或 `.md` 文件，获取分享链接，支持多用户权限管理和 API 自动化上传。

## 功能特性

- **HTML / Markdown 托管**：上传 `.html` 或 `.md` 文件，生成独立的访问链接；Markdown 会在访问时自动渲染并生成目录
- **Slug 路由**：支持自定义可读 URL（如 `/view/project/report`），同 slug 重复上传自动替换
- **多管理员**：超级管理员 + 普通管理员角色，邀请制注册
- **权限隔离**：普通管理员只能管理自己的页面，超管可管理全部
- **访问控制**：支持公开、仅自己、密码、所有登录用户、指定用户五种可见性
- **REST API**：程序化上传/替换/删除，适合 CI/CD、Codex 或 Claude Code 自动化
- **Codex Skill 安装**：提供 `/api/install-skill` 安装脚本，默认安装到 Codex，兼容 Claude Code
- **响应式 UI**：桌面端表格 + 移动端卡片布局，支持拖拽上传
- **Docker 部署**：一键部署，SQLite 存储无需外部数据库

## 技术栈

| 组件 | 技术 |
|------|------|
| 后端 | FastAPI + Uvicorn |
| 数据库 | SQLite |
| 模板 | Jinja2 |
| 部署 | Docker + Nginx |
| 依赖 | 仅 Python 标准库 + FastAPI |

## 快速开始

### 本地开发

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 启动（默认账号 admin/admin123）
uvicorn app.main:app --reload --port 8000
```

访问 http://localhost:8000/admin/login

### Docker 部署

```bash
cp .env.example .env
# 编辑 .env 填入密码哈希和 session secret

docker compose --env-file .env up -d --build
```

## 环境变量

| 变量 | 说明 | 必填 |
|------|------|:---:|
| `ADMIN_USERNAME` | 超级管理员用户名（仅首次初始化使用） | 否，默认 `admin` |
| `ADMIN_PASSWORD_HASH` | 超级管理员密码的 SHA-256 哈希 | 是 |
| `SESSION_SECRET` | Session 签名密钥 | 是 |
| `API_KEY` | REST API 认证密钥 | 否 |

生成密码哈希：
```bash
python3 -c "import hashlib; print(hashlib.sha256(b'your-password').hexdigest())"
```

生成 Session Secret / API Key：
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

## API 接口

所有 API 接口使用 `Authorization: Bearer <API_KEY>` 认证。既支持环境变量里的全局 `API_KEY`，也支持用户在后台生成的个人 API Token。

| 方法 | 路径 | 说明 |
|------|------|------|
| `PUT` | `/api/pages/{slug}` | 创建或替换页面（同 slug 自动覆盖） |
| `GET` | `/api/pages` | 列出所有页面 |
| `GET` | `/api/users` | 列出活跃用户，用于指定用户授权 |
| `DELETE` | `/api/pages/{slug}` | 删除页面 |

### 上传示例

```bash
curl -X PUT https://your-domain.com/api/pages/project/report \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -F "title=项目报告" \
  -F "file=@output.html"
```

响应：
```json
{"slug": "project/report", "id": "a1b2c3d4", "url": "/view/project/report", "visibility": "public"}
```

### Markdown 上传

`.md` 文件可以直接上传，不需要先转成 HTML：

```bash
curl -X PUT https://your-domain.com/api/pages/project/readme \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -F "title=项目说明" \
  -F "file=@README.md"
```

访问 `/view/project/readme` 时，服务会用 `marked.js` 渲染 Markdown，并生成左侧目录。

### 访问权限参数

`PUT /api/pages/{slug}` 支持以下可选表单参数：

| 参数 | 说明 |
|------|------|
| `visibility` | `public`、`private`、`password`、`users_all`、`users_specific` |
| `view_password` | `password` 模式下的访问密码，至少 8 位 |
| `allowed_user_ids` | `users_specific` 模式下允许访问的用户 ID，可重复提交多个 |

同一 slug 重新上传时，如果不传 `visibility`，会保留原页面的访问权限，不会把受限页面降级为公开。

### 安装 Skill

登录后台生成 API Token 后，可以通过安装脚本把上传工具安装到本机 Agent：

```bash
# 默认安装到 Codex: ~/.codex/skills/html-container
curl -fsSL "https://your-domain.com/api/install-skill?token=YOUR_API_TOKEN" | bash

# 如需安装给 Claude Code: ~/.claude/skills/html-container
curl -fsSL "https://your-domain.com/api/install-skill?token=YOUR_API_TOKEN&target=claude" | bash
```

安装后的 CLI 配置在 `~/.config/html-container/credentials.env`，脚本路径为 `~/.codex/skills/html-container/scripts/upload.py` 或 `~/.claude/skills/html-container/scripts/upload.py`。

## 用户权限

| 角色 | 查看页面 | 上传 | 替换/删除 | 管理用户 |
|------|:---:|:---:|:---:|:---:|
| super_admin | 全部 | 可以 | 全部 | 可以 |
| admin | 自己上传的页面；以及被授权访问的页面 | 可以 | 仅自己 | 不可以 |

### 添加新用户

1. 超管登录 → 用户管理 → 生成邀请链接
2. 将 `/admin/register/<code>` 链接发给成员
3. 成员通过邀请链接注册

## 项目结构

```
├── app/
│   ├── main.py              # FastAPI 应用（路由、认证、API）
│   ├── templates/
│   │   ├── admin.html       # 管理后台（上传、页面列表）
│   │   ├── login.html       # 登录页
│   │   ├── register.html    # 邀请注册页
│   │   └── users.html       # 用户管理页（超管）
│   └── data/
│       ├── html_store.db    # SQLite 数据库（git 忽略）
│       └── uploads/         # 上传的 HTML 文件（git 忽略）
├── Dockerfile
├── docker-compose.yml
├── deploy.sh                # 一键部署脚本（Debian/Ubuntu）
├── requirements.txt
└── .env.example
```

## 部署到服务器

详见 [DEPLOY.md](DEPLOY.md)，或使用一键脚本：

```bash
curl -fsSL https://raw.githubusercontent.com/vongoley/html-container/main/deploy.sh | bash
```

## License

MIT
