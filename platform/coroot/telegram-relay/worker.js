// Реле уведомлений Coroot → Telegram (Cloudflare Worker).
//
// Зачем: из кластера joinrpg api.telegram.org недоступен, а токен бота в URL
// Coroot пишет в лог при любой ошибке отправки. Coroot шлёт сюда только текст
// и ключ реле в заголовке; токен и chat_id хранятся в секретах воркера.
//
// Секреты воркера (wrangler secret put): BOT_TOKEN, CHAT_ID, RELAY_KEY.
// Ничего не логирует.

const TELEGRAM_TEXT_LIMIT = 4096;

function keysEqual(a, b) {
  const enc = new TextEncoder();
  const x = enc.encode(a);
  const y = enc.encode(b);
  if (x.byteLength !== y.byteLength) return false;
  return crypto.subtle.timingSafeEqual(x, y);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method !== "POST" || url.pathname !== "/sendMessage") {
      return new Response("not found", { status: 404 });
    }

    const key = request.headers.get("X-Relay-Key") || "";
    if (!env.RELAY_KEY || !keysEqual(key, env.RELAY_KEY)) {
      return new Response("forbidden", { status: 403 });
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return new Response("bad json", { status: 400 });
    }
    if (typeof body.text !== "string" || body.text === "") {
      return new Response("text required", { status: 400 });
    }

    // chat_id — только из секрета: через реле нельзя писать в другие чаты.
    const payload = {
      chat_id: env.CHAT_ID,
      text: body.text.slice(0, TELEGRAM_TEXT_LIMIT),
      disable_web_page_preview: true,
    };
    const resp = await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    // Ответ Telegram ({"ok":…,"description":…}) токена не содержит.
    return new Response(await resp.text(), {
      status: resp.status,
      headers: { "Content-Type": "application/json" },
    });
  },
};
