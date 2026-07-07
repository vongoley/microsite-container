#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/opt/html-container"
REPO="https://github.com/vongoley/html-container.git"
DEFAULT_DOMAIN="csbiwithai.intsig.net"

read -rp "请输入要绑定的域名 [${DEFAULT_DOMAIN}]: " DOMAIN
DOMAIN=${DOMAIN:-$DEFAULT_DOMAIN}

echo "=============================="
echo " HTML Container 一键部署脚本"
echo " 域名: $DOMAIN"
echo "=============================="

# ── 1. 安装依赖 ──────────────────────────────────────────────
echo ""
echo "[1/6] 安装 Docker、Nginx、Certbot ..."
apt update -qq
apt install -y docker.io docker-compose-plugin nginx certbot python3-certbot-nginx git

systemctl enable --now docker
systemctl enable --now nginx

# ── 2. 克隆仓库 ──────────────────────────────────────────────
echo ""
echo "[2/6] 克隆仓库到 $INSTALL_DIR ..."
if [ -d "$INSTALL_DIR" ]; then
    echo "  目录已存在，执行 git pull ..."
    cd "$INSTALL_DIR"
    git pull
else
    git clone "$REPO" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# ── 3. 配置环境变量 ──────────────────────────────────────────
echo ""
echo "[3/6] 配置环境变量 ..."

if [ -f .env ]; then
    echo "  .env 已存在，跳过生成（如需重置请先删除 .env）"
else
    read -rp "  请输入管理员用户名 [admin]: " ADMIN_USER
    ADMIN_USER=${ADMIN_USER:-admin}

    while true; do
        read -rsp "  请输入管理员密码: " ADMIN_PASS
        echo
        read -rsp "  再次确认密码: " ADMIN_PASS2
        echo
        if [ "$ADMIN_PASS" = "$ADMIN_PASS2" ]; then
            break
        fi
        echo "  密码不一致，请重新输入"
    done

    PASS_HASH=$(python3 -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" "$ADMIN_PASS")
    SESSION_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")

    cat > .env <<ENVEOF
ADMIN_USERNAME=${ADMIN_USER}
ADMIN_PASSWORD_HASH=${PASS_HASH}
SESSION_SECRET=${SESSION_SECRET}
ENVEOF

    echo "  .env 已生成"
fi

# ── 4. 启动 Docker 容器 ─────────────────────────────────────
echo ""
echo "[4/6] 构建并启动 Docker 容器 ..."
docker compose --env-file .env down 2>/dev/null || true
docker compose --env-file .env up -d --build

echo "  等待服务启动 ..."
sleep 3

if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/admin/login | grep -q "200"; then
    echo "  服务启动成功 ✓"
else
    echo "  警告: 服务可能未完全启动，请稍后检查 docker compose logs"
fi

# ── 5. 配置 Nginx 反向代理 ──────────────────────────────────
echo ""
echo "[5/6] 配置 Nginx 反向代理 ..."

cat > /etc/nginx/sites-available/html-container <<NGINXEOF
server {
    listen 80;
    server_name ${DOMAIN};

    client_max_body_size 20M;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGINXEOF

ln -sf /etc/nginx/sites-available/html-container /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

nginx -t && systemctl reload nginx
echo "  Nginx 配置完成 ✓"

# ── 6. 申请 HTTPS 证书 ─────────────────────────────────────
echo ""
echo "[6/6] 申请 HTTPS 证书 ..."
echo "  请确保 DNS 已将 ${DOMAIN} 解析到本服务器 IP"
read -rp "  是否现在申请证书？(y/n) [y]: " DO_CERT
DO_CERT=${DO_CERT:-y}

if [ "$DO_CERT" = "y" ] || [ "$DO_CERT" = "Y" ]; then
    read -rp "  输入接收证书通知的邮箱: " CERT_EMAIL
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$CERT_EMAIL" && \
        echo "  HTTPS 证书申请成功 ✓" || \
        echo "  证书申请失败，请检查 DNS 解析是否生效后手动执行: certbot --nginx -d $DOMAIN"
else
    echo "  跳过。之后手动执行: certbot --nginx -d $DOMAIN"
fi

# ── 完成 ─────────────────────────────────────────────────────
echo ""
echo "=============================="
echo " 部署完成!"
echo ""
echo " 管理后台: https://${DOMAIN}/admin"
echo " 分享链接: https://${DOMAIN}/view/<page_id>"
echo ""
echo " 常用命令:"
echo "   查看日志:  cd $INSTALL_DIR && docker compose logs -f"
echo "   重启服务:  cd $INSTALL_DIR && docker compose restart"
echo "   更新部署:  cd $INSTALL_DIR && git pull && docker compose --env-file .env up -d --build"
echo "=============================="
