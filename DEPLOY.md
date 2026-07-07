# 部署到 Debian 服务器

## 前置要求

```bash
# 安装 Docker & Docker Compose
apt update && apt install -y docker.io docker-compose-plugin
```

## 快速部署

```bash
# 1. 上传项目目录到服务器（排除 .venv/）
rsync -av --exclude='.venv' --exclude='data' . user@your-server:/opt/html-container/

# 2. 在服务器上配置环境变量
cd /opt/html-container
cp .env.example .env

# 生成密码哈希（把 your-password 换成你的密码）
python3 -c "import hashlib; print(hashlib.sha256(b'your-password').hexdigest())"

# 生成随机 SESSION_SECRET
python3 -c "import secrets; print(secrets.token_hex(32))"

# 编辑 .env 填入上面两个值
nano .env

# 3. 启动
docker compose --env-file .env up -d --build
```

## 反向代理（Nginx，推荐）

```nginx
server {
    listen 80;
    server_name your-domain.com;

    client_max_body_size 20M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

HTTPS 用 certbot：`certbot --nginx -d your-domain.com`

## 数据持久化

上传的文件和数据库存放在 `./data/`，docker compose 已挂载为 volume，容器重建不丢失。

## 更新部署

```bash
docker compose down
docker compose --env-file .env up -d --build
```

## 安装上传 Skill

登录后台后，进入 `API Token` 页面生成个人 Token，然后在本机安装上传工具。

默认安装到 Codex：

```bash
curl -fsSL "http://your-domain.com/api/install-skill?token=YOUR_API_TOKEN" | bash
```

如需继续给 Claude Code 使用：

```bash
curl -fsSL "http://your-domain.com/api/install-skill?token=YOUR_API_TOKEN&target=claude" | bash
```

安装后凭据写入 `~/.config/html-container/credentials.env`，Codex skill 路径为 `~/.codex/skills/html-container`，Claude Code skill 路径为 `~/.claude/skills/html-container`。

如果服务部署在公司内网域名（如 `csbiwithai.intsig.net`），本机调用上传脚本时需要绕过 Clash/HTTP 代理：

```bash
COMPANY_NO_PROXY="env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy NO_PROXY=csbiwithai.intsig.net,.intsig.net,localhost,127.0.0.1 no_proxy=csbiwithai.intsig.net,.intsig.net,localhost,127.0.0.1"
$COMPANY_NO_PROXY python3 ~/.codex/skills/html-container/scripts/upload.py check
```

## 上传说明

支持上传 `.html` 和 `.md` 文件。Markdown 文件应直接上传原文，访问时服务端页面会用 `marked.js` 渲染并生成目录，不需要先用脚本或 Pandoc 转成 HTML。

同一 slug 重新上传会替换内容；如果通过 API 上传时不传 `visibility`，会保留原页面访问权限。

## 权限说明

| 角色 / 访问者 | 访问路径 | 能做什么 |
|------|---------|---------|
| 超级管理员 | `/admin` | 管理全部页面、用户、邀请、Token |
| 管理员 | `/admin` | 上传、管理自己的页面，复制分享链接，生成个人 Token |
| 被分享者 | `/view/<id>` 或 `/view/<slug>` | 按页面可见性访问 HTML / Markdown 页面 |

页面可见性支持 `public`、`private`、`password`、`users_all`、`users_specific` 五种模式。
