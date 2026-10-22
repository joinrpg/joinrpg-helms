# cert-manager

Выпуск и продление TLS-сертификатов Let's Encrypt для Gateway кластера `joinrpg`.

Обёртка над официальным чартом [`jetstack/cert-manager`](https://cert-manager.io/docs/installation/helm/), версия зависимости — в [`Chart.yaml`](Chart.yaml), настройки — в [`values.yaml`](values.yaml) под ключом `cert-manager:`. ClusterIssuer — отдельным релизом, [`platform/cert-manager-issuers`](../cert-manager-issuers/README.md).

| | |
|---|---|
| Релиз | `cert-manager` |
| Namespace | `cert-manager` |
| Состав | контроллер, cainjector, webhook; CRD cert-manager (`crds.enabled`) |
| Откуда сертификаты | gateway-shim (`config.enableGatewayAPI`): по аннотации `cert-manager.io/cluster-issuer: letsencrypt-prod` на Gateway `infra/main` cert-manager сам создаёт `Certificate` на каждый уникальный `certificateRefs` HTTPS-листенеров |
| Метрики | порт 9402 контроллера, собирает Coroot по аннотациям `coroot.com/*` (правила `certificate-expiry`, `certificate-not-ready` — [`platform/coroot`](../coroot/README.md)) |

История: релиз поставлен руками `helm install` 2026-10-13, в репозиторий перенесён 2026-10-22 тем же именем и в тот же namespace — `helm upgrade` поверх живого релиза (revision 3), объекты не менялись.

## Установка и обновление

Из каталога чарта, `KUBECONFIG` указывает на кластер `joinrpg`. Путь к чарту — абсолютный: относительный `cd` из соседнего каталога может молча не сработать, и helm поставит чужой чарт.

```bash
cd /mnt/d/Gitlab/JoinRPG/joinrpg-helms/platform/cert-manager
helm repo add jetstack https://charts.jetstack.io   # один раз
helm dependency build                               # подчарт по Chart.lock

helm lint .
helm template cert-manager . -n cert-manager --no-hooks | kubectl diff -f -
helm upgrade --install cert-manager . -n cert-manager --dry-run=server

helm upgrade --install cert-manager . -n cert-manager
```

`--reuse-values` не нужен и не используется: все values — в [`values.yaml`](values.yaml). Правка values только через репозиторий, иначе следующий `upgrade` её снимет (в том числе аннотации `coroot.com/*` — правила по сертификатам в Coroot замолчат без ошибок).

На чистом кластере порядок: `cert-manager` → `cert-manager-issuers` (ClusterIssuer — CR, его CRD ставит этот релиз).

## Проверка

```bash
kubectl get pods -n cert-manager                 # cert-manager, cainjector, webhook — Running
kubectl get clusterissuer                        # letsencrypt-prod READY True
kubectl get certificate -A                       # все READY True
kubectl logs -n cert-manager deploy/cert-manager --tail=50
```

## Обновление версии cert-manager

Поднять `version` зависимости в [`Chart.yaml`](Chart.yaml), `helm dependency update`, прочитать [заметки к выпуску](https://cert-manager.io/docs/releases/) (изменения CRD и values), дальше — как в «Установке и обновлении». В `kubectl diff` будут CRD и образы — это ожидаемо, остальное разобрать до `upgrade`.

## Что нужно знать

- CRD ставятся этим релизом с аннотацией `helm.sh/resource-policy: keep`: `helm uninstall cert-manager` их не удаляет, а значит, не удаляет Certificate и ClusterIssuer. Удалять CRD руками — значит удалить все сертификаты кластера.
- Хуки `startupapicheck` (`post-install`) выполняются только при первой установке; `helm get manifest` их не показывает, `helm template` — показывает (поэтому в `kubectl diff` — `--no-hooks`).
- Новый HTTPS-листенер на Gateway `infra/main` со своим `certificateRefs` — cert-manager выпустит сертификат сам, `Certificate` руками не создаются. Выпуск — HTTP-01 через листенер `http` того же Gateway (параметры — в `cert-manager-issuers`).
- Gateway `infra/main` в этом репозитории пока нет — правится руками.
