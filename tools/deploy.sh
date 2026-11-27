#!/bin/bash
# Обновление прод-сервера Battle for the Galaxy (запуск из Git Bash на дев-ПК).
# Заливает текущий HEAD git на сервер, ставит зависимости, миграции, рестартит службы.
set -e
SERVER="root@93.183.104.131"
TMP="$(mktemp -d)/bfg.tar.gz"

echo "== архив HEAD =="
git archive HEAD --format=tar.gz --output="$TMP"

echo "== заливка =="
scp "$TMP" "$SERVER:/root/bfg.tar.gz"

echo "== распаковка, зависимости, миграции, рестарт =="
ssh "$SERVER" '
  set -e
  cd /opt/bfg
  cp .env /root/bfg.env.bak
  tar xzf /root/bfg.tar.gz -C /opt/bfg
  cp /root/bfg.env.bak .env && chmod 600 .env
  ./.venv/bin/pip install -q -r requirements.txt
  ./.venv/bin/python -m alembic upgrade head
  systemctl restart bfg-bot bfg-worker bfg-api
  sleep 5
  systemctl is-active bfg-bot bfg-worker bfg-api
  curl -s -m 5 http://127.0.0.1:8000/healthz
'
echo
echo "DEPLOY_OK"
