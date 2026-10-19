# coroot

Ресурс `Coroot` (Community Edition) для кластера `joinrpg`: по нему оператор из [`platform/coroot-operator`](../coroot-operator/README.md) разворачивает сервер Coroot, ClickHouse, агентов на нодах и cluster-agent.

Свой шаблон, без зависимостей: ресурс — в [`templates/coroot.yaml`](templates/coroot.yaml), весь `spec` — в [`values.yaml`](values.yaml) под ключом `spec:` (имена полей — как в CRD `coroots.coroot.com` оператора 1.10.6, `kubectl explain coroot.spec`). **Отклонение от соглашения репозитория:** нет `Chart.lock` и `charts/` — зависимостей нет, `helm dependency build` не нужен.

| | |
|---|---|
| Релиз | `coroot` |
| Namespace | `coroot` (тот же, что у оператора; в другой namespace шаблон не рендерится) |
| Ресурс | `Coroot` `coroot` |
| Проект в Coroot | `coroot` |
| Сервер | StatefulSet `coroot-coroot`, 1 под, Service `coroot-coroot` (http 8080, grpc 4317) |
| ClickHouse | StatefulSet `coroot-clickhouse-shard-0` (1 шард, 1 реплика) и `coroot-clickhouse-keeper` (1 реплика) |
| Агенты | DaemonSet `coroot-node-agent` (по поду на ноде), Deployment `coroot-cluster-agent` |
| Prometheus | нет — метрики в ClickHouse (`storeMetricsInClickhouse: true`) |
| PVC (`universal.ru-6`, `Retain`) | `data-coroot-coroot-0` 5Gi, `data-coroot-clickhouse-shard-0-0` 20Gi, `data-coroot-clickhouse-keeper-0` 2Gi |
| Хранение | метрики 7d, трейсы 3d, логи 3d, профили 3d |
| Размещение | сервер, ClickHouse, Keeper — только зона `ru-6a`; сервер и ClickHouse по возможности не на одной ноде с `joinrpg-portal` (`joinrpg-prod`) |

Версии образов (закреплены в [`values.yaml`](values.yaml)):

| Компонент | Образ |
|---|---|
| Сервер | `ghcr.io/coroot/coroot:1.27.4` |
| node-agent | `ghcr.io/coroot/coroot-node-agent:1.36.4` |
| cluster-agent | `ghcr.io/coroot/coroot-cluster-agent:1.11.6` |
| ClickHouse, Keeper | зашиты в оператор (меняются вместе с ним) |

## Secret `coroot-admin`

Пароль пользователя `admin` для первого запуска — в Secret `coroot/coroot-admin`, ключ `password`. Создаётся вручную до установки, в репозитории пароля нет.

Создать или заменить (bash, WSL; пароль вводится без эха, не попадает в историю и в аргументы процесса):

```bash
read -rsp 'Пароль admin Coroot: ' PW; echo
printf '%s' "$PW" | kubectl create secret generic coroot-admin -n coroot \
  --from-file=password=/dev/stdin \
  --dry-run=client -o yaml | kubectl apply -f -
unset PW
```

Это начальный пароль (переменная `AUTH_BOOTSTRAP_ADMIN_PASSWORD` сервера): дальше пароль меняют в интерфейсе Coroot. Без Secret ресурс получает ошибку `Failed to get Admin Password` в `status.errors`, но сервер всё равно запускается — без начального пароля, и пароль `admin` задаёт первый, кто откроет интерфейс. Поэтому Secret — строго до установки.

Secret `coroot-project-key` (API-ключ проекта `coroot`, которым агенты отправляют данные) создаёт оператор сам, руками его не создавать.

## Установка и обновление

Порядок: сначала `platform/coroot-operator` (оператор и CRD), потом этот чарт. Из каталога чарта, `KUBECONFIG` указывает на кластер `joinrpg`:

