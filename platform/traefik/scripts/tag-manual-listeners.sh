#!/bin/bash
# Тег manual-тега на ручные слушатели балансировщика traefik (все, у кого нет тега контроллера).
# Зачем: openstack-cloud-controller-manager при EnsureLoadBalancer (любое изменение Service
# traefik/traefik) удаляет на своём балансировщике слушатели БЕЗ тегов. Слушатель с чужим тегом
# не трогает. Тег контроллера — имя балансировщика (kube_service_<кластер>_traefik_traefik).
# Без аргументов — только показать. С --apply — проставить тег (существующие теги сохраняются).
set -e
TAG=joindb-manual
source "$(dirname "$0")/keystone-token.sh"

LB_NAME=$(lb_get "loadbalancers/$LB_ID" | python3 -c "import json,sys;print(json.load(sys.stdin)['loadbalancer']['name'])")
PLAN=$(lb_get "listeners?loadbalancer_id=$LB_ID" | python3 -c "
import json, sys
tag, lb_name = sys.argv[1], sys.argv[2]
for l in json.load(sys.stdin)['listeners']:
    tags = l.get('tags') or []
    if lb_name in tags:
        continue                      # слушатель контроллера
    new = tags if tag in tags else tags + [tag]
    print(l['id'], l['name'], json.dumps(tags), json.dumps(new))
" "$TAG" "$LB_NAME")

echo "ручные слушатели (id имя теги_сейчас теги_после):"
echo "$PLAN"
[ -n "$PLAN" ] || { echo "ручных слушателей нет"; exit 0; }
[ "$1" = "--apply" ] || { echo "(показ; для изменения — --apply)"; exit 0; }

while read -r id name cur new; do
  [ "$cur" = "$new" ] && { echo "$name: тег уже есть"; continue; }
  curl -sf -X PUT -H "X-Auth-Token: $TOKEN" -H 'Content-Type: application/json' \
    -d "{\"listener\":{\"tags\":$new}}" "$LB_API/listeners/$id" \
    | python3 -c "import json,sys;l=json.load(sys.stdin)['listener'];print(l['name'],l['tags'],l['provisioning_status'])"
  # следующее изменение балансировщик примет только в ACTIVE
  for i in $(seq 1 30); do
    st=$(lb_get "loadbalancers/$LB_ID" | python3 -c "import json,sys;print(json.load(sys.stdin)['loadbalancer']['provisioning_status'])")
    [ "$st" = ACTIVE ] && break; sleep 2
  done
  echo "  балансировщик: $st"
done <<< "$PLAN"
