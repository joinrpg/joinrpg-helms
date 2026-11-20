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
| Алерты | встроенные правила Coroot (у `kubernetes-events` `minCount: 3` — без шума от разовых событий на раскатке; `new-log-patterns`, `network-tcp-connections`, `network-connectivity`, `memory-leak` — только для категории `application`; `network-rtt-external` — только `application` и `for: 15m` — шум внешних фоновых соединений; `memory-leak` `for: 2h` — прогрев .NET после деплоя; `instance-availability` `for: 10m` — раскатка portal с `maxSurge: 0`) + свои `certificate-expiry`, `certificate-not-ready`, `node-cpu-requests`, `cluster-cpu-requests`, `calico-node-unavailable` (`spec.projects[].alertingRules`) |
| Категории | `control-plane` дополнена шаблонами `_/*` (процессы хоста) и `infra/*` (fluent-bit); `dev` — без уведомлений |
| Категория `dev` | `kogda-dev/*`, `joinrpg-dev/*` — только в интерфейсе, без уведомлений |
| SLO latency | prod `joinrpg-portal`, `joinrpg-idportal` — 95% быстрее 1 с (длинные соединения Blazor); `kogda-prod` и `kogda-dev` — 95% / 1 с (G156); у `coroot-sql-probes` latency objective 0%, контроль БД — отдельными SQL-алертами |
| Размещение | сервер, ClickHouse, Keeper — только зона `ru-6a`; сервер и ClickHouse по возможности не на одной ноде с `joinrpg-portal` (`joinrpg-prod`) |

Версии образов (закреплены в [`values.yaml`](values.yaml)):

| Компонент | Образ |
|---|---|
| Сервер | `ghcr.io/coroot/coroot:1.27.4` |
| node-agent | `ghcr.io/coroot/coroot-node-agent:1.36.4` |
| cluster-agent | `ghcr.io/coroot/coroot-cluster-agent:1.11.6` |
| ClickHouse, Keeper | зашиты в оператор (меняются вместе с ним) |

## Отказы ASP.NET Core rate limiting

Правило `aspnetcore-rate-limiting-rejected` проверяет рост
`aspnetcore_rate_limiting_requests_total` с меткой
`aspnetcore_rate_limiting_result=~"global_limiter|endpoint_limiter"` за последние 5 минут.
`acquired` — успешное получение разрешения, `request_canceled` — отмена ожидания;
эти результаты в алерт не входят.
Сначала применяется `increase`, затем результат суммируется по `namespace` и `pod`:
один warning на под при любом положительном приросте, без дополнительной выдержки
и удержания после исчезновения условия. Учитываются все поды проекта Coroot,
для которых собирается эта метрика с непустой меткой `pod`.
Уведомления направляются в категорию `application`.
Поля `for` и `keepFiringFor` опущены: Coroot 1.27.4 не принимает явное `0s`
и завершает запуск с `invalid duration: 0s`, хотя Helm успешно рендерит чарт.

Счётчик не обязан обнуляться: после прекращения отказов условие исчезает,
когда прирост выходит из окна 5 минут. Для `increase` нужны минимум два образца,
поэтому отказ до первого сбора новой серии может остаться незамеченным.
Отсутствие метрики этим правилом не проверяется.

Для сбора `/metrics` на порту 8080 у подов приложения нужны аннотации:

```yaml
coroot.com/scrape-metrics: "true"
coroot.com/metrics-port: "8080"
```

На проверке 18.11.2026 метрика есть у обоих `joinrpg-dev` portal, но scrape-аннотаций
не было. На prod portal метрика ещё не экспортировалась. Временно включить сбор
на текущих dev-подах без перезапуска можно из WSL:

```bash
kubectl annotate pods -n joinrpg-dev -l app.kubernetes.io/name=joinrpg-portal \
  coroot.com/scrape-metrics=true coroot.com/metrics-port=8080 --overwrite
```

Постоянно аннотации нужно добавить в `Deployment.spec.template.metadata.annotations`
в репозитории приложения: аннотация текущих pod исчезнет при следующем деплое.
Deployment приложения этим чартом не управляется.
Для повторного теста дождаться двух scrape-образцов и затем получить новые отказы:
отказы до начала сбора `increase` не обнаруживает. Уведомления этого правила
направлены в `application`, поэтому тестовый отказ dev тоже уведомляет.

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

Формат алертов: `⚠️ Предупреждение · <имя правила>` или
`🚨 Критично · <имя правила>`, проект, приложение (если оно известно),
текст причины, «Что проверить» (если первое поле Details — непустой
Description), ссылка. При закрытии заголовок меняется на `✅ Алерт закрыт`,
причина и ссылка сохраняются, длительность добавляется только при наличии.
Рекомендации для активного алерта в сообщение о закрытии не копируются.

