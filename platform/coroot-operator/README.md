# coroot-operator

Оператор [Coroot](https://docs.coroot.com/) для кластера `joinrpg`: ставит CRD `coroots.coroot.com` и по ресурсу `Coroot` разворачивает компоненты Coroot. Сам ресурс `Coroot` описан в отдельном чарте `platform/coroot` (следующий шаг).

Обёртка над официальным чартом [`coroot/coroot-operator`](https://github.com/coroot/helm-charts/tree/main/charts/coroot-operator), версия зависимости — в [`Chart.yaml`](Chart.yaml), настройки — в [`values.yaml`](values.yaml) под ключом `coroot-operator:`.

| | |
|---|---|
| Релиз | `coroot-operator` |
| Namespace | `coroot` (создаётся вручную, см. ниже) |
| Вид | Deployment `coroot-operator`, один под |
| ServiceAccount | `coroot-operator` |
| CRD | `coroots.coroot.com` (в `templates/` подчарта, управляется релизом) |
| Доступ к Secret | только в namespace `coroot` (`rbac.denyGlobalSecrets: true`) |

## Namespace

Namespace создаётся заранее и вручную, не через `--create-namespace`: компонентам Coroot (агент на нодах) нужен уровень Pod Security `privileged`.

```bash
kubectl create namespace coroot --dry-run=client -o yaml | kubectl apply -f -
kubectl label namespace coroot pod-security.kubernetes.io/enforce=privileged --overwrite
```

Команды можно повторять: если namespace и метка уже есть, ничего не изменится.

## Установка и обновление

Порядок: сначала этот чарт (оператор и CRD), потом `platform/coroot` (ресурс `Coroot`).

Из каталога чарта, `KUBECONFIG` указывает на кластер `joinrpg`:

```bash
cd platform/coroot-operator
helm repo add coroot https://coroot.github.io/helm-charts   # один раз
helm dependency build                                       # подчарт по Chart.lock

helm lint .
helm template coroot-operator . -n coroot > /tmp/coroot-operator.yaml  # посмотреть, что будет создано
helm upgrade --install coroot-operator . -n coroot --dry-run=server

helm upgrade --install coroot-operator . -n coroot
```

## Проверка

```bash
kubectl get crd coroots.coroot.com
kubectl get deploy,pods -n coroot -o wide                  # coroot-operator Running
kubectl logs -n coroot deploy/coroot-operator --tail=30    # без ошибок RBAC / forbidden

# Прямое чтение Secret через API — только в своём namespace (про косвенный доступ — «Что нужно знать»)
kubectl auth can-i list secrets -n joinrpg-prod --as=system:serviceaccount:coroot:coroot-operator  # no
kubectl auth can-i get secrets -n coroot --as=system:serviceaccount:coroot:coroot-operator         # yes
```

## Удаление

Сначала релиз `coroot` (ресурс `Coroot`), потом `coroot-operator`. Между ними — убедиться, что оператор успел убрать за ресурсом `Coroot` кластерные объекты: ClusterRole и ClusterRoleBinding cluster-agent удаляет сам оператор, сборщик мусора Kubernetes их не тронет (у кластерного объекта не может быть владельца из namespace).

```bash
helm uninstall coroot -n coroot
kubectl get coroots -n coroot                                      # пусто
kubectl get clusterrole,clusterrolebinding | grep cluster-agent    # пусто
helm uninstall coroot-operator -n coroot
```

После этого остаются:

- namespace `coroot` (создан вручную) и Secret, созданные в нём вручную, — `kubectl delete namespace coroot`;
- PVC с `reclaimPolicy: Retain` в ресурсе `Coroot` — они переживают удаление ресурса (данные Coroot). Удаление PVC удаляет и диск: у StorageClass Селектела `reclaimPolicy: Delete`.

## Что нужно знать

- **CRD `coroots.coroot.com` лежит в `templates/` подчарта, а не в `crds/`.** Поэтому `helm uninstall coroot-operator` удаляет CRD, а вместе с ней каскадно — все ресурсы `Coroot` и всё, что оператор по ним создал (поды; PVC — если у них `reclaimPolicy: Delete`, по умолчанию). Пока ресурс `Coroot` существует, релиз оператора не удалять; порядок удаления — выше. Плюс такого расположения: CRD обновляется вместе с `helm upgrade`.
- Оператор и ресурс `Coroot` должны быть в одном namespace (`coroot`): при `denyGlobalSecrets` оператор читает и создаёт Secret только в namespace своего релиза.
- При старте и затем раз в час оператор проверяет новые версии `coroot`, `coroot-node-agent` и `coroot-cluster-agent` в ghcr.io/coroot и переводит компонент на самый новый тег, если в ресурсе `Coroot` у него не закреплён `image.name`. Закреплять `image.name` у всех трёх компонентов в ресурсе `Coroot` обязательно.
- Образы ClickHouse и Prometheus зашиты в оператор и меняются только при его обновлении — **обновление оператора может перезапустить ClickHouse**.
- Образ самого оператора берётся с тегом `appVersion` подчарта (сейчас 1.10.6); обновление оператора — смена версии зависимости в [`Chart.yaml`](Chart.yaml), `helm dependency update` и `helm upgrade`.
- `denyGlobalSecrets` убирает только прямое чтение Secret через API вне `coroot`, но не изолирует оператора: по ClusterRole он может создавать поды и ServiceAccount в любом namespace (под может смонтировать Secret своего namespace), менять и удалять namespace (в том числе снимать метки Pod Security). Учётка оператора по сути равна администратору кластера — это поведение официального чарта.
