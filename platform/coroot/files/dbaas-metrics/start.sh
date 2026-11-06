#!/bin/sh
set -eu
umask 077

# Разрешены только символы Bearer-токена: кавычки/переводы строк не попадут в nginx.conf.
case "${SELECTEL_DBAAS_METRICS_TOKEN:-}" in
  ''|*[!A-Za-z0-9._~+/=-]*)
    echo 'Некорректный или пустой токен метрик DBaaS' >&2
    exit 1
    ;;
esac

DNS_RESOLVER=$(awk '/^nameserver / { print $2; exit }' /etc/resolv.conf)
if [ -z "$DNS_RESOLVER" ]; then
  echo 'Не найден DNS resolver' >&2
  exit 1
fi
export DNS_RESOLVER

# Подставляем только эти две переменные; переменные самого nginx сохраняются.
envsubst '${SELECTEL_DBAAS_METRICS_TOKEN} ${DNS_RESOLVER}' \
  < /config/nginx.conf.template > /tmp/nginx.conf
unset SELECTEL_DBAAS_METRICS_TOKEN
exec nginx -c /tmp/nginx.conf -g 'daemon off;'
