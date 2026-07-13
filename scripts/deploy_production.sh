#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_DIR="/opt/clover-blog"
APP_USER="ubuntu"
VENV_DIR="${PROJECT_DIR}/.venv"
MANAGE_PY="${PROJECT_DIR}/白车轴草/manage.py"
LOCK_FILE="/var/lock/clover-blog-deploy.lock"
DEPLOY_SCRIPT_SOURCE="${PROJECT_DIR}/scripts/deploy_production.sh"
DEPLOY_SCRIPT_TARGET="/usr/local/sbin/clover-blog-deploy"
GITHUB_REMOTE_URL="github-clover-blog:saitoasukakuku/clover-blog.git"
GIT_FETCH_ATTEMPTS=3
GIT_FETCH_TIMEOUT_SECONDS=30
HTTP_CHECK_ATTEMPTS=15
WORKER_SERVICE_SOURCE="${PROJECT_DIR}/scripts/systemd/clover-blog-worker.service"
WORKER_SERVICE_TARGET="/etc/systemd/system/clover-blog-worker.service"
FORWARDED_PROTO_MAP_SOURCE="${PROJECT_DIR}/scripts/nginx_forwarded_proto_map.conf"
FORWARDED_PROTO_MAP_TARGET="/etc/nginx/conf.d/clover-blog-forwarded-proto.conf"

trap 'exit_code=$?; echo "部署失败：第 ${LINENO} 行退出，状态码 ${exit_code}。" >&2' ERR

if [[ "${EUID}" -ne 0 ]]; then
    echo "请使用 sudo clover-blog-deploy 运行部署。" >&2
    exit 1
fi

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
    echo "已有另一个部署任务正在运行。" >&2
    exit 1
fi

cd "${PROJECT_DIR}"

python_version="$("${VENV_DIR}/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if ! "${VENV_DIR}/bin/python" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    echo "Django 5.2 requires Python 3.10 or newer; current version: ${python_version}." >&2
    exit 1
fi

if ! sudo -u "${APP_USER}" git diff --quiet -- ||
   ! sudo -u "${APP_USER}" git diff --cached --quiet --; then
    echo "服务器存在未提交的受跟踪文件改动，已停止部署。" >&2
    exit 1
fi

current_commit="$(sudo -u "${APP_USER}" git rev-parse HEAD)"
echo "当前提交：${current_commit}"
echo "正在从 GitHub 获取 origin/main..."

sudo -u "${APP_USER}" git remote set-url origin "${GITHUB_REMOTE_URL}"

fetch_succeeded=false
for fetch_attempt in $(seq 1 "${GIT_FETCH_ATTEMPTS}"); do
    if timeout "${GIT_FETCH_TIMEOUT_SECONDS}" \
        sudo -u "${APP_USER}" git \
        fetch --prune origin main; then
        fetch_succeeded=true
        break
    fi

    echo "第 ${fetch_attempt} 次获取失败。"
    if [[ "${fetch_attempt}" -lt "${GIT_FETCH_ATTEMPTS}" ]]; then
        sleep 5
    fi
done

if [[ "${fetch_succeeded}" != "true" ]]; then
    echo "GitHub 获取连续失败，当前线上版本未改变。" >&2
    exit 1
fi

target_commit="$(sudo -u "${APP_USER}" git rev-parse origin/main)"
echo "目标提交：${target_commit}"

if ! sudo -u "${APP_USER}" git merge-base --is-ancestor \
    "${current_commit}" "${target_commit}"; then
    echo "origin/main 不能从当前提交快进，已停止部署。" >&2
    exit 1
fi

sudo -u "${APP_USER}" git merge --ff-only origin/main

if [[ -f "${DEPLOY_SCRIPT_SOURCE}" ]]; then
    install -o root -g root -m 755 \
        "${DEPLOY_SCRIPT_SOURCE}" "${DEPLOY_SCRIPT_TARGET}"
fi

echo "正在同步 Python 依赖..."
sudo -u "${APP_USER}" "${VENV_DIR}/bin/python" -m pip install \
    -r "${PROJECT_DIR}/requirements.txt"

