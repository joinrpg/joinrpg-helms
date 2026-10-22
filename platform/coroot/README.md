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
| Адрес интерфейса | <https://coroot.joinrpg.ru> — HTTPRoute `coroot` на Gateway `infra/main` (раздел «Доступ к интерфейсу») |
| ClickHouse | StatefulSet `coroot-clickhouse-shard-0` (1 шард, 1 реплика) и `coroot-clickhouse-keeper` (1 реплика) |
| Агенты | DaemonSet `coroot-node-agent` (по поду на ноде), Deployment `coroot-cluster-agent` |
| Prometheus | нет — метрики в ClickHouse (`storeMetricsInClickhouse: true`) |
| PVC (`fast2.ru-6`, `Retain`) | `data-coroot-coroot-0` 5Gi, `data-coroot-clickhouse-shard-0-0` 20Gi, `data-coroot-clickhouse-keeper-0` 2Gi |
| Хранение | метрики 30d, трейсы 7d, логи 7d, профили 3d (~400 MiB/сутки, при этих сроках ~4–8 GiB) |
| Алерты | встроенные правила Coroot (у `kubernetes-events` `minCount: 3` — без шума от разовых событий на раскатке; `new-log-patterns`, `network-tcp-connections`, `network-connectivity`, `memory-leak` — только для категории `application`) + свои `certificate-expiry`, `certificate-not-ready`, `node-cpu-requests`, `cluster-cpu-requests` (`spec.projects[].alertingRules`) |
| Категории | `control-plane` дополнена шаблоном `_/*` (процессы хоста); `dev` — без уведомлений |
| Категория `dev` | `kogda-dev/*`, `joinrpg-dev/*` — только в интерфейсе, без уведомлений |
| SLO latency | prod `joinrpg-portal`, `joinrpg-idportal` — 95% быстрее 1 с (длинные соединения Blazor) |
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
grep -n '\$' /tmp/coroot.yaml                         # ровно одна строка: TELEGRAM_RELAY_KEY
grep -n 'name: ghcr' /tmp/coroot.yaml                 # три закреплённых образа
helm upgrade --install coroot . -n coroot --dry-run=server