Coroot 1.27.4 не привязывает пользовательские PromQL-алерты к приложению,
даже если у результата есть метки `namespace` и `pod`. Отдельных меток ресурса
в webhook-шаблоне нет: под, диск или сертификат остаётся в Summary правила.
Поэтому строка «Приложение» выводится только при непустом имени,
без фиктивного «Объект: —/без привязки». При пустом namespace выводится только имя.

Например, для rate limiting:

```text
⚠️ Предупреждение · ASP.NET Core rate limiting rejected requests
Проект: coroot

ASP.NET Core: под joinrpg-dev/joinrpg-portal-… отклоняет запросы по rate limiting

Что проверить: За последние 5 минут вырос счётчик отказов…
<ссылка на алерт>
```

Чтобы реле не обрезало ссылку при лимите Telegram 4096 UTF-16 единиц,
ограничены длины полей в рунах: имя правила80, проект64, namespace63,
имя приложения128, Summary650, Description450, длительность64.
Это оставляет запас и для символов вне BMP; URL выводится целиком.
Raw Sample, логи и PromQL из Details не отправляются. Шаблоны инцидентов
и деплоев и маршрутизация уведомлений этим форматом не меняются.

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

### Качество сигналов (07.11.2026)

- `coroot:Deployment:coroot-sql-probes`: latency objective **0% / 5s**.
  В Coroot 1.27.4 это убирает latency burn-rate incidents: доля медленных запросов
  не может превысить бюджет 100%. HTTP `/metrics` технического экспортера при
  отказе БД закономерно ждёт timeout; контролировать его пользовательским SLO
  не нужно. Метрики, availability SLO и отдельные critical SQL-правила не отключаются.
- `kogda-dev:Deployment:kogda-igra`: latency **95% / 1s**, как у kogda-prod.
  Последние эпизоды G156 проверены по Traefik за 06.11 21:00–21:45 МСК:
  29 запросов, max415ms, ни одного >500ms, HTTP5xx0. Availability не ослабляется.
- `kubernetes-events`: `minCount: 3` сохранён, дополнительный `keepFiringFor`
  уменьшен с1h до10m. **Часовое окно событий остаётся:** это не настройка
  подавления стартовых CNI/FailedMount и не гарантирует закрытие через10мин
  после последнего события. Источник этой версии не применяет `for`; CRD не даёт
  фильтра по возрасту ноды/причине события. Такие ошибки не исключаются целиком.
- Webhook alert template показывает проект, приложение (если известно), summary
  и первое поле Description, если оно есть. При автоотбое — summary и длительность.
  Description должен содержать влияние и первые действия (они уже заданы у
  SQL/дисковых правил). Для KubeEvents добавлена инструкция сверить свежесть события,
  readiness и rollout. Raw Sample/логи/PromQL из Details в сообщение не копируются.
  Актуальные лимиты полей и формат описаны в разделе уведомлений выше;
  JSON формирует функция `json`.
  Для dev маршрутизация без уведомлений сохраняется, SQL-alerts идут в application.

Новые параметры изменяют только CR Coroot, не SQL-пробы, приложение или Kubernetes
probes. Для отката SLO явно задать dev99%/500ms и exporter99%/5s; не откатывать
полностью values из старого коммита с отсутствующими селекторами G198.

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
Дашборд «PostgreSQL: DBaaS Kaitlyn» — [`dashboards/postgresql.json`](dashboards/postgresql.json),
команда синхронизации — в [`dashboards/README.md`](dashboards/README.md).
Правила алертов — ниже, в разделе «Алерты MSSQL и PostgreSQL DBaaS».

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

## Алерты MSSQL и PostgreSQL DBaaS

Первый набор — диски и потеря наблюдаемости. Правила в
`spec.projects[].alertingRules`, уведомления пользовательских правил направлены
в категорию `application` (действующий webhook Telegram).

| Правило | Условие | Выдержка | Уровень |
|---|---|---|---|
| Встроенный `storage-space`, в том числе MSSQL | Занято >80% | 5m | warning |
| `mssql-disk-critical` | Файловая система `/var/opt/mssql` занята >90% | 5m | critical |
| `mssql-metrics-missing` | За 5m нет `node_agent_info` или метрик диска MSSQL | Ещё 2m | critical |
| `dbaas-disk-warning` | Диск каждой ноды Kaitlyn занят >80% | 15m | warning |
| `dbaas-disk-critical` | Диск каждой ноды Kaitlyn занят >90% | 5m | critical |
| `dbaas-metrics-missing` | `up` прокси =0 либо за 5m есть метрики диска менее двух нод | 2m | critical |
| `dbaas-pgbouncer-queue` | В одной БД одной ноды есть ожидающие клиенты и maxwait >5s | 1m | warning |
| `dbaas-no-master` | У ноды Kaitlyn `dbaas_role=0` (роль неизвестна) либо нет рядов ролей за 5m | 1m | warning |

