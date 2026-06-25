#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_DIR="/opt/clover-blog"
APP_USER="ubuntu"
VENV_DIR="${PROJECT_DIR}/.venv"
MANAGE_PY="${PROJECT_DIR}/白车轴草/manage.py"
LOCK_FILE="/var/lock/clover-blog-deploy.lock"
DEPLOY_SCRIPT_SOURCE="${PROJECT_DIR}/scripts/deploy_production.sh"
DEPLOY_SCRIPT_TARGET="/usr/local/sbin/clover-blog-deploy"
GIT_FETCH_ATTEMPTS=5
HTTP_CHECK_ATTEMPTS=15

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

if ! sudo -u "${APP_USER}" git diff --quiet -- ||
   ! sudo -u "${APP_USER}" git diff --cached --quiet --; then
    echo "服务器存在未提交的受跟踪文件改动，已停止部署。" >&2
    exit 1
fi

current_commit="$(sudo -u "${APP_USER}" git rev-parse HEAD)"
echo "当前提交：${current_commit}"
echo "正在从 GitHub 获取 origin/main..."

fetch_succeeded=false
for fetch_attempt in $(seq 1 "${GIT_FETCH_ATTEMPTS}"); do
    if sudo -u "${APP_USER}" git \
        -c http.version=HTTP/1.1 \
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

echo "正在执行数据库迁移..."
sudo -u "${APP_USER}" "${VENV_DIR}/bin/python" "${MANAGE_PY}" migrate

echo "正在收集静态资源..."
sudo -u "${APP_USER}" "${VENV_DIR}/bin/python" "${MANAGE_PY}" \
    collectstatic --noinput

echo "正在执行 Django 部署检查..."
sudo -u "${APP_USER}" "${VENV_DIR}/bin/python" "${MANAGE_PY}" check --deploy

echo "正在检查 Nginx 配置..."
nginx -t

echo "正在重启应用服务..."
systemctl restart clover-blog
systemctl reload nginx

systemctl is-active --quiet clover-blog
systemctl is-active --quiet nginx
systemctl is-active --quiet mysql
systemctl is-active --quiet cloudflared-quick-tunnel.service

http_check_succeeded=false
for http_check_attempt in $(seq 1 "${HTTP_CHECK_ATTEMPTS}"); do
    if curl --fail --silent --show-error --output /dev/null \
        "http://127.0.0.1/index/"; then
        http_check_succeeded=true
        break
    fi

    sleep 2
done

if [[ "${http_check_succeeded}" != "true" ]]; then
    echo "应用重启后，本机首页健康检查失败。" >&2
    exit 1
fi

deployed_commit="$(sudo -u "${APP_USER}" git rev-parse HEAD)"
echo "部署成功：${deployed_commit}"
