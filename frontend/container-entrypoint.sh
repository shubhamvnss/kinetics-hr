#!/bin/sh
set -eu

if [ -n "${BACKEND_PROXY_URL:-}" ]; then
  sed "s|http://backend:8000|${BACKEND_PROXY_URL%/}|g" \
    /etc/nginx/kinetic.local.conf > /etc/nginx/conf.d/default.conf
else
  cp /etc/nginx/kinetic.azure.conf /etc/nginx/conf.d/default.conf
fi

exec /docker-entrypoint.sh nginx -g "daemon off;"