MSSQL warning уже встроен в Coroot 1.27.4 (порог 80%, `for: 5m`), поэтому
новое предупреждение 80%/15m не добавляется: это дало бы дубль. Встроенное правило
для остальных приложений не меняется. На высоком заполнении warning и critical
могут существовать одновременно — это два уровня, автоматического подавления нет.

При исчезновении метрик предупреждение возникает примерно через 7 минут
(5m окно отсутствия +2m выдержка +цикл проверки). Ошибка scrape видимой цели
DBaaS обнаруживается быстрее — после 2m выдержки и цикла проверки.
Проверяется отсутствие всех данных, а также потеря одной ноды Kaitlyn.
Количество `2` — ожидаемый состав кластера: при изменении числа нод нужно
обновить `dbaas-metrics-missing`. Фильтр по `ds_id`, без закрепления IP мастера.
Для MSSQL используется подтверждённый machine-id ВМ и mount point тома.

Очередь PgBouncer сопоставляется по ноде и базе: ожидание в одной БД не соединяется
с числом клиентов другой. Начальный порог выбран для устойчивой очереди;
за первые 35 минут сбора waiting и maxwait были нулевыми. После рабочего пика
порог можно пересмотреть. При отсутствии метрик очереди правило молчит — потерю
сбора проверяет отдельное правило выше.

`dbaas-no-master` добавлен после failover 19.11.2026: старый мастер 3,5 минуты имел
`dbaas_role=0` до переключения на реплику. Роли: 0 — неизвестна, 1 — мастер, 2 — реплика.
Выдержка `dbaas-pgbouncer-queue` снижена до 1m: очередь при failover держалась ~2 минуты
и с `for: 5m` не была поймана. При полном отсутствии рядов `dbaas_role` ветка
`absent_over_time` срабатывает без меток ноды — потерю сбора деталирует `dbaas-metrics-missing`.

`calico-node-unavailable` (`kube_daemonset_status_number_unavailable > 0`, `for: 5m`) —
устойчивый сигнал недоступности CNI. Стартовые события rolling restart (`bird.ctl`,
BGP, `FailedPreStopHook`) встроенный `kubernetes-events` не фильтрует: `for` к нему
не применяется, `minCount`/`keepFiringFor` шум не устраняют (разбор 20.11.2026).

Эти алерты не доказывают SQL-доступность, не определяют причину сбоя и не заменяют
внешний мониторинг самого Coroot: если Coroot остановится, он не сможет отправить
свои уведомления. CPU и память пока не добавлены. SQL-пробы описаны ниже.

CRD оператора 1.10.6 поддерживает у `source.promql` только `expression`:
поле `scope` из API-модели Coroot здесь использовать нельзя. Проверять рендер
через `kubectl apply --dry-run=server --validate=strict`, а у Helm dry-run
проверять не только exit code, но и отсутствие предупреждений `unknown field`.

У каждого пользовательского PromQL-правила явно задаётся `selector: {type: all}`.
Coroot 1.27.4 не подставляет его автоматически: пустой селектор не совпадает
с созданным алертом, и `resolveNonMatchingAlerts` тут же закрывает его даже при
сохраняющейся ошибке. Проверка непустого результата PromQL и `enabled: true`
эту проблему не выявляет — нужен тест открытия и устойчивого состояния алерта.

Проверка запросов 06.11.2026 через read-only API: в здоровом состоянии все пять
правил пусты; проверены пониженные пороги дисков, отсутствующий агент, отсутствие
всех/одной ноды DBaaS и условие ошибки scrape. Это проверка выражений, не отправка
тестовых уведомлений и не остановка реальных компонентов.

Применение этих правил — обычный `helm upgrade coroot ./platform/coroot -n coroot`
после lint/template/diff и `--dry-run=server`. Изменение CR может перезапустить
сервер Coroot для чтения конфигурации; ВМ MSSQL и DBaaS оно не меняет.

## SQL-пробы MSSQL и PostgreSQL

`sqlProbes` включает один Deployment `coroot-sql-probes` с `sql_exporter` 0.24.13,
закреплённым digest. Две цели из Kubernetes выполняют `SELECT 1 AS success`:

| target | Подключение | База |
|---|---|---|
| `mssql` | `192.168.0.2:1433` | `joinrpg-prod` |
| `postgres` | master DNS Kaitlyn, PgBouncer `5433` | `joinrpg_prod_notifications` |

Отдельные пользователи `coroot_probe` имеют подключение к нужной базе, без
выданных пробой прав чтения/записи таблиц. PostgreSQL пользователь создаётся через
панель Selectel (G155), без административных ролей. MSSQL login и database user
создаются вручную, `GRANT CONNECT`. Учётки приложений и `sa` не используются.
Проверка 07.11.2026: обе учётки выполнили SELECT 1 через TCP-соединения из
`coroot-cluster-agent`. Пароли не выводились и не сохранялись в проверочные файлы.

