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

## 权限说明

| 角色 | 访问路径 | 能做什么 |
|------|---------|---------|
| 管理员 | `/admin` | 登录后可上传、查看列表、删除、复制分享链接 |
| 被分享者 | `/view/<id>` | 只能渲染单个 HTML，无法获知其他页面 |
