# telegram-relay

Cloudflare Worker `coroot-tg-relay` — реле уведомлений Coroot → Telegram.

Зачем:

- из кластера `joinrpg` (Селектел) `api.telegram.org` недоступен — таймаут; Cloudflare (`*.workers.dev`) доступен;
- Telegram принимает токен бота только в URL, а Coroot при любой ошибке отправки пишет URL в лог целиком — с токеном.

Coroot шлёт `POST https://coroot-tg-relay.joinrpg.workers.dev/sendMessage` с JSON `{"text": "…"}` и заголовком `X-Relay-Key`. Воркер сверяет ключ, подставляет токен и `chat_id` из своих секретов и пересылает в `sendMessage` Telegram. `chat_id` из запроса игнорируется — через реле нельзя писать в другие чаты. Воркер ничего не логирует.

| Секрет воркера | Что это |
|---|---|
| `BOT_TOKEN` | токен бота от BotFather |
| `CHAT_ID` | id чата-получателя |
| `RELAY_KEY` | общий ключ с Coroot (Secret `coroot-telegram`, ключ `relay-key`) |

Значения секретов в репозиторий не кладутся.

## Деплой (PowerShell, из этого каталога)

```powershell
cd platform\coroot\telegram-relay
npx wrangler@4.151.3 login                 # вход в аккаунт Cloudflare через браузер
npx wrangler@4.151.3 deploy                # печатает адрес https://coroot-tg-relay.joinrpg.workers.dev
                                           # (joinrpg — поддомен workers.dev аккаунта: Workers & Pages → Subdomain)

npx wrangler@4.151.3 secret put BOT_TOKEN  # ввод значения — по запросу, в историю не попадает
npx wrangler@4.151.3 secret put CHAT_ID
npx wrangler@4.151.3 secret put RELAY_KEY
```

Ключ реле — случайная строка, например:

```powershell
[Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLower()
```

## Проверка

```powershell
$url = 'https://coroot-tg-relay.joinrpg.workers.dev/sendMessage'
$key = Read-Host 'RELAY_KEY'
Invoke-RestMethod -Method Post -Uri $url -Headers @{ 'X-Relay-Key' = $key } -ContentType 'application/json; charset=utf-8' -Body '{"text":"Проверка реле Coroot"}'
# ожидается ok = True и сообщение в Telegram; без ключа или с неверным — 403
```

## Смена токена бота

`npx wrangler@4.151.3 secret put BOT_TOKEN` — новый токен; Coroot и кластер не трогаются.
