# 宿舍电费监控

一个轻量级全栈 Web 应用，用于从校园自助购电系统监控宿舍电费余额。

## 运行环境

- Python 3.11+
- 初始实现不需要第三方 Python 依赖
- 面向 Ubuntu 服务器部署设计

## 本地快速启动

```bash
python -m backend.app.main
```

在浏览器中打开 `http://127.0.0.1:8000`。

## 环境变量

```text
APP_HOST=127.0.0.1
APP_PORT=8000
APP_TIMEZONE=Asia/Shanghai
DATA_DIR=./data
CAMPUS_LOGIN_URL=https://webvpn.njucm.edu.cn/http/webvpn34f6d2940beaaa8a549e2c772ae7c064/Default.aspx
CAMPUS_ELECTRICITY_URL=https://webvpn.njucm.edu.cn/http/webvpn34f6d2940beaaa8a549e2c772ae7c064/Web/Student/FeeElect.aspx
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM=
```

只有启用邮件提醒时才需要配置 SMTP 设置。

`CAMPUS_LOGIN_URL` 和 `CAMPUS_ELECTRICITY_URL` 默认使用上面的 NJUCM WebVPN 门户路径。仅在校园门户路径变化，或部署环境需要使用其他可访问的校园网关时覆盖它们。

## Ubuntu 上的 Docker 部署

以下步骤从一台全新的 Ubuntu 服务器开始，通过 Docker Compose 从 GitHub 部署项目。

### 1. 安装前置依赖

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

可选：允许当前用户在不使用 `sudo` 的情况下运行 Docker。

```bash
sudo usermod -aG docker "$USER"
newgrp docker
```

### 2. 克隆项目

```bash
git clone https://github.com/gy1041/nzyDormitory.git
cd nzyDormitory
```

### 3. 检查 Docker 文件

仓库包含：

- `Dockerfile`：构建 Python 3.11 镜像，并运行 `python -m backend.app.main`。
- `.dockerignore`：将本地数据、缓存、部署 `.env`、Trellis 元数据和 Git 元数据排除在镜像构建上下文之外。
- `docker-compose.yml`：在端口 `8000` 暴露应用，设置 WebVPN 校园 URL 默认值，并将持久化数据挂载到 `/app/data`。

### 4. 配置部署环境

仅在需要覆盖默认值或配置 SMTP 提醒设置时创建 `.env`。

```bash
cat > .env <<'EOF'
CAMPUS_LOGIN_URL=https://webvpn.njucm.edu.cn/http/webvpn34f6d2940beaaa8a549e2c772ae7c064/Default.aspx
CAMPUS_ELECTRICITY_URL=https://webvpn.njucm.edu.cn/http/webvpn34f6d2940beaaa8a549e2c772ae7c064/Web/Student/FeeElect.aspx

# 可选邮件提醒设置
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM=
EOF
```

Compose 文件会把 SQLite 数据库和运行时数据存储在名为 `dorm-electricity-data` 的 Docker volume 中，因此应用数据会在容器重建和更新后保留。

### 5. 构建并启动

```bash
docker compose up -d --build
docker compose ps
```

在浏览器中打开 `http://<server-ip>:8000`。如果服务器启用了防火墙，请放行该端口：

```bash
sudo ufw allow 8000/tcp
```

### 6. 日志和运维操作

```bash
docker compose logs -f dorm-electricity
docker compose restart dorm-electricity
docker compose down
```

`docker compose down` 会停止并删除容器和网络，但不会删除命名数据 volume。如需同时删除已持久化的读数，请显式删除该 volume：

```bash
docker volume rm dorm-electricity-data
```

### 7. 从 GitHub 更新

```bash
git pull --ff-only
docker compose up -d --build
docker compose logs --tail=100 dorm-electricity
```

重建后会继续复用现有的 `dorm-electricity-data` volume。

## 故障排查

- `docker compose ps` 显示容器正在反复重启：运行 `docker compose logs -f dorm-electricity` 并检查环境变量值。
- 浏览器无法访问应用：确认 `docker compose ps` 显示端口映射为 `0.0.0.0:8000->8000/tcp`，然后检查防火墙和云服务安全组规则。
- 登录页面或电费查询失败：确认服务器可以访问 `CAMPUS_LOGIN_URL` 和 `CAMPUS_ELECTRICITY_URL` 中配置的 WebVPN URL。
- 邮件提醒无法发送：设置 `SMTP_HOST`、`SMTP_PORT`、`SMTP_USERNAME`、`SMTP_PASSWORD` 和 `SMTP_FROM`，然后使用 `docker compose up -d` 重启。
