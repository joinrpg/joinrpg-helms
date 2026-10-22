#!/bin/bash
# Только чтение: снимок балансировщика traefik (слушатели, пулы, участники, health monitor)
# в docs/lb-snapshot-<суффикс>.json (docs/ в .gitignore — там адреса и allowed_cidrs) и сводка.
# Использование: platform/traefik/scripts/lb-snapshot.sh [суффикс, по умолчанию дата]
set -e
source "$(dirname "$0")/keystone-token.sh"
OUT="$REPO_ROOT/docs/lb-snapshot-${1:-$(date +%F)}.json"
mkdir -p "$REPO_ROOT/docs"

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
lb_get "loadbalancers/$LB_ID" > "$TMP/lb.json"
lb_get "listeners?loadbalancer_id=$LB_ID" > "$TMP/listeners.json"
lb_get "pools?loadbalancer_id=$LB_ID" > "$TMP/pools.json"
lb_get "healthmonitors" > "$TMP/monitors.json"
for p in $(python3 -c "import json,sys;print(' '.join(p['id'] for p in json.load(open(sys.argv[1]))['pools']))" "$TMP/pools.json"); do
  lb_get "pools/$p/members" > "$TMP/members-$p.json"
done

python3 - "$TMP" "$OUT" <<'EOF'
import json, sys, os
tmp, out = sys.argv[1], sys.argv[2]
load = lambda n: json.load(open(os.path.join(tmp, n)))
lb = load('lb.json')['loadbalancer']
ls = load('listeners.json')['listeners']
ps = load('pools.json')['pools']
hm = {m['id']: m for m in load('monitors.json')['healthmonitors']}
for p in ps:
    p['members_full'] = load(f"members-{p['id']}.json")['members']
    p['healthmonitor_full'] = hm.get(p.get('healthmonitor_id'))
json.dump({'loadbalancer': lb, 'listeners': ls, 'pools': ps}, open(out, 'w'), indent=2, ensure_ascii=False)
print('saved', out)
print('lb', lb['name'], lb['provisioning_status'], lb['operating_status'])
pools = {p['id']: p for p in ps}
for l in sorted(ls, key=lambda l: l['protocol_port']):
    p = pools.get(l.get('default_pool_id')) or {}
    members = [m['operating_status'] for m in p.get('members_full', [])]
    print(f"{l['protocol_port']:>5} {l['name'][:48]:48} {l['protocol']:4} pool={p.get('protocol', '-'):6} "
          f"members={members} tags={l.get('tags')} {l['provisioning_status']}/{l['operating_status']}")
EOF