helm upgrade --install coroot . -n coroot
```

## Проверка

```bash
kubectl get coroot coroot -n coroot -o jsonpath='{.status}'; echo   # status OK, без errors
kubectl get pods -n coroot -o wide                                   # все Running; сервер, ClickHouse, Keeper — на нодах ru-6a
kubectl get ds coroot-node-agent -n coroot                           # DESIRED = READY = числу нод
kubectl get pvc -n coroot                                            # 3 PVC Bound, fast2.ru-6
kubectl get pods -n coroot -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[*].image}{"\n"}{end}'
kubectl auth can-i list secrets -A --as=system:serviceaccount:coroot:coroot-cluster-agent   # no
kubectl top pods -n coroot
```

## Уведомления в Telegram

Webhook в `spec.projects[].notificationIntegrations.webhook` шлёт `{"text": "…"}` на реле — Cloudflare Worker `coroot-tg-relay` ([`telegram-relay/`](telegram-relay/README.md)), а реле — в `sendMessage` бота `joinrpg_alert_bot`. Флаги `alerts`, `incidents`, `deployments` в `values.yaml`.

Почему реле: из кластера `api.telegram.org` недоступен (таймаут), а Telegram принимает токен только в URL — Coroot при ошибке отправки пишет URL в лог целиком, с токеном. Токен бота и chat_id хранятся только в секретах воркера; в кластере — лишь ключ реле.

- Ключ реле — из Secret `coroot-telegram` (ключ `relay-key`) через `spec.env` сервера (`optional: true` — без Secret под стартует); в конфиг попадает как `${TELEGRAM_RELAY_KEY}` в заголовке `X-Relay-Key` (Coroot прогоняет конфиг через `os.ExpandEnv`). Других символов доллара в `values.yaml` быть не должно.
- Категориям без явного `webhook` в `notificationSettings` Coroot включает уведомления сам; поэтому у `dev` (`kogda-dev/*`, `joinrpg-dev/*`) `webhook: {enabled: false}` задан явно для всех трёх видов. О деплоях Coroot уведомляет только по категории по умолчанию `application`.
- Шаблоны собирают текст через `printf` и экранируют функцией `json` — кавычки и переводы строк из сообщений логов не ломают JSON; без `parse_mode` (Markdown падал бы на `_` и `*` из логов).

Secret создаётся вручную тем же ключом, что `RELAY_KEY` воркера; затем сервер перезапускается, чтобы прочитать env:

```bash
read -rs RELAY_KEY; echo
kubectl create secret generic coroot-telegram -n coroot --from-literal=relay-key="$RELAY_KEY"
unset RELAY_KEY
kubectl rollout restart statefulset coroot-coroot -n coroot
```

Проверка доставки из кластера (под удаляется сам):

```bash
read -rs RELAY_KEY; echo
kubectl run relaytest -n coroot --rm -i --restart=Never --image=curlimages/curl:8.10.1 --env="K=$RELAY_KEY" -- \
  sh -c 'curl -s -m 15 -X POST -H "X-Relay-Key: $K" -H "Content-Type: application/json" -d "{\"text\":\"Проверка из кластера\"}" https://coroot-tg-relay.joinrpg.workers.dev/sendMessage; echo'
unset RELAY_KEY
```

Смена токена бота — только в воркере (`telegram-relay/README.md`), кластер не трогается.

## Метрики cert-manager

Правила `certificate-expiry` (меньше 21 дня до истечения — продление за 30 дней не удалось) и `certificate-not-ready` работают на метриках контроллера cert-manager (порт 9402). cluster-agent Coroot собирает метрики только с подов с аннотациями `coroot.com/*` (`prometheus.io/*` не читает), поэтому у пода контроллера должны быть:

```yaml
coroot.com/scrape-metrics: "true"
coroot.com/metrics-port: "9402"
```

Аннотации заданы в `podAnnotations` чарта [`platform/cert-manager`](../cert-manager/values.yaml). Если их убрать, правила по сертификатам замолчат без ошибок: правило на отсутствующую метрику ничего не находит.

## Смена сроков хранения

`metricsTTL`, `tracesTTL`, `logsTTL`, `profilesTTL` в `values.yaml` действуют только на **новые** таблицы: Coroot создаёт их через `CREATE TABLE IF NOT EXISTS ... TTL`, существующие не меняет. После `helm upgrade` сроки уже созданных таблиц меняются вручную, в секундах (1d = 86400). Пример — сроки traces/logs 7d, metrics 30d; таблицы профилей (`profiling_*`) — по тому же образцу:

```bash
kubectl exec -i -n coroot coroot-clickhouse-shard-0-0 -c clickhouse-server -- \
  clickhouse-client -d coroot_0r4fqpy1 --multiquery --materialize_ttl_after_modify=0 <<'EOF'
ALTER TABLE metrics MODIFY TTL toDateTime(Timestamp) + toIntervalSecond(2592000);
ALTER TABLE otel_traces MODIFY TTL toDateTime(Timestamp) + toIntervalSecond(604800);
ALTER TABLE otel_traces_histogram MODIFY TTL Timestamp + toIntervalSecond(604800);
ALTER TABLE otel_traces_trace_id_ts MODIFY TTL toDateTime(Start) + toIntervalSecond(604800);
ALTER TABLE otel_traces_service_name MODIFY TTL toDateTime(LastSeen) + toIntervalSecond(604800);
ALTER TABLE otel_logs MODIFY TTL toDateTime(Timestamp) + toIntervalSecond(604800);
ALTER TABLE otel_logs_service_name_severity_text MODIFY TTL toDateTime(LastSeen) + toIntervalSecond(604800);
EOF

# проверка: интервал у каждой таблицы
kubectl exec -n coroot coroot-clickhouse-shard-0-0 -c clickhouse-server -- clickhouse-client -d coroot_0r4fqpy1 -q "select name, extract(create_table_query, 'toInterval[A-Za-z]+\\(\\d+\\)') ttl from system.tables where database = 'coroot_0r4fqpy1' and engine like '%MergeTree' order by name"
```

- `coroot_0r4fqpy1` — база проекта `coroot` (имя создаёт Coroot); выражения TTL — как в `ch/client.go` coroot 1.27.4 (у `otel_traces_histogram` — без `toDateTime`).
- `materialize_ttl_after_modify=0` — без перезаписи старых кусков данных: при увеличении срока удалять нечего, при уменьшении старые данные уйдут при следующих слияниях.

## Доступ к интерфейсу

Публичный адрес — <https://coroot.joinrpg.ru>, открыт всем (метрики смотрят и с мобильного интернета), защита — только пароль. Поэтому пароль `admin` после установки сразу меняется в интерфейсе на длинный, а начальный из Secret `coroot-admin` больше не действует.

Как устроено:

- HTTPRoute `coroot` (шаблон [`templates/httproute.yaml`](templates/httproute.yaml), блок `route` в [`values.yaml`](values.yaml)) цепляется к общему Gateway `infra/main` и ведёт на Service `coroot-coroot:8080`;
- на Gateway `main` — listener `coroot.joinrpg.ru` (HTTPS, порт 8443, Secret `coroot-joinrpg-tls`). Gateway в репозиториях не описан, listener добавляется вручную (ниже);
- сертификат: по аннотации `cert-manager.io/cluster-issuer: letsencrypt-prod` на Gateway cert-manager сам создаёт `Certificate` `infra/coroot-joinrpg-tls` и продлевает его (Let's Encrypt, HTTP-01 через listener `http`);
- редирект http → https — общий HTTPRoute `infra/https-redirect`;
- маршруты принимаются только из namespace с меткой `gateway-access=shared` — она стоит на `coroot` вручную;
- запись DNS `coroot.joinrpg.ru` — в `joinrpg-infrastructure`, слой `05-dns`.

Listener и метка (один раз, до `helm upgrade` с маршрутом). Listener добавляется JSON-патчем в конец списка: `kubectl apply` файлом Gateway не делать — список listener'ов заменится целиком, и пропадут добавленные позже руками.

```bash
kubectl label ns coroot gateway-access=shared

PATCH='[{"op":"add","path":"/spec/listeners/-","value":{"name":"coroot.joinrpg.ru","hostname":"coroot.joinrpg.ru","port":8443,"protocol":"HTTPS","tls":{"mode":"Terminate","certificateRefs":[{"kind":"Secret","name":"coroot-joinrpg-tls"}]},"allowedRoutes":{"namespaces":{"from":"Selector","selector":{"matchLabels":{"gateway-access":"shared"}}}}}}]'
kubectl patch gateway main -n infra --type=json -p "$PATCH" --dry-run=server -o yaml | grep -A12 'name: coroot.joinrpg.ru'
kubectl patch gateway main -n infra --type=json -p "$PATCH"
```

Проверка:

```bash
kubectl get certificate -n infra coroot-joinrpg-tls        # READY True (1–2 минуты)
kubectl get httproute -n coroot coroot -o jsonpath='{.status.parents[0].conditions[*].reason}'; echo   # Accepted ResolvedRefs
curl -sI https://coroot.joinrpg.ru | head -1
```

Запасной доступ без Gateway — port-forward:

```bash
kubectl port-forward -n coroot svc/coroot-coroot 8080:8080
```

Открыть <http://localhost:8080>. Если port-forward запущен в WSL, а браузер в Windows не открывает страницу — выполнить port-forward из PowerShell (kubectl для Windows с тем же kubeconfig).

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
- `reclaimPolicy: Retain` в ресурсе `Coroot` — это **PVC без ownerReference**: PVC не удаляется вместе с ресурсом. У StorageClass `fast2.ru-6` `reclaimPolicy: Delete` — удаление PVC удаляет диск.
- **Смена `className` дисков** не применяется сама: у PVC и у `volumeClaimTemplates` StatefulSet (`coroot-coroot`, `coroot-clickhouse-shard-0`, `coroot-clickhouse-keeper`) эти поля неизменяемы. После `helm upgrade` удалить три StatefulSet и их PVC (данные пропадут) — оператор пересоздаст их по ресурсу `Coroot`.
- StorageClass `universal.ru-6` в кластере не работает: типа тома `universal.ru-6a` нет (есть `universal2.ru-6a`), диски не создаются. Поэтому — `fast2.ru-6`.
- Все диски — в зоне `ru-6a` (туда же привязаны поды). Диск Cinder не переезжает между зонами: если в `ru-6a` не останется нод, сервер и ClickHouse не запустятся.
- Образы ClickHouse и Keeper зашиты в оператор: **обновление оператора может перезапустить ClickHouse**.
- **Версию сервера Coroot не откатывать** `helm rollback` без восстановления PVC `data-coroot-coroot-0`: новая версия может перенести схему своей базы (SQLite с пользователями и настройками из интерфейса), и старая на ней не запустится. Резервной копии этой базы нет — основная конфигурация в ресурсе `Coroot`.
- Secret `craas-auth` в namespace `coroot` (и во всех остальных) создаёт Селектел (`mk-registry-adm`) — pull-secret реестра; Coroot его не использует, не трогать.
- Токены, пароли и ключи в [`values.yaml`](values.yaml) не класть — только ссылки на Secret (`…Secret: {name, key}`) в namespace `coroot`.
