# cert-manager-issuers

ClusterIssuer для [`cert-manager`](../cert-manager/README.md) кластера `joinrpg`. Шаблон — [`templates/clusterissuer.yaml`](templates/clusterissuer.yaml), параметры — [`values.yaml`](values.yaml).

| | |
|---|---|
| Релиз | `cert-manager-issuers` |
| Namespace релиза | `cert-manager` (сам ClusterIssuer — кластерный) |
| ClusterIssuer | `letsencrypt-prod` — Let's Encrypt (production), ACME HTTP-01 |
| Проверка HTTP-01 | временный HTTPRoute на листенер `http` Gateway `infra/main` |
| Ключ ACME-аккаунта | Secret `cert-manager/letsencrypt-prod-key`, создаёт cert-manager |

Отдельным релизом, а не в чарте `cert-manager`: ClusterIssuer — CR, а CRD ставит релиз `cert-manager`. В одном релизе на чистом кластере установка падает (`no matches for kind ClusterIssuer`).

История: `letsencrypt-prod` создан руками (`kubectl apply`) 2026-10-13, передан релизу 2026-10-22 (аннотации `meta.helm.sh/*`, метка `app.kubernetes.io/managed-by=Helm`) без изменения `spec` — аккаунт ACME и выпущенные сертификаты прежние.

## Email аккаунта ACME

Репозиторий публичный, поэтому email в [`values.yaml`](values.yaml) пустой и передаётся при каждой установке через `--set acme.email=<адрес>`. Без него рендер падает с ошибкой `acme.email обязателен`. Текущий адрес можно взять из кластера (см. ниже).

## Установка и обновление

Ставится после релиза `cert-manager`. Из каталога чарта, `KUBECONFIG` указывает на кластер `joinrpg`:

```bash
cd /mnt/d/Gitlab/JoinRPG/joinrpg-helms/platform/cert-manager-issuers
E=$(kubectl get clusterissuer letsencrypt-prod -o jsonpath='{.spec.acme.email}')   # на чистом кластере — задать руками

helm lint . --set acme.email="$E"
helm template cert-manager-issuers . -n cert-manager --set acme.email="$E" | kubectl diff -f -
helm upgrade --install cert-manager-issuers . -n cert-manager --set acme.email="$E" --dry-run=server

helm upgrade --install cert-manager-issuers . -n cert-manager --set acme.email="$E"
unset E
```

## Проверка

```bash
kubectl get clusterissuer letsencrypt-prod -o jsonpath='{.status.conditions[0].reason}{"\n"}'   # ACMEAccountRegistered
kubectl get certificate -A
```

## Что нужно знать

- Смена `privateKeySecretName` или `acme.server` — регистрация нового аккаунта ACME. Для пробы использовать staging Let's Encrypt отдельным ClusterIssuer, а не менять этот: у production жёсткие лимиты на выпуск.
- Смена email на том же ключе — обновление контакта аккаунта, сертификаты не перевыпускаются.
- `helm uninstall cert-manager-issuers` удаляет ClusterIssuer: продление всех сертификатов остановится (выпущенные продолжат работать до истечения).
