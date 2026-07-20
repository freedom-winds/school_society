#!/usr/bin/env bash
# 校园社团中心：服务器一键部署脚本
# 用法：在项目根目录执行 sudo bash scripts/deploy.sh

set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="school-society"
APP_USER="${SUDO_USER:-$(id -un)}"
APP_GROUP="$(id -gn "$APP_USER")"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PORT="${PORT:-8000}"
DOMAIN="soc.shs.alexweb.space"
VENV_DIR="$PROJECT_DIR/.venv"
ENV_FILE="$PROJECT_DIR/.env"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
NGINX_CONFIG="/etc/nginx/conf.d/soc-shs.conf"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "请使用 sudo bash scripts/deploy.sh 运行。" >&2
  exit 1
fi

random_secret() {
  "$PYTHON_BIN" -c 'import secrets; print(secrets.token_urlsafe(48))'
}

random_password() {
  "$PYTHON_BIN" -c 'import secrets; print(secrets.token_urlsafe(14))'
}

mkdir -p "$PROJECT_DIR/backend/instance" "$PROJECT_DIR/backend/uploads"
chown -R "$APP_USER:$APP_GROUP" "$PROJECT_DIR/backend/instance" "$PROJECT_DIR/backend/uploads"

if [[ ! -f "$ENV_FILE" ]]; then
  initial_password="$(random_password)"
  cat > "$ENV_FILE" <<EOF
# 首次部署自动生成；请妥善保管且不要提交到 Git。
SECRET_KEY=$(random_secret)
JWT_SECRET=$(random_secret)
DATABASE_URL=sqlite:///$PROJECT_DIR/backend/instance/club_center.db
UPLOAD_DIR=$PROJECT_DIR/backend/uploads
INITIAL_ADMIN_PASSWORD=$initial_password
EOF
  chmod 600 "$ENV_FILE"
  chown "$APP_USER:$APP_GROUP" "$ENV_FILE"
  printf '\n首次管理员账号：admin\n首次管理员密码：%s\n请立即保存该密码。\n\n' "$initial_password"
fi

sudo -u "$APP_USER" "$PYTHON_BIN" -m venv "$VENV_DIR"
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/backend/requirements.txt"

sudo -u "$APP_USER" npm ci --prefix "$PROJECT_DIR/frontend"
sudo -u "$APP_USER" npm run build --prefix "$PROJECT_DIR/frontend"

set -a
source "$ENV_FILE"
set +a
(
  cd "$PROJECT_DIR/backend"
  sudo -u "$APP_USER" env DATABASE_URL="$DATABASE_URL" "$VENV_DIR/bin/python" -m alembic -c alembic.ini upgrade head
)

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=校园社团中心 Gunicorn 服务
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_GROUP
WorkingDirectory=$PROJECT_DIR/backend
EnvironmentFile=$ENV_FILE
Environment=PYTHONPATH=$PROJECT_DIR/backend
ExecStart=$VENV_DIR/bin/gunicorn --workers 3 --bind 127.0.0.1:$PORT --access-logfile - --error-logfile - wsgi:app
Restart=always
RestartSec=3
TimeoutStopSec=20
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "$APP_NAME"
systemctl restart "$APP_NAME"
systemctl --no-pager --full status "$APP_NAME"

cat > "$NGINX_CONFIG" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    root $PROJECT_DIR/frontend/dist;
    index index.html;
    client_max_body_size 10m;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location /uploads/ {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

nginx -t
systemctl reload nginx

certbot_args=(--nginx --non-interactive --agree-tos --redirect --keep-until-expiring -d "$DOMAIN")
if [[ -n "${CERTBOT_EMAIL:-}" ]]; then
  certbot_args+=(--email "$CERTBOT_EMAIL")
else
  certbot_args+=(--register-unsafely-without-email)
fi
certbot "${certbot_args[@]}"

cat <<EOF

部署完成。

- 站点地址：https://$DOMAIN
- Nginx 配置：$NGINX_CONFIG
- 后端仅监听：127.0.0.1:$PORT
- 前端产物：$PROJECT_DIR/frontend/dist
- 服务名：$APP_NAME
- 查看日志：journalctl -u $APP_NAME -f
EOF
