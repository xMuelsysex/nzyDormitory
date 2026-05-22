# Dorm Electricity Monitor

A lightweight full-stack web app for monitoring dorm electricity balance from the campus self-service electricity purchase system.

## Runtime

- Python 3.11+
- No third-party Python dependencies required for the initial implementation
- Designed for Ubuntu server deployment

## Local quick start

```bash
python -m backend.app.main
```

Open `http://127.0.0.1:8000` in a browser.

## Environment variables

```text
APP_HOST=127.0.0.1
APP_PORT=8000
APP_TIMEZONE=Asia/Shanghai
DATA_DIR=./data
CAMPUS_LOGIN_URL=https://webvpn.njucm.edu.cn/http/webvpn34f6d2940beaaa8a549e2c772ae7c064/Default.aspx
CAMPUS_ELECTRICITY_URL=https://webvpn.njucm.edu.cn/http/webvpn34f6d2940beaaa8a549e2c772ae7c064/Web/Student/FeeElect.aspx
SESSION_KEEP_ALIVE_INTERVAL_SECONDS=300
PERSIST_PORTAL_COOKIES=false
PORTAL_COOKIE_PATH=./data/portal_cookies.txt
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM=
```

SMTP settings are required only when email alerts are enabled.

`SESSION_KEEP_ALIVE_INTERVAL_SECONDS` controls the independent campus portal keep-alive interval and defaults to 300 seconds. `PERSIST_PORTAL_COOKIES` is disabled by default; if set to `true`, the app stores the campus portal session cookie file at `PORTAL_COOKIE_PATH` with best-effort `0600` permissions so a trusted single-user deployment can survive restarts. The cookie file is a login credential, so enable this only on trusted hosts.

`CAMPUS_LOGIN_URL` and `CAMPUS_ELECTRICITY_URL` default to the NJUCM WebVPN portal paths above. Override them only when the campus portal path changes or when deploying in an environment with a different reachable campus gateway.

## Docker deployment on Ubuntu

These steps start from a fresh Ubuntu server and deploy from GitHub with Docker Compose.

### 1. Install prerequisites

```bash
sudo apt update
sudo apt install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
```

Optional: allow your current user to run Docker without `sudo`.

```bash
sudo usermod -aG docker "$USER"
newgrp docker
```

### 2. Clone the project

```bash
git clone https://github.com/gy1041/nzyDormitory.git
cd nzyDormitory
```

### 3. Review Docker files

The repository includes:

- `Dockerfile`: builds a Python 3.11 image and runs `python -m backend.app.main`.
- `.dockerignore`: keeps local data, caches, deployment `.env`, Trellis metadata, and Git metadata out of the image build context.
- `docker-compose.yml`: exposes the app on port `8000`, sets the WebVPN campus URL defaults, and mounts persistent data at `/app/data`.

### 4. Configure deployment environment

Create `.env` only for values that differ from the defaults or for SMTP alert settings.

```bash
cat > .env <<'EOF'
CAMPUS_LOGIN_URL=https://webvpn.njucm.edu.cn/http/webvpn34f6d2940beaaa8a549e2c772ae7c064/Default.aspx
CAMPUS_ELECTRICITY_URL=https://webvpn.njucm.edu.cn/http/webvpn34f6d2940beaaa8a549e2c772ae7c064/Web/Student/FeeElect.aspx

# Optional session continuity settings
SESSION_KEEP_ALIVE_INTERVAL_SECONDS=300
PERSIST_PORTAL_COOKIES=false
PORTAL_COOKIE_PATH=/app/data/portal_cookies.txt

# Optional email alert settings
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM=
EOF
```

The Compose file stores the SQLite database and runtime data in the named Docker volume `dorm-electricity-data`, so app data survives container rebuilds and updates.

### 5. Build and start

```bash
docker compose up -d --build
docker compose ps
```

Open `http://<server-ip>:8000` in a browser. If the server firewall is enabled, allow the port:

```bash
sudo ufw allow 8000/tcp
```

### 6. Logs and operations

```bash
docker compose logs -f dorm-electricity
docker compose restart dorm-electricity
docker compose down
```

`docker compose down` stops and removes the container and network, but it does not remove the named data volume. To remove persisted readings too, explicitly remove the volume:

```bash
docker volume rm dorm-electricity-data
```

### 7. Update from GitHub

```bash
git pull --ff-only
docker compose up -d --build
docker compose logs --tail=100 dorm-electricity
```

The existing `dorm-electricity-data` volume is reused after the rebuild.

## Troubleshooting

- `docker compose ps` shows the container restarting: run `docker compose logs -f dorm-electricity` and check environment values.
- Browser cannot reach the app: confirm `docker compose ps` maps `0.0.0.0:8000->8000/tcp`, then check firewall and cloud security group rules.
- Login page or electricity query fails: verify the server can reach the WebVPN URLs configured in `CAMPUS_LOGIN_URL` and `CAMPUS_ELECTRICITY_URL`.
- Email alerts do not send: set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and `SMTP_FROM`, then restart with `docker compose up -d`.
