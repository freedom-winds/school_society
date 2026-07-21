#!/usr/bin/env bash
# 校园社团中心：拉取代码后的热更新脚本
# 用法：git pull 后，在项目根目录执行 sudo bash scripts/hot-update.sh

set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="school-society"
APP_USER="${SUDO_USER:-$(id -un)}"
VENV_DIR="$PROJECT_DIR/.venv"
ENV_FILE="$PROJECT_DIR/.env"
NGINX_CONFIG="/etc/nginx/conf.d/soc-shs.conf"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "请使用 sudo bash scripts/hot-update.sh 运行。" >&2
  exit 1
fi

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

if [[ -f "$NGINX_CONFIG" ]]; then
  if grep -qE '^[[:space:]]*client_max_body_size[[:space:]]+' "$NGINX_CONFIG"; then
    sed -Ei 's/^[[:space:]]*client_max_body_size[[:space:]]+[^;]+;/    client_max_body_size 30m;/' "$NGINX_CONFIG"
  else
    sed -Ei '/^[[:space:]]*index[[:space:]]+index\.html;/a\    client_max_body_size 30m;' "$NGINX_CONFIG"
  fi
fi

systemctl restart "$APP_NAME"
nginx -t
systemctl reload nginx
systemctl --no-pager --full status "$APP_NAME"

echo "热更新完成。"
