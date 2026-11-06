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
| Хранение | метрики 30d, трейсы 7d, логи 30d, профили 14d (по замерам 2026-11-06 прогноз занятого диска ~8–9 GiB; бюджет с запасом ~10–12 GiB из 20Gi) |
| Алерты | встроенные правила Coroot (у `kubernetes-events` `minCount: 3` — без шума от разовых событий на раскатке; `new-log-patterns`, `network-tcp-connections`, `network-connectivity`, `memory-leak` — только для категории `application`; `memory-leak` `for: 2h` — прогрев .NET после деплоя; `instance-availability` `for: 10m` — раскатка portal с `maxSurge: 0`) + свои `certificate-expiry`, `certificate-not-ready`, `node-cpu-requests`, `cluster-cpu-requests` (`spec.projects[].alertingRules`) |
| Категории | `control-plane` дополнена шаблонами `_/*` (процессы хоста) и `infra/*` (fluent-bit); `dev` — без уведомлений |
| Категория `dev` | `kogda-dev/*`, `joinrpg-dev/*` — только в интерфейсе, без уведомлений |
| SLO latency | prod `joinrpg-portal`, `joinrpg-idportal` — 95% быстрее 1 с (длинные соединения Blazor); `kogda-prod` `kogda-igra` — 95% / 1 с (ложные > 10 с в серверной гистограмме, G156) |
| Размещение | сервер, ClickHouse, Keeper — только зона `ru-6a`; сервер и ClickHouse по возможности не на одной ноде с `joinrpg-portal` (`joinrpg-prod`) |

Версии образов (закреплены в [`values.yaml`](values.yaml)):

| Компонент | Образ |
|---|---|
| Сервер | `ghcr.io/coroot/coroot:1.27.4` |
| node-agent | `ghcr.io/coroot/coroot-node-agent:1.36.4` |
| cluster-agent | `ghcr.io/coroot/coroot-cluster-agent:1.11.6` |
| ClickHouse, Keeper | зашиты в оператор (меняются вместе с ним) |

## Пользовательские дашборды

JSON дашбордов хранится в [`dashboards/`](dashboards/README.md), синхронизация —
[`scripts/sync-dashboard.py`](scripts/sync-dashboard.py). Это отдельная команда
через API Coroot 1.27.4, не часть Helm-релиза. По умолчанию скрипт показывает план,
запись выполняется с `--apply`. Перед обновлением сохраняется локальная резервная
копия. Авторизация пользовательская, ключи агентов для неё не подходят.
Доступны дашборды MSSQL, самого мониторинга, prod-приложений и запаса ресурсов
Kubernetes-кластера. Команды пакетного применения — в инструкции `dashboards/`.

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
helm template coroot . -n coroot --show-only templates/coroot.yaml > /tmp/coroot-cr.yaml
grep -n '\$' /tmp/coroot-cr.yaml                      # ровно одна строка: TELEGRAM_RELAY_KEY
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

## Метрики PostgreSQL DBaaS Selectel

`dbaasMetrics` в `values.yaml` включает Deployment `coroot-dbaas-metrics`:
cluster-agent → HTTP `/metrics` прокси → HTTPS `ru-6.dbaas.selcloud.ru/metrics` с Bearer.
Токен берётся из Secret `coroot/selectel-dbaas-metrics`, ключ `token`.
Secret создаётся вручную, Helm его не хранит и не удаляет.
Это отдельные ресурсы чарта вне `spec` Coroot, изменение прокси не меняет CR оператора.
Отдельные Prometheus, Service и публичный маршрут не создаются.

Прокси работает без root с read-only rootfs, временная конфигурация с токеном —
в `/tmp` на memory-backed `emptyDir`, права файла `600`. Образ закреплён digest.
ConfigMap содержит только шаблон, токена в нём нет. NetworkPolicy разрешает вход
на порт 8080 только cluster-agent в этом namespace (метки проверены для оператора 1.10.6).
Доступ Kubernetes-администраторов к pod/Secret этим не ограничивается.
TLS upstream проверяется, DNS обновляется, кеш отключён; ошибки API передаются
сборщику. `/healthz` проверяет сам nginx, а не доступность Selectel.
Не запускать `nginx -T`: он напечатает конфигурацию с токеном.

Источник и область токена: **Kaitlyn → Мониторинг → Токены для Prometheus**.
Токен даёт доступ к метрикам всех DBaaS-кластеров проекта в пуле `ru-6`.
Проверка 06.11.2026: 46 имён метрик, 536 рядов, две ноды Kaitlyn.
Общие метки: `ds_id`, `ds_name`, `ds_group`, `ds_type_version`, `ip`; у БД — `datname`.
CPU, RAM и диск (`dbaas_cpu`, `dbaas_memory_percent`, `dbaas_disk_percent`) — проценты.
Есть OOM, WAL, replication lag, подключения, блокировки и ожидание PgBouncer.
Метрики IO уже выражены в секунду; ряд имён, похожих на счётчики, объявлен `gauge`.
Не применять `rate()` только на основании имени. История начинается с подключения,
метрики сохраняются в ClickHouse на общий срок `metricsTTL` (30d).
Это не SQL-интеграция `clusterAgent.databases` и не доставка логов PostgreSQL.
Дашборд и правила алертов DBaaS добавляются после проверки сбора.

Команды для WSL из корня репозитория. Сначала создать Secret из локального
`secrets.env` (токен не передаётся аргументом процесса и не печатается),
отрендерить и проверить diff. `kubectl diff` возвращает 1, когда есть отличия.
В diff ожидаются только новые ресурсы `coroot-dbaas-metrics`, CR Coroot не меняется.

