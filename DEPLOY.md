# 部署到 Debian/Ubuntu 服务器

## 一键部署

脚本会安装 Docker、Nginx 和 Certbot，克隆当前仓库，创建持久化数据目录，并配置
Nginx 流式上传、Range 数据面与 HTTPS：

```bash
curl -fsSL https://raw.githubusercontent.com/vongoley/microsite-container/main/deploy.sh | bash
```

默认安装目录为 `/opt/microsite-container`。已有目录会执行 `git pull`，已有 `.env` 不会
被覆盖。

## 手动部署

```bash
apt update
apt install -y docker.io docker-compose-plugin nginx

git clone https://github.com/vongoley/microsite-container.git /opt/microsite-container
cd /opt/microsite-container
cp .env.example .env
```

至少填写：

```text
ADMIN_PASSWORD_HASH=<sha256>
SESSION_SECRET=<random secret>
API_KEY=<random api key>
MICROSITE_ACCEL_PREFIX=/_protected_microsite_blobs
```

生成值：

```bash
python3 -c "import hashlib; print(hashlib.sha256(b'your-password').hexdigest())"
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

启动：

```bash
docker compose --env-file .env up -d --build
```

## Nginx

应用容器监听宿主机 `127.0.0.1:8080`。推荐配置：

```nginx
server {
    listen 80;
    server_name your-domain.com;

    # 大型音视频 blob；实际大小仍受应用配额限制。
    client_max_body_size 0;

    # 必须与 MICROSITE_ACCEL_PREFIX 一致，不能允许客户端直接访问。
    location /_protected_microsite_blobs/ {
        internal;
        alias /opt/microsite-container/data/microsites/blobs/;
    }

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_request_buffering off;
        proxy_read_timeout 300s;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

`internal` 很重要：浏览器只能通过 `/sites/...` 或 `/_deployments/...` 访问资源，FastAPI
先解析活动部署和文件映射，再让 Nginx 发送对应哈希文件。Nginx 原生处理 Range 请求。

HTTPS：

```bash
certbot --nginx -d your-domain.com
```

## 数据持久化与备份

宿主机 `./data` 映射到容器 `/app/app/data`，包含：

- `html_store.db`：控制面元数据
- `microsites/blobs/`：内容寻址资源
- `microsites/tmp/`：未完成上传的临时文件
- `uploads/`：继承的单文件页面

一致性备份需要同时保存 SQLite 和 `microsites/blobs/`。不要只备份数据库。

## 安装 Skill

在后台生成个人 API Token 后：

```bash
curl -fsSL "https://your-domain.com/api/install-skill?token=YOUR_TOKEN" | bash
python3 ~/.codex/skills/microsite-container/scripts/deploy.py check
```

Claude Code：

```bash
curl -fsSL "https://your-domain.com/api/install-skill?token=YOUR_TOKEN&target=claude" | bash
```

配置位于 `~/.config/microsite-container/credentials.env`。CLI 直接连接服务地址，不读取
HTTP 代理环境变量，因此公司内网部署不需要额外的 Clash 绕过命令。

## 更新

```bash
cd /opt/microsite-container
git pull
docker compose --env-file .env up -d --build
```

部署记录和 blob 均在挂载卷中，重建容器不会丢失。
