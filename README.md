# HTML Container

轻量级 HTML 页面托管与分享服务。上传 HTML 文件，获取分享链接，支持多用户权限管理和 API 自动化上传。

## 功能特性

- **HTML 托管**：上传 .html 文件，生成独立的访问链接
- **Slug 路由**：支持自定义可读 URL（如 `/view/project/report`），同 slug 重复上传自动替换
- **多管理员**：超级管理员 + 普通管理员角色，邀请制注册
- **权限隔离**：普通管理员只能管理自己的页面，超管可管理全部
- **REST API**：程序化上传/替换/删除，适合 CI/CD 或 Claude Code 自动化
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

所有 API 接口使用 `Authorization: Bearer <API_KEY>` 认证。

| 方法 | 路径 | 说明 |
|------|------|------|
| `PUT` | `/api/pages/{slug}` | 创建或替换页面（同 slug 自动覆盖） |
| `GET` | `/api/pages` | 列出所有页面 |
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
{"slug": "project/report", "id": "a1b2c3d4", "url": "/view/project/report"}
```

## 用户权限

| 角色 | 查看页面 | 上传 | 替换/删除 | 管理用户 |
|------|:---:|:---:|:---:|:---:|
| super_admin | 全部 | 可以 | 全部 | 可以 |
| admin | 仅自己 | 可以 | 仅自己 | 不可以 |

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
