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
ENTERPRISE_WECHAT_ELECTRICITY_URL=http://wx.njucm.edu.cn/work/njucm/card.aspx?wid=37
SESSION_KEEP_ALIVE_INTERVAL_SECONDS=300
PERSIST_PORTAL_COOKIES=false
PORTAL_COOKIE_PATH=./data/portal_cookies.txt
ENTERPRISE_WECHAT_COOKIE_PATH=./data/enterprise_wechat_cookies.txt
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM=
```

SMTP settings are required only when email alerts are enabled.

`SESSION_KEEP_ALIVE_INTERVAL_SECONDS` controls the independent campus portal keep-alive interval and defaults to 300 seconds. `PERSIST_PORTAL_COOKIES` is disabled by default; if set to `true`, the app stores the campus portal session cookie file at `PORTAL_COOKIE_PATH` with best-effort `0600` permissions so a trusted single-user deployment can survive restarts. The cookie file is a login credential, so enable this only on trusted hosts.

`CAMPUS_LOGIN_URL` and `CAMPUS_ELECTRICITY_URL` default to the NJUCM WebVPN portal paths above. Override them only when the campus portal path changes or when deploying in an environment with a different reachable campus gateway.

`ENTERPRISE_WECHAT_ELECTRICITY_URL` defaults to the NJUCM enterprise WeChat card workbench entry. After opening the school workbench in an authorized Enterprise WeChat session, import the `wx.njucm.edu.cn` Cookie in the app. HAR analysis confirmed the electricity page at `/work/njucm/s_card_selfhelp_elect.aspx`; collection now queries `/work/njucm/card.ashx?action=selfhelp_elect_query` with the imported session. When both enterprise WeChat and the campus portal are authenticated, collection prefers enterprise WeChat and keeps the campus portal as fallback.

## Enterprise WeChat guide option

The dashboard includes an enterprise WeChat guide link at `/wechat/guide`.

1. Open the dashboard, copy the guide link, and send/open it in Windows Enterprise WeChat.
2. On the guide page, click "打开学校电费页" to launch the configured `ENTERPRISE_WECHAT_ELECTRICITY_URL`.
3. If the school page opens normally, use that authenticated session as the source for manual Cookie import or the helper tool.
4. If the guide opens in a system browser, or the school page shows `messageerror.aspx`, export a HAR from the browser/capture tool and run the local diagnostic helper.

The app cannot automatically read cookies from `wx.njucm.edu.cn` while serving `/wechat/guide`; browsers enforce same-origin isolation between the school domain and this app.

### Enterprise WeChat HAR import helper

For non-technical users, the shortest fallback is to export a HAR from the authenticated Enterprise WeChat traffic and import it locally. Keep the app running, then run:

```bash
python3 tools/import_wechat_har.py path/to/enterprise-wechat.har
```

The import helper uses only the Python standard library. It extracts the `ASP.NET_SessionId` cookie for `wx.njucm.edu.cn`, imports it through `/wechat/session/import`, and, when the HAR contains the electricity query request, saves the room selection through `/api/room-selection`. It does not print Cookie values.

If the app is running on a different local port, pass the base URL explicitly:

```bash
python3 tools/import_wechat_har.py path/to/enterprise-wechat.har --base-url http://127.0.0.1:8123
```

Use `--dry-run` to check whether the HAR contains the needed cookie and room fields without importing anything. Raw HAR files contain login credentials; do not paste them into chat or commit them. The repository ignores `*.har` by default.

### Enterprise WeChat cookie lifetime probe

If the HAR contains a reusable `ASP.NET_SessionId`, import it once and let the server keep it alive instead of asking users to capture traffic repeatedly. Enable cookie persistence on a trusted host:

```text
PERSIST_PORTAL_COOKIES=true
ENTERPRISE_WECHAT_COOKIE_PATH=./data/enterprise_wechat_cookies.txt
SESSION_KEEP_ALIVE_INTERVAL_SECONDS=300
```

To measure how long the captured cookie remains usable, run the probe helper. It reads the cookie and room fields from the HAR, verifies the session, then repeatedly performs the enterprise WeChat electricity query until it fails or is stopped. Cookie values are never printed.

```bash
python3 tools/probe_wechat_cookie_lifetime.py path/to/enterprise-wechat.har --interval 300
```

Run one check only:

```bash
python3 tools/probe_wechat_cookie_lifetime.py path/to/enterprise-wechat.har --once
```

If the HAR has no room query, provide the room explicitly or use session-page keep-alive only:

```bash
python3 tools/probe_wechat_cookie_lifetime.py path/to/enterprise-wechat.har --building C20 --room 2324
python3 tools/probe_wechat_cookie_lifetime.py path/to/enterprise-wechat.har --keep-alive-only --interval 300
```

The dashboard `/api/status` response includes enterprise WeChat `lastVerifiedAt`, `lastKeepAliveAt`, and `lastKeepAliveError` fields so server-side keep-alive health can be inspected without exposing Cookie values.

### Enterprise WeChat HAR diagnostic fallback

The diagnostic helper is offline and uses only the Python standard library. It prints a redacted report with matched enterprise WeChat paths, OAuth-like query key presence, Cookie/Set-Cookie names, User-Agent markers, candidate `.ashx`/`.aspx` endpoints, and likely electricity payload signals.

```bash
python3 tools/analyze_wechat_har.py path/to/enterprise-wechat.har
```

Review the generated report instead of sharing the raw HAR; it omits Cookie values, query values, OAuth codes, tokens, and response bodies.

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
ENTERPRISE_WECHAT_ELECTRICITY_URL=http://wx.njucm.edu.cn/work/njucm/card.aspx?wid=37

# Optional session continuity settings
SESSION_KEEP_ALIVE_INTERVAL_SECONDS=300
PERSIST_PORTAL_COOKIES=false
PORTAL_COOKIE_PATH=/app/data/portal_cookies.txt
ENTERPRISE_WECHAT_COOKIE_PATH=/app/data/enterprise_wechat_cookies.txt

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