В `secrets.env` заполнить `MSSQL_PROBE_USER`, `MSSQL_PROBE_PASSWORD`,
`POSTGRES_PROBE_USER`, `POSTGRES_PROBE_PASSWORD`.
Скрипт [`build-sql-probe-secret.py`](scripts/build-sql-probe-secret.py) кодирует
учётные данные в URI и готовит Secret `coroot-sql-probes` с полным JSON-конфигом
в ключе `sql_exporter.yml` (JSON совместим с YAML). По умолчанию выводится только
результат проверки конфигурации. `--secret-stdout` предназначен **только для pipe**
в kubectl: вывод содержит секреты, его нельзя публиковать или сохранять в Git.
В интерактивный TTY скрипт Secret не выводит. Сам скрипт ничего не применяет.

Сбор через аннотации `coroot.com/*`, без Service/Ingress. NetworkPolicy разрешает
вход только cluster-agent в namespace coroot. Pod без root и service-account token,
с read-only rootfs, requests 10m/32Mi и memory limit128Mi. Secret смонтирован read-only,
в ConfigMap/values паролей нет. Пробы pod проверяют процесс по TCP, не базы данных.

На каждом scrape выполняются запросы без кеша; соединения старше 1s не переиспользуются,
чтобы на следующем scrape снова проверить вход и разрешение DNS. К каждой цели не
больше одного соединения, общий timeout scrape5s. SQL выполняется только на чтение.
TLS шифруется, но CA/hostname не проверяются: PostgreSQL `sslmode=require`, MSSQL
`encrypt=true&TrustServerCertificate=true` (как в действующем подключении portal).
Доверенную CA для обеих БД можно подключить отдельным изменением.

`db_sql_probe_success{target="mssql|postgres"}=1` означает успешный SELECT1.
Экспортёр также отдаёт `up{target="…"}` — это проверка подключения/ping, отдельно
от результата SQL. Два новых critical правила `mssql-sql-probe` и `postgres-sql-probe`
ловят `up=0` в течение2m либо отсутствие результата SELECT1 за3m с выдержкой ещё2m.
Пустые метрики не трактуются как успех. Эти пробы не проверяют другие базы, права
пользователей приложений, запись или реальные бизнес-запросы.

При срабатывании `postgres-sql-probe` первым шагом проверить failover DBaaS
(`dbaas_role` обеих нод Kaitlyn, алерт `dbaas-no-master`), а не учётку `coroot_probe`:
19.11.2026 проба падала из-за недоступности старого мастера и восстановилась
после переключения с той же учёткой.

Применение в WSL из корня репозитория; Secret не печатается и не записывается на диск:

```bash
(
set -euo pipefail
export KUBECONFIG=~/.kube/joinrpg.yaml
source <(tr -d '\r' < secrets.env)
python3 -B platform/coroot/scripts/build-sql-probe-secret.py
python3 -B platform/coroot/scripts/build-sql-probe-secret.py --secret-stdout | kubectl apply -f -
helm lint ./platform/coroot -n coroot
helm upgrade coroot ./platform/coroot -n coroot --dry-run=server
helm upgrade coroot ./platform/coroot -n coroot
kubectl rollout status deployment/coroot-sql-probes -n coroot --timeout=180s
kubectl rollout status statefulset/coroot-coroot -n coroot --timeout=180s
)
```

После1–2мин проверить в ClickHouse:

```bash
kubectl exec -i -n coroot coroot-clickhouse-shard-0-0 -c clickhouse-server -- \
  clickhouse-client --readonly=1 -d coroot_0r4fqpy1 <<'SQL'
SELECT Labels['target'] AS target, argMax(Value, Timestamp) AS success,
       max(Timestamp) AS last_seen
FROM metrics
WHERE Timestamp > now() - INTERVAL 2 MINUTE AND MetricName = 'db_sql_probe_success'
GROUP BY target ORDER BY target FORMAT PrettyCompact;
SQL
```

Ожидаются две строки со значением1 и свежим временем. После ротации Secret нужен
`kubectl rollout restart deployment/coroot-sql-probes -n coroot`: конфиг читается
при старте, автоматическая перезагрузка не включена. Если отключать `sqlProbes.enabled`,
также отключить оба SQL-правила (`enabled: false`), иначе они сообщат о потере проб.
Обновление Secret не меняет пользователей и пароли самих БД.

Проверки: `python3 -B -m unittest discover -s platform/coroot/scripts -p 'test_*.py'`,
`sql_exporter -config.check` на закреплённом образе, строгая server dry-run Helm-манифестов.
Документация: [SQL Exporter](https://github.com/burningalchemist/sql_exporter/tree/0.24.13).

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
