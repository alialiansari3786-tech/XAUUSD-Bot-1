/**
 * webhook-receiver/worker.js
 *
 * Cloudflare Worker that receives TradingView alert webhooks (from your
 * licensed Guardeer indicator's alertcondition()s) and relays them to your
 * Telegram bot. This does NOT touch or reproduce Guardeer's code - it just
 * catches the alert TEXT that TradingView sends when an alertcondition()
 * fires, and forwards it.
 *
 * SETUP:
 * 1. Free Cloudflare account -> Workers & Pages -> Create Worker.
 * 2. Paste this file's contents in as the worker script.
 * 3. In Worker Settings -> Variables, add two secrets:
 *      TELEGRAM_BOT_TOKEN = your bot token
 *      TELEGRAM_CHAT_ID   = your chat/user id
 * 4. Deploy - you'll get a URL like https://your-worker.your-subdomain.workers.dev
 * 5. In TradingView, on your Guardeer-loaded chart, create an Alert on any
 *    of Guardeer's alertcondition()s (Bullish BOS, Bearish CHoCH, EQH, FVG,
 *    OB touch, Accumulation/Distribution Zone, etc). Under "Notifications",
 *    enable "Webhook URL" and paste your Worker URL there.
 * 6. In the alert's "Message" box, write something identifying which signal
 *    fired, e.g.: {{ticker}} Bullish BOS on {{interval}} @ {{close}}
 *    (TradingView's {{placeholders}} get filled in automatically)
 *
 * You'll want one alert set up per alertcondition() you care about - repeat
 * step 5 for each (Bullish BOS, Bearish BOS, Bullish CHoCH, Bearish CHoCH,
 * EQH, EQL, Bullish FVG, Bearish FVG, OB touches, Zones, etc).
 */

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("Only POST accepted", { status: 405 });
    }

    let alertText;
    try {
      // TradingView sends the alert message as raw text (or JSON if you
      // configured a JSON message body in the alert - either works here).
      alertText = await request.text();
    } catch (err) {
      return new Response("Could not read request body", { status: 400 });
    }

    if (!alertText || alertText.trim().length === 0) {
      return new Response("Empty alert body", { status: 400 });
    }

    const telegramUrl = `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`;

    const message = `🔔 *Guardeer Alert*\n\n${alertText}`;

    const telegramResp = await fetch(telegramUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: env.TELEGRAM_CHAT_ID,
        text: message,
        parse_mode: "Markdown",
      }),
    });

    if (!telegramResp.ok) {
      const errText = await telegramResp.text();
      return new Response(`Telegram relay failed: ${errText}`, { status: 502 });
    }

    return new Response("OK", { status: 200 });
  },
};
