# fluent-bit

Сбор логов контейнеров кластера `joinrpg` и отправка в сервис [Логи Селектела](https://docs.selectel.ru/logs/about-logs/).

Обёртка над официальным чартом [`fluent/fluent-bit`](https://github.com/fluent/helm-charts/tree/main/charts/fluent-bit), версия зависимости — в [`Chart.yaml`](Chart.yaml), настройки — в [`values.yaml`](values.yaml) под ключом `fluent-bit:`.

| | |
|---|---|
| Релиз | `fluent-bit` |
| Namespace | `infra` |
| Вид | DaemonSet — по поду на каждой ноде |
| Какие логи | stdout/stderr контейнеров в namespace `kogda-dev`, `kogda-prod`, `larp-dictionary` |
| Куда | сервис Логи, пул `ru-6`, API `ru-6.logs.selcloud.ru` |
| Группа логов | `k8s/<namespace>` (создаётся автоматически) |
| Поток | `<pod>.<container>` |
| Хранение | 30 дней (настройка группы в панели Селектела) |

Записи, для которых не удалось определить namespace или под, попадают в группу `k8s/unmatched`, поток `unmatched`. Если там что-то появляется — смотреть логи fluent-bit.

## Доступ к сервису Логи

Fluent Bit пишет в Логи через API, совместимый с CloudWatch Logs, под отдельным сервисным пользователем Селектела с ролью `logs.writer` в проекте (только запись). Ключи — Access Key ID и Secret Access Key из S3-ключа этого пользователя. Они лежат в Secret `infra/fluent-bit-selectel-logs`, который создаётся вручную: в репозитории ключей нет.

Создать или заменить Secret (bash, WSL; ключи вводятся без эха и не попадают в историю):

```bash
read -rsp 'Access Key ID: ' AK; echo
read -rsp 'Secret Access Key: ' SK; echo
kubectl create secret generic fluent-bit-selectel-logs -n infra \
  --from-literal=AWS_ACCESS_KEY_ID="$AK" \
  --from-literal=AWS_SECRET_ACCESS_KEY="$SK" \
  --dry-run=client -o yaml | kubectl apply -f -
unset AK SK
```

После замены ключей — перезапустить поды: `kubectl rollout restart ds/fluent-bit -n infra`.

## Установка и обновление

Из каталога чарта, `KUBECONFIG` указывает на кластер `joinrpg`:

```bash
cd platform/fluent-bit
helm repo add fluent https://fluent.github.io/helm-charts   # один раз
helm dependency build                                       # подчарт по Chart.lock

helm lint .
helm template fluent-bit . -n infra > /tmp/fluent-bit.yaml  # посмотреть, что будет создано
helm upgrade --install fluent-bit . -n infra --dry-run=server

helm upgrade --install fluent-bit . -n infra
```

Secret `fluent-bit-selectel-logs` должен существовать до установки, иначе поды не стартуют (`CreateContainerConfigError`).

## Проверка

```bash
kubectl get ds,pods -n infra -o wide          # по поду на каждой ноде, все Running
kubectl logs -n infra ds/fluent-bit --tail=50 # нет ошибок cloudwatch / AccessDenied / retry

# счётчики пода: input.records, output.proc_records, output.errors
kubectl get --raw "/api/v1/namespaces/infra/pods/<pod>:2020/proxy/api/v1/metrics"
```

Файлы, найденные при старте, читаются с конца — отправляются только новые строки. Пока приложение ничего не пишет, счётчики нулевые, а группа и поток не создаются. При первой записи в логе fluent-bit появляются строки `Created log group k8s/<namespace>` и `Created log stream ...`.

Группы `k8s/<namespace>` и потоки видны в панели Селектела, раздел «Логи», или через AWS CLI ключом пользователя с правом чтения (`logs.viewer`, `reader` или `member`; ключом `logs.writer` читать нельзя):

```bash
aws logs describe-log-groups --endpoint-url https://ru-6.logs.selcloud.ru --region ru-6
```

## Как добавить namespace

В `values.yaml`, `config.inputs`, в `Path` дописать через запятую `/var/log/containers/*_<namespace>_*.log` и выполнить `helm upgrade` (раздел выше). Группа `k8s/<namespace>` создастся сама при первой записи.

## Что нужно знать

- В Селектеле containerd, а не Docker: логи лежат в `/var/log/pods`, `/var/log/containers/*.log` — ссылки на них, формат CRI (`multiline.parser cri`). Каталог `/var/lib/docker/containers` не монтируется.
- Позиции чтения хранятся на ноде в `/var/log/fluent-bit-kube.db`: после перезапуска пода логи не дублируются и не теряются. При замене ноды история её логов, ещё не отправленная, пропадает вместе с нодой.
- JSON-логи приложений разворачиваются в поля записи (`Merge_Log On`), в событие попадает вся запись вместе с метаданными `kubernetes` (под, контейнер, namespace, метки). Аннотации подов не отправляются.
- Под может отключить сбор своих логов аннотацией `fluentbit.io/exclude: "true"`.
- В шаблоне имени потока после переменной допустимы только `.` или `,` — поэтому `<pod>.<container>`, а не `<pod>/<container>`.
