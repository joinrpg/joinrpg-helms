# gateway

Gateway `infra/main` кластера `joinrpg` — общая точка входа всех сайтов (Gateway API), и HTTPRoute `infra/https-redirect` — редирект HTTP → HTTPS. Шаблоны — [`templates/`](templates/), параметры и список доменов — [`values.yaml`](values.yaml).

| | |
|---|---|
| Релиз | `gateway` |
| Namespace | `infra` |
| GatewayClass | `traefik` (релиз [`traefik`](../traefik/README.md)) |
| Листенер `http` | порт 8000 (снаружи 80), маршруты только из `infra`: `https-redirect` и HTTP-01 cert-manager |
| Листенеры HTTPS | порт 8443 (снаружи 443), по одному на имя; маршруты — из namespace с меткой `gateway-access=shared` |
| Сертификаты | cert-manager по аннотации `cert-manager.io/cluster-issuer: letsencrypt-prod` (релиз [`cert-manager`](../cert-manager/README.md)) |

История: Gateway и `https-redirect` созданы руками 2026-10-13, листенеры дописывались `kubectl edit`/Freelens; переданы релизу 2026-10-22 без изменения `spec` (rev 1). Rev 2 — редирект для всех сайтов (`from: Same`), rev 3 — листенер `components.joinrpg.ru`.

## Правки — только через этот чарт

Не править Gateway `kubectl edit`, `kubectl apply` старого файла или в Freelens. Список листенеров в Gateway API — обычный список: `kubectl apply` и `helm upgrade` заменяют его **целиком**. У Gateway остался устаревший `kubectl.kubernetes.io/last-applied-configuration` (6 листенеров из 21): `kubectl apply` по нему снесёт листенеры, добавленные позже, вместе с их сертификатами (G165). Ручная правка, кроме того, пропадёт при следующем `helm upgrade`.

## Как добавить домен

1. DNS: A-запись имени на балансировщик traefik (`joinrpg-infrastructure`, слой `05-dns`). Без неё сертификат не выпустится: HTTP-01 проверяет имя через листенер `http`.
2. Листенер — элемент `https.listeners` в [`values.yaml`](values.yaml):
   ```yaml
   - name: example.joinrpg       # уникальное имя листенера
     hostname: example.joinrpg.ru
     secret: example-joinrpg-tls # Secret сертификата в infra
   ```
   - **Новый Secret** — отдельный сертификат на это имя, остальные не трогаются. Так проще и безопаснее.
   - **Существующий Secret** — cert-manager перевыпустит общий сертификат с добавленным именем (в сертификат входят `hostname` всех листенеров с этим Secret). Ошибка в одном имени (нет DNS) остановит продление сертификата для всех имён этого Secret.
3. `helm upgrade` (ниже). cert-manager создаст `Certificate` с именем Secret, выпуск — 1–2 минуты.
4. HTTPRoute приложения (в его репозитории) — `parentRefs` на Gateway `main` в `infra`, `hostnames` — это имя; namespace приложения — с меткой `gateway-access=shared`.

Проверка:

```bash
kubectl get certificate <secret> -n infra                         # READY True
kubectl get httproute <маршрут> -n <ns> -o jsonpath='{.status.parents[0].conditions[0].reason}'   # Accepted
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://<имя>/    # 301 https://<имя>/
curl -s -o /dev/null -w '%{http_code}\n' https://<имя>/
```

HTTPRoute без листенера со своим `hostname` получает `Accepted=False (NoMatchingListenerHostname)` — сайт не работает ни по HTTP, ни по HTTPS.

## Как убрать домен

Сначала проверить, что имя не используется IngressRoute из [`platform/infra-routes`](../infra-routes/README.md): листенеры `avatars.*` и `docs.*` нужны им ради сертификата, хотя HTTPRoute на них нет.

Удалить элемент из `https.listeners` и `helm upgrade`. `Certificate` и Secret cert-manager **не удаляет** — убрать руками, если Secret больше никем не используется: `kubectl delete certificate,secret <secret> -n infra`.

## Редирект HTTP → HTTPS

`https-redirect` — HTTPRoute без `hostnames` на листенере `http`, отвечает `301` на `https://` для любого имени. Листенер `http` принимает маршруты только из `infra` (`http.allowedRoutesFrom: Same`): HTTPRoute приложений обычно указывают Gateway без `sectionName` и иначе цепляются ко всем листенерам, включая `http`, — маршрут с `hostname` по правилам Gateway API приоритетнее редиректа без `hostname`, и сайт открывается по HTTP без шифрования (так было до 2026-10-22). Временные HTTPRoute cert-manager для HTTP-01 создаются в namespace сертификата — `infra`, поэтому проходят.

## Установка и обновление

```bash
cd /mnt/d/Gitlab/JoinRPG/joinrpg-helms/platform/gateway
helm lint .
helm template gateway . -n infra | kubectl diff -n infra -f -
helm upgrade --install gateway . -n infra --dry-run=server

helm upgrade --install gateway . -n infra
```

В `kubectl diff` листенеры, которых нет в `values.yaml`, видны как удаляемые — значит, Gateway поправили в обход чарта: перенести листенер в `values.yaml`, а не применять.

## Что нужно знать

- Порядок листенеров в `values.yaml` на работу не влияет — он совпадает с живым объектом на момент переноса, чтобы diff был пустым.
- `helm uninstall gateway` удаляет Gateway: все сайты перестают открываться, cert-manager перестаёт продлевать сертификаты. Сами `Certificate` и Secret остаются.
- `https-redirect` отключается `httpsRedirect: false` — тогда по HTTP никто не отвечает (кроме HTTP-01 cert-manager).