echo "正在校验依赖一致性..."
sudo -u "${APP_USER}" "${VENV_DIR}/bin/python" -m pip check

echo "正在执行 Django 生产安全检查..."
sudo -u "${APP_USER}" "${VENV_DIR}/bin/python" "${MANAGE_PY}" \
    check --deploy --fail-level WARNING

echo "正在执行数据库迁移..."
sudo -u "${APP_USER}" "${VENV_DIR}/bin/python" "${MANAGE_PY}" migrate

echo "正在清理过期运行状态..."
sudo -u "${APP_USER}" "${VENV_DIR}/bin/python" "${MANAGE_PY}" \
    cleanup_site_state

echo "正在迁移受保护媒体文件..."
sudo -u "${APP_USER}" "${VENV_DIR}/bin/python" "${MANAGE_PY}" \
    migrate_private_media

echo "正在收集静态资源..."
sudo -u "${APP_USER}" "${VENV_DIR}/bin/python" "${MANAGE_PY}" \
    collectstatic --noinput

if [[ -f "${WORKER_SERVICE_SOURCE}" ]]; then
    install -d -o "${APP_USER}" -g "${APP_USER}" -m 750 \
        "${PROJECT_DIR}/白车轴草/media" \
        "${PROJECT_DIR}/protected_media"
    install -o root -g root -m 644 \
        "${WORKER_SERVICE_SOURCE}" "${WORKER_SERVICE_TARGET}"
    systemctl daemon-reload
    systemctl enable clover-blog-worker.service
fi

echo "正在加入音乐播放版生成任务..."
sudo -u "${APP_USER}" "${VENV_DIR}/bin/python" "${MANAGE_PY}" \
    enqueue_site_task prepare_music_playback

echo "正在同步 Nginx 可信代理协议映射..."
install -o root -g root -m 644 \
    "${FORWARDED_PROTO_MAP_SOURCE}" "${FORWARDED_PROTO_MAP_TARGET}"

echo "正在检查 Nginx 配置..."
nginx -t
nginx_configuration="$(nginx -T 2>&1)"
if [[ "${nginx_configuration}" != *"location ^~ /media/covers/"* ]] ||
   [[ "${nginx_configuration}" != *"location ^~ /media/post_images/"* ]]; then
    echo "Nginx 尚未加载受保护媒体规则；请在站点 server 块中 include scripts/nginx_protected_media.conf。" >&2
    exit 1
fi
if [[ "${nginx_configuration}" != *"proxy_set_header X-Forwarded-Proto \$clover_forwarded_proto;"* ]]; then
    echo "Nginx 尚未使用可信隧道协议映射；请将 X-Forwarded-Proto 设为 \$clover_forwarded_proto。" >&2
    exit 1
fi

echo "正在重启应用服务..."
systemctl restart clover-blog
systemctl restart clover-blog-worker
systemctl reload nginx

systemctl is-active --quiet clover-blog
systemctl is-active --quiet clover-blog-worker
systemctl is-active --quiet nginx
systemctl is-active --quiet mysql
if systemctl cat cloudflared-quick-tunnel.service >/dev/null 2>&1; then
    systemctl is-active --quiet cloudflared-quick-tunnel.service
fi

http_check_succeeded=false
for http_check_attempt in $(seq 1 "${HTTP_CHECK_ATTEMPTS}"); do
    http_status_code=""
    if http_status_code="$(curl --silent --show-error --output /dev/null \
        --connect-timeout 3 \
        --max-time 10 \
        --write-out "%{http_code}" \
        --header "Host: 111.230.11.5" \
        --header "X-Forwarded-Proto: https" \
        "http://127.0.0.1/health/")" &&
       [[ "${http_status_code}" == "200" ]]; then
        http_check_succeeded=true
        break
    fi

    echo "第 ${http_check_attempt} 次健康检查返回 HTTP ${http_status_code:-request-error}。"
    sleep 2
done

if [[ "${http_check_succeeded}" != "true" ]]; then
    echo "应用重启后，本机首页健康检查失败。" >&2
    exit 1
fi

deployed_commit="$(sudo -u "${APP_USER}" git rev-parse HEAD)"
echo "部署成功：${deployed_commit}"
