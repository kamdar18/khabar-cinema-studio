#!/usr/bin/env bash
# Khabar Cinema Studio installer for a fresh Ubuntu 22.04/24.04 droplet (1 GB RAM is enough).
# Usage (as root):  bash install.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_USER=studio
PORT="${APP_PORT:-8765}"  # internal app port (only on 127.0.0.1)
PUBLIC_PORT="${PUBLIC_PORT:-8090}"  # website: http://IP:8090 — does not touch port 80

if [ "$(id -u)" -ne 0 ]; then echo "root tarike chalavo: sudo bash install.sh"; exit 1; fi

echo "==> Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip >/dev/null
if ! command -v nginx >/dev/null; then
  # fresh nginx: its default site would grab port 80 from your other projects, so remove it
  apt-get install -y -qq nginx >/dev/null || true
  rm -f /etc/nginx/sites-enabled/default
fi

if ss -ltn | grep -q ":$PUBLIC_PORT \|:$PORT "; then
  if ! systemctl is-active -q khabar-studio; then
    echo "Port $PUBLIC_PORT ke $PORT pehle thi vaparay che. Bijo port: PUBLIC_PORT=8091 sudo -E bash install.sh"; exit 1
  fi
fi

echo "==> 1 GB swap (1 GB RAM server mate)"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q /swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "==> App user"
id "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"

echo "==> Python packages"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "==> .env"
if [ ! -f "$APP_DIR/.env" ]; then
  read -r -s -p "Website mate navo password lakho (dekhashe nahi): " PW; echo
  [ -n "$PW" ] || { echo "Password khali na chale"; exit 1; }
  cat > "$APP_DIR/.env" <<EOF
ADMIN_PASSWORD=$PW
SECRET_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))')
API_TOKEN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')
MAX_UPLOAD_MB=300
EOF
fi
chmod 600 "$APP_DIR/.env"
mkdir -p "$APP_DIR/data/media"
chown -R "$APP_USER:$APP_USER" "$APP_DIR/data" "$APP_DIR/.env"

echo "==> systemd service"
cat > /etc/systemd/system/khabar-studio.service <<EOF
[Unit]
Description=Khabar Cinema Studio
After=network.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
Environment=DATA_DIR=$APP_DIR/data
ExecStart=$APP_DIR/.venv/bin/gunicorn -w 1 --threads 4 -b 127.0.0.1:$PORT --timeout 900 app:app
Restart=always
MemoryMax=350M

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now khabar-studio
systemctl restart khabar-studio

echo "==> nginx"
cat > /etc/nginx/sites-available/khabar-studio <<EOF
server {
    listen $PUBLIC_PORT;
    server_name _;
    client_max_body_size 300M;
    proxy_read_timeout 900s;
    proxy_send_timeout 900s;
    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_request_buffering off;
    }
}
EOF
ln -sf /etc/nginx/sites-available/khabar-studio /etc/nginx/sites-enabled/khabar-studio
nginx -t -q && { systemctl reload nginx 2>/dev/null || systemctl restart nginx; }

echo "==> Firewall"
# never turn the firewall on here (other projects may need other ports); only open our port if it is already on
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then ufw allow "$PUBLIC_PORT/tcp" >/dev/null; fi

IP=$(curl -s -4 ifconfig.me || hostname -I | awk '{print $1}')
echo
echo "✅ Thai gayu! Phone ma kholo:  http://$IP:$PUBLIC_PORT"
echo "(Tamara bija projects jem chalta hata em j chalshe.)"
