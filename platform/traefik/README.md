# traefik

Входной HTTP(S)-трафик кластера `joinrpg`: Traefik — контроллер Gateway API (GatewayClass `traefik`) и обработчик IngressRoute, за ним — балансировщик Селектела с публичным адресом.

Обёртка над официальным чартом [`traefik/traefik`](https://github.com/traefik/traefik-helm-chart), версия зависимости — в [`Chart.yaml`](Chart.yaml), настройки — в [`values.yaml`](values.yaml) под ключом `traefik:` (только отличия от значений по умолчанию). Gateway `infra/main` и листенеры сайтов — отдельный релиз [`platform/gateway`](../gateway/README.md).

| | |
|---|---|
| Релиз | `traefik` |
| Namespace | `traefik` |
| Вид | Deployment, 1 реплика; Service `traefik/traefik` типа `LoadBalancer` |
| Входы | `web` (8000, снаружи 80), `websecure` (8443, снаружи 443) |
| Провайдеры | Gateway API, IngressRoute (CRD), Ingress |
| IP клиента | PROXY protocol от балансировщика (раздел ниже) |
| Журнал запросов | stdout, JSON → fluent-bit → Логи Селектела, группа `k8s/traefik` |

История: релиз поставлен руками `helm install` 2026-10-13 (rev 1–3), в репозиторий перенесён 2026-10-22 тем же именем и в тот же namespace (rev 4, объекты не менялись).

## Балансировщик — главный риск

Балансировщик (Octavia, `amphora`) создал облачный контроллер Kubernetes (`openstack-cloud-controller-manager`) для `Service traefik/traefik` и владеет им: имя `kube_service_joinrpg_traefik_traefik`, id — в аннотации Service `loadbalancer.openstack.org/load-balancer-id`, публичный адрес — в README `joinrpg-infrastructure`.

- **Удаление Service или `helm uninstall traefik` удаляет балансировщик** вместе с публичным адресом и ручными слушателями. DNS всех сайтов указывает на этот адрес.
- **Любое изменение Service** (аннотации, порты, тип) запускает у контроллера `EnsureLoadBalancer`: он пересинхронизирует балансировщик и **удаляет на нём слушатели без тегов** (`openstack-cloud-controller-manager` v1.35, `deleteOctaviaListeners`). Перед изменением Service — проверить теги ручных слушателей (раздел ниже).
- Смена протокола пула (аннотация `proxy-protocol`) — пулы 80/443 удаляются и создаются заново: **простой сайтов до 30 секунд** (2026-10-22: 19:12:40–19:13:10).
- Изменения Deployment (args, ресурсы) балансировщик не трогают, но перезапускают под: при одной реплике — короткий отказ в соединениях на время обновления endpoints.

Понять, тронет ли `upgrade` Service: `kubectl diff` (ниже) показывает `v1.Service.traefik.traefik` — тогда это изменение балансировщика. Контроль после: `resourceVersion` Service до и после `upgrade` одинаковый — Service не менялся.

## Ручные слушатели на балансировщике

На том же балансировщике вручную (`openstack loadbalancer`) заведены TCP-слушатели доступа к базам. В чарте их нет и быть не должно — это не Service Kubernetes; порты 1433, 2222, 5433 **нельзя** добавлять в порты Service traefik.

| Слушатель | Порт | Куда | Доступ |
|---|---|---|---|
| `joindb-mssql` | 1433 | MS SQL, ВМ `mssql-prod`:1433 | список адресов `allowed_cidrs` |
| `joindb-ssh` | 2222 | SSH ВМ `mssql-prod`:22 | список адресов `allowed_cidrs` |
| `joindb-psql` | 5433 | пулер мастера PostgreSQL DBaaS:5433 | список адресов `allowed_cidrs` |

Адреса, `allowed_cidrs` и id — не здесь (репозиторий публичный): снимок — `docs/lb-snapshot-*.json` (в `.gitignore`), живьём — скриптом ниже.

Параметры у всех трёх одинаковые: слушатель TCP, таймауты клиента и бэкенда 1 ч (3600000 мс), соединение с бэкендом 5 с; пул TCP `ROUND_ROBIN`, один участник; health monitor TCP, delay 10, timeout 5, retries 3.

**У каждого ручного слушателя должен быть тег `joindb-manual`** — иначе контроллер удалит его при первом же изменении Service. Тег проставлен 2026-10-22.

### Скрипты

Из корня репозитория, в WSL; нужны `secrets.env` (токен Keystone) и `KUBECONFIG` на кластер.

```bash
bash platform/traefik/scripts/lb-snapshot.sh [суффикс]           # снимок в docs/lb-snapshot-<суффикс>.json + сводка (только чтение)
bash platform/traefik/scripts/tag-manual-listeners.sh            # какие ручные слушатели без тега (только чтение)
bash platform/traefik/scripts/tag-manual-listeners.sh --apply    # проставить тег joindb-manual
```

Ручным скрипт считает любой слушатель без тега контроллера (имени балансировщика).

### Пересоздать слушатель

Если слушатель пропал — по снимку (`docs/lb-snapshot-*.json`: порт, адрес участника, `allowed_cidrs`). OpenStack CLI с `python-octaviaclient`, окружение из `secrets.env`; после каждой команды балансировщик должен вернуться в `ACTIVE` (`openstack loadbalancer show <lb> -c provisioning_status`).

```bash
LB=<id балансировщика>
openstack loadbalancer listener create --name joindb-mssql --protocol TCP --protocol-port 1433 \
  --timeout-client-data 3600000 --timeout-member-data 3600000 --timeout-member-connect 5000 \
  --allowed-cidr <адрес/32> --allowed-cidr <адрес/32> --allowed-cidr <адрес/32> \
  --tag joindb-manual $LB
openstack loadbalancer pool create --name joindb-mssql --listener joindb-mssql --protocol TCP --lb-algorithm ROUND_ROBIN
openstack loadbalancer member create --name mssql-prod --address <внутренний адрес ВМ> --protocol-port 1433 joindb-mssql
openstack loadbalancer healthmonitor create --name joindb-mssql --type TCP --delay 10 --timeout 5 --max-retries 3 joindb-mssql
```

Для `joindb-ssh` — порт 2222, участник `mssql-prod-ssh` на порт 22; для `joindb-psql` — порт 5433, участник `psql-master` на порт 5433.

### Сменить список адресов

`allowed_cidrs` задаётся целиком (старый список заменяется):

```bash
openstack loadbalancer listener set --allowed-cidr <адрес/32> --allowed-cidr <адрес/32> joindb-mssql
```

### Бэкенд PostgreSQL после переключения мастера

Участник `psql-master` задан IP-адресом мастера. После переключения мастера DBaaS — заменить участника на адрес, который даёт имя мастера кластера (в README `joinrpg-infrastructure`):

```bash
openstack loadbalancer member create --name psql-master --address <новый адрес> --protocol-port 5433 joindb-psql
openstack loadbalancer member delete joindb-psql <id старого участника>
```

## Реальный IP клиента

Балансировщик — TCP-прокси: без дополнительной настройки Traefik видит адрес ноды или пода, а не клиента.

- Аннотация Service `loadbalancer.openstack.org/proxy-protocol: "true"` — пулы 80/443 с протоколом PROXY: балансировщик передаёт IP клиента PROXY-заголовком ([документация Селектела](https://docs.selectel.ru/managed-kubernetes/networks/use-proxy)). Заодно `status.loadBalancer.ingress[].ipMode` становится `Proxy`: запросы изнутри кластера к публичному адресу тоже идут через балансировщик.
- `ports.*.proxyProtocol.trustedIPs` — PROXY-заголовок принимается только из сети подов `10.10.0.0/16` (SNAT kube-proxy при `externalTrafficPolicy: Cluster`) и подсети нод `192.168.0.0/24`. Соединение без заголовка Traefik принимает как обычное.
- `ports.*.forwardedHeaders` без `insecure` и `trustedIPs` — `X-Forwarded-For` от клиента отбрасывается, Traefik ставит свой. Приложения доверяют `X-Forwarded-For` от сети подов.

Проверка — поле `ClientHost` журнала запросов: внешние адреса.

```bash
kubectl logs -n traefik deploy/traefik --since=5m | grep -o '"ClientHost":"[^"]*"' | sort | uniq -c | sort -rn | head
```

Откат: убрать аннотацию из `values.yaml` и `helm upgrade` — пулы вернутся на TCP с тем же коротким простоем (теги ручных слушателей — до этого). `trustedIPs` можно оставить: без PROXY-заголовка они ни на что не влияют.

## Журнал запросов

JSON в stdout пода, в Логах Селектела — группа `k8s/traefik`, поток `traefik`. Полезные поля: `ClientHost` (IP клиента), `RequestHost`, `RequestMethod`, `RequestPath`, `DownstreamStatus`, `Duration` (нс), `RouterName`, `request_User-Agent`. Из заголовков пишется только `User-Agent`: в остальных куки и токены.

## Установка и обновление

Из каталога чарта, `KUBECONFIG` указывает на кластер `joinrpg`. Путь к чарту — абсолютный (G152).

```bash
cd /mnt/d/Gitlab/JoinRPG/joinrpg-helms/platform/traefik
helm repo add traefik https://traefik.github.io/charts   # один раз
helm dependency build

helm lint .
helm template traefik . -n traefik --no-hooks | kubectl diff -f -
helm upgrade traefik . -n traefik --dry-run=server

helm upgrade traefik . -n traefik
```

### Чек-лист, если `kubectl diff` затрагивает Service

До:
1. `bash platform/traefik/scripts/lb-snapshot.sh before-<что>` — снимок.
2. `bash platform/traefik/scripts/tag-manual-listeners.sh` — у всех ручных слушателей есть `joindb-manual`; если нет — `--apply`.
3. Тихое время: смена пулов — до 30 с простоя.

После:
1. `kubectl get events -n traefik --field-selector involvedObject.kind=Service` — `EnsuredLoadBalancer`, не `SyncLoadBalancerFailed`.
2. `bash platform/traefik/scripts/lb-snapshot.sh after-<что>` — все слушатели на месте, участники `ONLINE`.
3. Сайты по HTTPS; базы — подключение через 1433/5433, SSH 2222.

### Обновление версии Traefik

Поднять `version` зависимости в [`Chart.yaml`](Chart.yaml), `helm dependency update`, прочитать [заметки к выпуску](https://github.com/traefik/traefik-helm-chart/releases) чарта (изменения values, CRD — в `crds/` подчарта, Helm их при `upgrade` не обновляет). В `kubectl diff` должны быть Deployment и CRD-зависимые объекты, **не Service** — иначе разобрать до `upgrade`.
