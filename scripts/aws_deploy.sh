#!/usr/bin/env bash
#
# One-shot deploy/setup for QuizCraft on an Amazon Linux 2023 EC2 instance.
#
# It is idempotent: run it once to provision the box (Python, nginx, a systemd
# service), and run it again on every code update -- it re-syncs the source,
# reinstalls dependencies, runs collectstatic + migrate and restarts the app.
#
# Data lives in Cloud Firestore (DATA_STORE=firestore), so nothing important is
# written to this instance's disk. The uploaded source file is only read once
# for text extraction; set DISCARD_SOURCE_UPLOADS=1 (the script's default) so
# the bytes are dropped immediately and no S3 bucket is needed.
#
# Usage (on the EC2 box, as root):
#
#   sudo ./scripts/aws_deploy.sh                       # use the checkout this script lives in
#   sudo ./scripts/aws_deploy.sh --source git@github.com:me/quizcraft.git
#   sudo ./scripts/aws_deploy.sh --source /tmp/quizcraft --host ec2-1-2-3-4.compute.amazonaws.com
#
# Before the first run, put your Firebase service-account JSON at
# "$APP_DIR/firebase_credentials.json" and fill in "$APP_DIR/.env" (the script
# seeds it from .env.example and then tells you what is missing).
#
# Not handled here (do these in the AWS console):
#   * Security group: allow inbound TCP 80 from the world (and 22 from your IP).
#   * This script serves plain HTTP. Add a certificate / load balancer later and
#     then drop DJANGO_SSL_REDIRECT=0 from the server .env.
#
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/quizcraft}
APP_USER=${APP_USER:-quizcraft}
APP_PORT=${APP_PORT:-8000}
GUNICORN_WORKERS=${GUNICORN_WORKERS:-2}
GUNICORN_THREADS=${GUNICORN_THREADS:-4}

SOURCE=""
APP_HOST=""

usage() {
    sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --source) SOURCE=${2:?--source needs a value}; shift 2 ;;
        --host)   APP_HOST=${2:?--host needs a value}; shift 2 ;;
        -h|--help) usage 0 ;;
        *) echo "Unknown argument: $1" >&2; usage 1 ;;
    esac
done