```bash
cd platform/coroot

helm lint . -n coroot
helm template coroot . -n coroot > /tmp/coroot.yaml   # посмотреть ресурс Coroot
grep -n '\$' /tmp/coroot.yaml                         # должно быть пусто
grep -n 'name: ghcr' /tmp/coroot.yaml                 # три закреплённых образа
helm upgrade --install coroot . -n coroot --dry-run=server

helm upgrade --install coroot . -n coroot
```

## Проверка

```bash
kubectl get coroot coroot -n coroot -o jsonpath='{.status}'; echo   # status OK, без errors
kubectl get pods -n coroot -o wide                                   # все Running; сервер, ClickHouse, Keeper — на нодах ru-6a
kubectl get ds coroot-node-agent -n coroot                           # DESIRED = READY = числу нод
kubectl get pvc -n coroot                                            # 3 PVC Bound, universal.ru-6
kubectl get pods -n coroot -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[*].image}{"\n"}{end}'
kubectl auth can-i list secrets -A --as=system:serviceaccount:coroot:coroot-cluster-agent   # no
kubectl top pods -n coroot
```

## Доступ к интерфейсу

Ingress не создаётся, доступ — через port-forward:

```bash
kubectl port-forward -n coroot svc/coroot-coroot 8080:8080
```

Открыть <http://localhost:8080>, логин `admin`, пароль — из Secret `coroot-admin`.

Если port-forward запущен в WSL, а браузер в Windows не открывает страницу — выполнить port-forward из PowerShell (kubectl для Windows с тем же kubeconfig).

## Удаление

```bash
helm uninstall coroot -n coroot
kubectl get coroots -n coroot                                      # пусто
kubectl get clusterrole,clusterrolebinding | grep cluster-agent    # пусто (их удаляет оператор)
```

После удаления остаются:

- PVC с `reclaimPolicy: Retain` — данные Coroot и ClickHouse. Повторная установка подхватит их по тем же именам. Удалить: `kubectl delete pvc -n coroot <имя>` — вместе с PVC удаляется и диск. PVC ClickHouse и Keeper удалять или оставлять **только вместе**: без метаданных в Keeper реплицируемые таблицы ClickHouse становятся read-only;
- Secret `coroot-project-key` (оператор создаёт его без владельца) и `coroot-admin` (создан вручную).

## Что нужно знать

- **Закреплять `image.name` у сервера, node-agent и cluster-agent обязательно.** Без него оператор при старте и раз в час переводит компонент на самый новый тег из ghcr.io/coroot.
- **Символ доллара в `spec` запрещён** (в любой строке, включая будущие правила и шаблоны уведомлений): Coroot прогоняет конфиг через `os.ExpandEnv`, и `$имя` заменится значением переменной окружения или пустой строкой. Проверка — `grep '\$'` по рендеру (раздел «Установка»).
- `reclaimPolicy: Retain` в ресурсе `Coroot` — это **PVC без ownerReference**: PVC не удаляется вместе с ресурсом. У StorageClass `universal.ru-6` `reclaimPolicy: Delete` — удаление PVC удаляет диск.
- Все диски — в зоне `ru-6a` (туда же привязаны поды). Диск Cinder не переезжает между зонами: если в `ru-6a` не останется нод, сервер и ClickHouse не запустятся.
- Образы ClickHouse и Keeper зашиты в оператор: **обновление оператора может перезапустить ClickHouse**.
- **Версию сервера Coroot не откатывать** `helm rollback` без восстановления PVC `data-coroot-coroot-0`: новая версия может перенести схему своей базы (SQLite с пользователями и настройками из интерфейса), и старая на ней не запустится. Резервной копии этой базы нет — основная конфигурация в ресурсе `Coroot`.
- Secret `craas-auth` в namespace `coroot` (и во всех остальных) создаёт Селектел (`mk-registry-adm`) — pull-secret реестра; Coroot его не использует, не трогать.
- Токены, пароли и ключи в [`values.yaml`](values.yaml) не класть — только ссылки на Secret (`…Secret: {name, key}`) в namespace `coroot`.
