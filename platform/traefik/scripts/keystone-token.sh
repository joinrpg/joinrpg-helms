# Подключаемый файл (source): токен Keystone проекта в $TOKEN, id балансировщика traefik в $LB_ID,
# адрес Octavia API в $LB_API. Нужны secrets.env в корне репозитория и KUBECONFIG на кластер joinrpg.
REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
source <(tr -d '\r' < "$REPO_ROOT/secrets.env")   # tr — переводы строк Windows (G134)
export KUBECONFIG=${KUBECONFIG:-~/.kube/joinrpg.yaml}

LB_ID=$(kubectl get svc traefik -n traefik -o jsonpath='{.metadata.annotations.loadbalancer\.openstack\.org/load-balancer-id}')
[ -n "$LB_ID" ] || { echo "нет id балансировщика в аннотации Service traefik/traefik" >&2; exit 1; }
LB_API=https://ru-6.cloud.api.selcloud.ru/load-balancer/v2/lbaas

TOKEN=$(python3 - <<'EOF' | curl -si -H 'Content-Type: application/json' -d @- "$OS_AUTH_URL/auth/tokens" | awk -F': ' 'tolower($1)=="x-subject-token"{print $2}' | tr -d '\r'
import os, json
print(json.dumps({"auth":{"identity":{"methods":["password"],"password":{"user":{
 "name":os.environ["OS_USERNAME"],"domain":{"name":os.environ["OS_USER_DOMAIN_NAME"]},
 "password":os.environ["OS_PASSWORD"]}}},"scope":{"project":{"id":os.environ["OS_PROJECT_ID"]}}}}))
EOF
)
[ -n "$TOKEN" ] || { echo "токен Keystone пустой — проверь secrets.env" >&2; exit 1; }

lb_get() { curl -sf -H "X-Auth-Token: $TOKEN" "$LB_API/$1"; }