log()  { echo "[aws_deploy] $*"; }
warn() { echo "[aws_deploy] WARNING: $*" >&2; }
die()  { echo "[aws_deploy] ERROR: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run this as root (sudo)."

# If no --source was given, assume the script is inside the repo checkout.
if [ -z "$SOURCE" ]; then
    SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
    SOURCE=$(cd "$SCRIPT_DIR/.." && pwd)
fi
is_git_remote() { [[ "$1" == *://* || "$1" == git@* || "$1" == *.git ]]; }
if [ ! -f "$SOURCE/manage.py" ] && [ ! -d "$SOURCE/.git" ] && ! is_git_remote "$SOURCE"; then
    die "--source must be a repo checkout (with manage.py) or a git URL; got: $SOURCE"
fi

command -v dnf >/dev/null 2>&1 || die "this script targets Amazon Linux 2023 (dnf not found)."

# --- 1. System packages ---------------------------------------------------
log "Installing system packages..."
PYTHON_BIN=""
for cand in python3.13 python3.12; do
    if command -v "$cand" >/dev/null 2>&1; then PYTHON_BIN=$(command -v "$cand"); break; fi
done

dnf install -y python3.12 nginx git rsync >/dev/null

PYTHON_BIN=${PYTHON_BIN:-$(command -v python3.12 || true)}
[ -n "$PYTHON_BIN" ] || die "python3.12 not found after install; Django 6 needs Python >= 3.12."
"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' \
    || die "$PYTHON_BIN is older than 3.12."
log "Using $($PYTHON_BIN --version) at $PYTHON_BIN"

# --- 2. Service account ---------------------------------------------------
if ! id -u "$APP_USER" >/dev/null 2>&1; then
    log "Creating system user $APP_USER..."
    useradd --system --home-dir "$APP_DIR" --shell /sbin/nologin "$APP_USER"
fi

# --- 3. Fetch the source --------------------------------------------------
fetch_source() {
    if [ -d "$SOURCE/.git" ] || is_git_remote "$SOURCE"; then
        if [ -d "$APP_DIR/.git" ]; then
            log "Updating git checkout in $APP_DIR..."
            sudo -u "$APP_USER" git -C "$APP_DIR" fetch --prune
            sudo -u "$APP_USER" git -C "$APP_DIR" reset --hard origin/HEAD
        else
            [ -z "$(ls -A "$APP_DIR" 2>/dev/null)" ] || die "$APP_DIR is not empty; refusing to clone over it."
            log "Cloning $SOURCE into $APP_DIR..."
            install -d -o "$APP_USER" -g "$APP_USER" "$APP_DIR"
            sudo -u "$APP_USER" git clone "$SOURCE" "$APP_DIR"
        fi
    else
        log "Syncing local checkout $SOURCE -> $APP_DIR..."
        install -d -o "$APP_USER" -g "$APP_USER" "$APP_DIR"
        rsync -a --delete --chown="$APP_USER:$APP_USER" \
            --exclude='.git' --exclude='.env' --exclude='db.sqlite3' \
            --exclude='media' --exclude='staticfiles' --exclude='__pycache__' \
            --exclude='.venv' --exclude='firebase_credentials.json' \
            "$SOURCE"/ "$APP_DIR"/
    fi
}

install -d -o "$APP_USER" -g "$APP_USER" "$APP_DIR"
chmod 755 "$APP_DIR"
fetch_source

# --- 4. Virtualenv + dependencies ----------------------------------------
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
    log "Creating virtualenv..."
    sudo -u "$APP_USER" "$PYTHON_BIN" -m venv "$APP_DIR/.venv"
fi
if [ ! -x "$APP_DIR/.venv/bin/pip" ]; then
    sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" -m ensurepip --upgrade
fi
log "Installing Python dependencies..."
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip >/dev/null
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# --- 5. Configuration -----------------------------------------------------
ENV_FILE="$APP_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    log "Seeding $ENV_FILE from .env.example..."
    cp "$APP_DIR/.env.example" "$ENV_FILE"
    {
        echo ""
        echo "# --- Added by aws_deploy.sh ---------------------------------------------"
        echo "DJANGO_DEBUG=0"
        echo "DJANGO_SSL_REDIRECT=0"
        echo "DATA_STORE=firestore"
        echo "DISCARD_SOURCE_UPLOADS=1"
        echo "FIREBASE_CREDENTIAL_PATH=$APP_DIR/firebase_credentials.json"
    } >> "$ENV_FILE"
fi
chown "$APP_USER:$APP_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"

if [ -n "$APP_HOST" ]; then
    if grep -q '^DJANGO_ALLOWED_HOSTS=' "$ENV_FILE"; then
        sed -i "s|^DJANGO_ALLOWED_HOSTS=.*|DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,$APP_HOST|" "$ENV_FILE"
    else
        echo "DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,$APP_HOST" >> "$ENV_FILE"
    fi
    log "Set DJANGO_ALLOWED_HOSTS to include $APP_HOST"
fi

CREDS_PATH=$(grep -E '^FIREBASE_CREDENTIAL_PATH=' "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)
if [ -n "$CREDS_PATH" ] && [ ! -f "$CREDS_PATH" ]; then
    warn "Firebase credentials not found at $CREDS_PATH -- signup/login will fail until you copy it there."
fi
if grep -qE '^DJANGO_SECRET_KEY=(change-me-to-something-random)?$' "$ENV_FILE"; then
    warn "DJANGO_SECRET_KEY is still the placeholder -- set a real one in $ENV_FILE."
fi
if ! grep -qE '^GEMINI_API_KEY=.+' "$ENV_FILE"; then
    warn "GEMINI_API_KEY is empty -- quiz generation will fail until you set it in $ENV_FILE."
fi

# --- 6. collectstatic + migrate ------------------------------------------
log "Running collectstatic..."
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" "$APP_DIR/manage.py" collectstatic --noinput >/dev/null

log "Running migrate..."
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" "$APP_DIR/manage.py" migrate --noinput

# nginx (runs as its own user) must be able to read the collected static files.
chmod 755 "$APP_DIR/staticfiles"

# --- 7. systemd unit ------------------------------------------------------
log "Writing /etc/systemd/system/quizcraft.service..."
cat > /etc/systemd/system/quizcraft.service <<EOF
[Unit]
Description=QuizCraft (gunicorn)
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
Environment=PYTHONUNBUFFERED=1
# Note: no --max-requests. Generation runs in a daemon thread inside the worker,
# so recycling the worker would kill in-flight quizzes (they would stay stuck
# in "processing"). If you add --max-requests later, expect that.
ExecStart=$APP_DIR/.venv/bin/gunicorn quizcraft.wsgi:application \\
    --bind 127.0.0.1:$APP_PORT \\
    --workers $GUNICORN_WORKERS \\
    --threads $GUNICORN_THREADS \\
    --timeout 180 \\
    --access-logfile - \\
    --error-logfile -
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# --- 8. nginx -------------------------------------------------------------
log "Writing /etc/nginx/conf.d/quizcraft.conf..."
cat > /etc/nginx/conf.d/quizcraft.conf <<EOF
server {
    listen 80 default_server;
    server_name _;

    client_max_body_size 16m;

    location /static/ {
        alias $APP_DIR/staticfiles/;
        access_log off;
        expires 30d;
    }

    location / {
        proxy_pass http://127.0.0.1:$APP_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 180s;
    }
}
EOF

# --- 9. Start / restart services -----------------------------------------
log "Enabling and restarting services..."
systemctl daemon-reload
systemctl enable nginx quizcraft >/dev/null 2>&1 || true
systemctl restart nginx
systemctl restart quizcraft

if systemctl is-active --quiet firewalld; then
    log "Opening port 80 in firewalld..."
    firewall-cmd --permanent --add-service=http >/dev/null
    firewall-cmd --reload >/dev/null
fi

# --- 10. Health check -----------------------------------------------------
sleep 2
if curl -fsS "http://127.0.0.1/health/" >/dev/null 2>&1; then
    log "Health check passed: http://127.0.0.1/health/ replied OK."
else
    warn "Health check failed. Inspect logs with: journalctl -u quizcraft -n 50 --no-pager"
fi

cat <<EOF

Deployed. Open http://<your-ec2-public-ip-or-dns>/ once the security group
allows inbound port 80.

Still to do by hand:
  * Set DJANGO_SECRET_KEY and GEMINI_API_KEY in $ENV_FILE, then:
      sudo systemctl restart quizcraft
  * Copy the Firebase service-account JSON to $CREDS_PATH
  * Add DJANGO_ALLOWED_HOSTS=<your-host> (re-run with --host <host> to do this)
  * When you add HTTPS (certificate or load balancer), remove
    DJANGO_SSL_REDIRECT=0 from $ENV_FILE so cookies/redirects become secure.

Logs: journalctl -u quizcraft -f   |   nginx: /var/log/nginx/
EOF