```bash
(
set -euo pipefail
export KUBECONFIG=~/.kube/joinrpg.yaml
source <(tr -d '\r' < secrets.env)
test -n "${SELECTEL_DBAAS_METRICS_TOKEN:-}" || { echo 'Не заполнен SELECTEL_DBAAS_METRICS_TOKEN'; exit 1; }
printf '%s' "$SELECTEL_DBAAS_METRICS_TOKEN" | kubectl create secret generic selectel-dbaas-metrics -n coroot \
  --from-file=token=/dev/stdin --dry-run=client -o yaml | kubectl apply -f -

helm lint ./platform/coroot -n coroot
helm template coroot ./platform/coroot -n coroot > /tmp/coroot-dbaas.yaml
kubectl diff -f /tmp/coroot-dbaas.yaml || test "$?" -eq 1
helm upgrade coroot ./platform/coroot -n coroot --dry-run=server
)
```

После проверки diff:

```bash
helm upgrade coroot ./platform/coroot -n coroot
kubectl rollout status deployment/coroot-dbaas-metrics -n coroot --timeout=120s
kubectl get pods -n coroot -l app.kubernetes.io/name=selectel-dbaas-metrics -o wide
kubectl logs -n coroot deployment/coroot-dbaas-metrics --tail=30
```

Через 1–2 минуты проверить, что метрики дошли до ClickHouse:

```bash
kubectl exec -i -n coroot coroot-clickhouse-shard-0-0 -c clickhouse-server -- \
  clickhouse-client --readonly=1 -d coroot_0r4fqpy1 <<'SQL'
SELECT MetricName, Labels['ds_name'] AS cluster, Labels['ip'] AS node,
       argMax(Value, Timestamp) AS value, max(Timestamp) AS last_seen
FROM metrics
WHERE Timestamp > now() - INTERVAL 5 MINUTE
  AND MetricName IN ('dbaas_cpu', 'dbaas_memory_percent', 'dbaas_disk_percent', 'dbaas_oom_count')
GROUP BY MetricName, cluster, node
ORDER BY MetricName, node
FORMAT PrettyCompact;
SQL
```

Если данных нет: проверить Secret (только наличие ключа, без печати значения),
логи прокси и cluster-agent. HTTP 401 означает проблему токена; 502/504 —
DNS/TLS/сеть или отказ upstream. Пробы могут оставаться зелёными при отказе API —
успешный `/healthz` не доказывает успешный сбор `/metrics`.
У cluster-agent scrape interval по умолчанию 15s, эта настройка общая для его целей.

При ротации повторить создание Secret и выполнить
`kubectl rollout restart deployment/coroot-dbaas-metrics -n coroot`:
токен читается из env при старте, смена Secret сама не перезапускает pod.
Изменения шаблона ConfigMap вызывают rollout через checksum-аннотацию.
Чтобы отключить сбор, поставить `dbaasMetrics.enabled: false` и применить чарт:
удалятся только ресурсы прокси, накопленные метрики и Secret останутся.

Документация: [метрики Selectel](https://docs.selectel.ru/managed-databases/postgresql/monitoring/).

## Смена сроков хранения

`metricsTTL`, `tracesTTL`, `logsTTL`, `profilesTTL` в `values.yaml` действуют только на **новые** таблицы: Coroot создаёт их через `CREATE TABLE IF NOT EXISTS ... TTL`, существующие не меняет (G157). После `helm upgrade` сроки уже созданных таблиц меняются вручную, в секундах (1d = 86400). Ниже переход логов с 7d на 30d и профилей с 3d на 14d; TTL метрик (30d) и трейсов (7d) не меняется. Уже удалённая история не восстанавливается.

```bash
kubectl exec -i -n coroot coroot-clickhouse-shard-0-0 -c clickhouse-server -- \
  clickhouse-client -d coroot_0r4fqpy1 --multiquery --materialize_ttl_after_modify=0 <<'EOF'
ALTER TABLE otel_logs MODIFY TTL toDateTime(Timestamp) + toIntervalSecond(2592000);
ALTER TABLE otel_logs_service_name_severity_text MODIFY TTL toDateTime(LastSeen) + toIntervalSecond(2592000);
ALTER TABLE profiling_stacks MODIFY TTL toDateTime(LastSeen) + toIntervalSecond(1209600);
ALTER TABLE profiling_samples MODIFY TTL toDateTime(Start) + toIntervalSecond(1209600);
ALTER TABLE profiling_profiles MODIFY TTL toDateTime(LastSeen) + toIntervalSecond(1209600);
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
- на Gateway `main` — listener `coroot.joinrpg.ru` (HTTPS, порт 8443, Secret `coroot-joinrpg-tls`), задан в [`platform/gateway`](../gateway/README.md) (`https.listeners` в его `values.yaml`);
- сертификат: по аннотации `cert-manager.io/cluster-issuer: letsencrypt-prod` на Gateway cert-manager сам создаёт `Certificate` `infra/coroot-joinrpg-tls` и продлевает его (Let's Encrypt, HTTP-01 через listener `http`);
- редирект http → https — общий HTTPRoute `infra/https-redirect`;
- маршруты принимаются только из namespace с меткой `gateway-access=shared` — она стоит на `coroot` вручную;
- запись DNS `coroot.joinrpg.ru` — в `joinrpg-infrastructure`, слой `05-dns`.

Метка namespace (один раз, до `helm upgrade` с маршрутом). Listener `coroot.joinrpg.ru` уже есть в чарте `platform/gateway`; на чистом кластере релиз `gateway` ставится до маршрута. Править Gateway патчем или `kubectl apply` нельзя — правка пропадёт при `helm upgrade gateway` (README `platform/gateway`).

```bash
kubectl label ns coroot gateway-access=shared
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
