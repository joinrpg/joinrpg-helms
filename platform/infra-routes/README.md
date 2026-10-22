# infra-routes

Сайты-обёртки над бакетами в ns `infra`: IngressRoute traefik, Middleware и Service типа ExternalName. Шаблоны — [`templates/`](templates/), список сайтов — [`values.yaml`](values.yaml).

| | |
|---|---|
| Релиз | `infra-routes` |
| Namespace | `infra` |
| Вход | entryPoint `websecure` traefik ([`platform/traefik`](../traefik/README.md)), не Gateway API |
| Бэкенды | Service ExternalName: бакеты S3 Селектела, сайты Yandex Object Storage (`allowExternalNameServices` в чарте traefik) |

| Сайт | Как работает |
|---|---|
| `avatars.joinrpg.ru`, `avatars.dev.joinrpg.ru` | Middleware `redirectRegex` — 301 на адрес бакета Селектела с тем же путём; до Service запрос не доходит |
| `docs.joinrpg.ru`, `docs.dev.joinrpg.ru` | прокси по HTTP на сайт бакета Yandex Object Storage (`*.website.yandexcloud.net:80`) |
| `rpg.ru` | прокси по HTTPS на бакет Селектела — страница-заглушка; **сертификата нет** (ниже) |

Service `joinrpg-s3` ни одним маршрутом не используется — оставлен как есть (2026-10-23).

История: объекты созданы вручную (`kubectl apply`/Freelens) 2026-10-13…21, переданы релизу 2026-10-23 без изменения `spec` (rev 1).

## Сертификаты — через Gateway

IngressRoute не выпускает сертификаты сам. HTTPS работает так:

- `tlsSecret` задан (`docs.*` — `joinrpg-tls`) — traefik берёт этот Secret. Его выпускает cert-manager, потому что на Gateway есть листенер с этим Secret и именем `docs.*`.
- `tlsSecret` не задан (`tls: {}`, `avatars.*`, `rpg.ru`) — traefik отдаёт сертификат по SNI из общего хранилища, куда попадают Secret'ы листенеров Gateway. Для `avatars.*` это листенеры `avatars.*` в [`platform/gateway`](../gateway/README.md).

Отсюда:

- **Листенеры Gateway `avatars.*`, `docs.*` нужны этим сайтам**, хотя HTTPRoute на них нет. Убрать листенер — сайт останется без своего сертификата.
- Нет листенера — traefik отдаёт `TRAEFIK DEFAULT CERT`, браузер показывает ошибку. Так сейчас у `rpg.ru` (решение 2026-10-23 — пока не трогаем). Починка: листенер `rpg.ru` с отдельным Secret в `platform/gateway`.
- HTTP → HTTPS для этих имён делает общий `https-redirect` Gateway.

## Как добавить сайт

1. DNS — A-запись на балансировщик traefik; листенер с этим именем и Secret — в [`platform/gateway`](../gateway/README.md) (иначе нет сертификата).
2. Бэкенд — элемент `externalServices` (имя бакета или сайта и порт).
3. Маршрут — элемент `ingressRoutes`: `host`, `service` (`name`, `port`, `scheme`: `https` для бакетов Селектела, `http` для сайтов Yandex; для внешних бэкендов — `serversTransport: short-dial`), при необходимости `middlewares` (`retry-on-connect-error` для внешних бэкендов), `priority`, `tlsSecret`.
4. Редирект вместо прокси — элемент `redirects` и его имя в `middlewares` маршрута.
5. `helm upgrade` (ниже), проверка — `curl -sv https://<имя>/` (сертификат Let's Encrypt, нужный код ответа).

`passHostHeader: false` в шаблоне — бэкенду уходит его собственное имя (`*.selstorage.ru`, `*.website.yandexcloud.net`), иначе хранилище не найдёт бакет.

## Установка и обновление

```bash
cd /mnt/d/Gitlab/JoinRPG/joinrpg-helms/platform/infra-routes
helm lint .
helm template infra-routes . -n infra | kubectl diff -n infra -f -
helm upgrade --install infra-routes . -n infra --dry-run=server

helm upgrade --install infra-routes . -n infra
```

Не править эти объекты `kubectl edit` или в Freelens — правка пропадёт при следующем `helm upgrade`.

## Что нужно знать

- **Сайты Yandex (`docs.*`) изредка не принимают подключение.** TCP-подключение из кластера к `*.website.yandexcloud.net` иногда не устанавливается (≈1–2 % попыток, бывает пачками; с любой ноды, 2026-10-23). Без защиты traefik ждал подключения до 30 с — зависшая страница, в access log `DownstreamStatus` 499/502. Поэтому у маршрутов `docs.*` — ServersTransport `short-dial` (`dialTimeout: 2s`) и Middleware `retry-on-connect-error` (3 попытки): неудачное подключение стоит ~2 с задержки. Retry повторяет запрос, только если бэкенд не ответил, — повтор после полученного ответа не делается.
- **Новый Middleware или ServersTransport для уже работающего маршрута — в два `helm upgrade`:** сначала только объект, потом ссылка на него в маршруте. Если traefik получит маршрут раньше объекта, он выкидывает маршрут целиком (`servers transport not found` / `middleware ... does not exist` в логе) — сайт отдаёт 404, пока не подтянется объект (2026-10-23: ~30 с на `docs.joinrpg.ru`).
- `helm uninstall infra-routes` удаляет все пять сайтов вместе с ExternalName.
