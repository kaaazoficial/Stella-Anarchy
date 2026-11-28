import { verifyWebhook } from "./signature.js";
import { generateReply } from "./ai.js";
import { directReply, normalizeEvent, sendMessageThroughApi } from "./platform.js";

const MAX_BODY_BYTES = 32 * 1024;
const FALLBACK_REPLY = "Ahora mismo no pude procesar ese mensaje. Prueba de nuevo en un momento.";

function json(data, status = 200) {
  return Response.json(data, { status, headers: { "Cache-Control": "no-store" } });
}

async function processAndSend(event, normalized, env) {
  try {
    const content = await generateReply(event, env);
    if (!content) return;
    await sendMessageThroughApi({ content, threadId: normalized.threadId, messageId: normalized.messageId }, env);
  } catch (error) {
    console.error("[anarchy] background reply failed:", error?.message ?? "unknown error");
    try {
      await sendMessageThroughApi({ content: FALLBACK_REPLY, threadId: normalized.threadId, messageId: normalized.messageId }, env);
    } catch (fallbackError) {
      console.error("[anarchy] fallback delivery failed:", fallbackError?.message ?? "unknown error");
    }
  }
}

async function handleWebhook(request, env, ctx) {
  const declaredLength = Number(request.headers.get("content-length") || 0);
  if (declaredLength > MAX_BODY_BYTES) return json({ error: "payload_too_large" }, 413);
  const rawBody = new Uint8Array(await request.arrayBuffer());
  if (rawBody.byteLength > MAX_BODY_BYTES) return json({ error: "payload_too_large" }, 413);
  if (!await verifyWebhook(request, rawBody, env)) return json({ error: "invalid_signature" }, 401);
  let event;
  try { event = JSON.parse(new TextDecoder().decode(rawBody)); }
  catch { return json({ error: "invalid_json" }, 400); }
  if (event?.type === "ping") {
    const nonce = event?.data?.nonce;
    return json(nonce ? { nonce } : { ok: true });
  }
  const normalized = normalizeEvent(event, env);
  if (!normalized.shouldRespond || normalized.isOwnMessage) return json({ ok: true });
  const replyMode = String(env.ANARCHY_REPLY_MODE || "direct").toLowerCase();
  if (replyMode === "api") {
    if (!ctx || typeof ctx.waitUntil !== "function") return json({ error: "background_execution_unavailable" }, 503);
    ctx.waitUntil(processAndSend(event, normalized, env));
    return json({ accepted: true });
  }
  try {
    const content = await generateReply(event, env);
    if (!content) return json({ ok: true });
    return json(directReply(content, normalized.messageId));
  } catch (error) {
    console.error("[anarchy] AI reply failed:", error?.message ?? "unknown error");
    return json(directReply(FALLBACK_REPLY, normalized.messageId));
  }
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") return json({ status: "ok", service: "anarchy-worker" });
    if (request.method === "GET" && url.pathname === "/") return new Response("Anarchy Worker is running.", { headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" } });
    if (request.method === "POST" && url.pathname === "/webhook") {
      if (!env.ANARCHY_WEBHOOK_SECRET) return json({ error: "webhook_not_configured" }, 503);
      return handleWebhook(request, env, ctx);
    }
    return json({ error: "not_found" }, 404);
  },
};
