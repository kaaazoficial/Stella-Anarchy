const MODEL_DEFAULT = "@cf/zai-org/glm-4.7-flash";
const MAX_BODY_BYTES = 96 * 1024;
const MAX_MESSAGES = 24;
const MAX_MESSAGE_CHARS = 18_000;
const encoder = new TextEncoder();

function json(data, status = 200) {
  return Response.json(data, {
    status,
    headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" },
  });
}

function constantTimeEqual(left, right) {
  const a = encoder.encode(String(left ?? ""));
  const b = encoder.encode(String(right ?? ""));
  let difference = a.length ^ b.length;
  const length = Math.max(a.length, b.length);
  for (let index = 0; index < length; index += 1) {
    difference |= (a[index] ?? 0) ^ (b[index] ?? 0);
  }
  return difference === 0;
}

function isAuthorized(request, env) {
  const expected = String(env.STELLA_GATEWAY_TOKEN ?? "");
  const authorization = String(request.headers.get("Authorization") ?? "");
  if (!expected || !authorization.startsWith("Bearer ")) return false;
  return constantTimeEqual(authorization.slice(7).trim(), expected);
}

function normalizeMessages(value) {
  if (!Array.isArray(value) || value.length < 1 || value.length > MAX_MESSAGES) return null;
  const messages = [];
  let totalChars = 0;
  for (const item of value) {
    if (!item || typeof item !== "object") return null;
    const role = String(item.role ?? "");
    if (!["system", "user", "assistant"].includes(role)) return null;
    if (typeof item.content !== "string" || !item.content.trim()) return null;
    const content = item.content;
    if (content.length > MAX_MESSAGE_CHARS) return null;
    totalChars += content.length;
    if (totalChars > MAX_BODY_BYTES / 2) return null;
    messages.push({ role, content });
  }
  if (!messages.some((item) => item.role === "user")) return null;
  return messages;
}

function extractText(result) {
  if (typeof result === "string") return result;
  if (!result || typeof result !== "object") return "";
  const candidate = result.response ?? result.output_text ?? result.text ?? result.result
    ?? result.choices?.[0]?.message?.content ?? result.choices?.[0]?.text;
  if (typeof candidate === "string") return candidate;
  if (Array.isArray(candidate)) {
    return candidate.map((part) => typeof part === "string" ? part : part?.text ?? "").join("\n");
  }
  if (candidate && typeof candidate === "object") {
    if (typeof candidate.content === "string") return candidate.content;
    if (typeof candidate.text === "string") return candidate.text;
  }
  return "";
}

function boundedInteger(value, fallback, minimum, maximum) {
  const parsed = Number(value);
  return Number.isFinite(parsed)
    ? Math.max(minimum, Math.min(maximum, Math.trunc(parsed)))
    : fallback;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && (url.pathname === "/" || url.pathname === "/health")) {
      return json({
        status: "ok",
        service: "stella-cloudflare-ai-gateway",
        ai_binding_configured: Boolean(env.AI && typeof env.AI.run === "function"),
        gateway_auth_configured: Boolean(env.STELLA_GATEWAY_TOKEN),
      });
    }
    if (url.pathname !== "/internal/infer") return json({ error: "not_found" }, 404);
    if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
    if (!env.STELLA_GATEWAY_TOKEN) return json({ error: "gateway_not_configured" }, 503);
    if (!isAuthorized(request, env)) return json({ error: "unauthorized" }, 401);
    if (!env.AI || typeof env.AI.run !== "function") {
      return json({ error: "workers_ai_binding_missing" }, 503);
    }

    const declaredLength = Number(request.headers.get("content-length") || 0);
    if (declaredLength > MAX_BODY_BYTES) return json({ error: "payload_too_large" }, 413);
    let payload;
    try {
      const rawBody = await request.arrayBuffer();
      if (rawBody.byteLength > MAX_BODY_BYTES) return json({ error: "payload_too_large" }, 413);
      payload = JSON.parse(new TextDecoder().decode(rawBody));
    } catch {
      return json({ error: "invalid_json" }, 400);
    }

    const messages = normalizeMessages(payload?.messages);
    if (!messages) return json({ error: "invalid_messages" }, 400);
    const model = String(env.AI_MODEL || MODEL_DEFAULT).slice(0, 120);
    const maxCompletionTokens = boundedInteger(
      payload.max_completion_tokens ?? payload.max_tokens,
      900,
      128,
      2048,
    );
    const temperature = Number.isFinite(Number(payload.temperature))
      ? Math.max(0, Math.min(2, Number(payload.temperature)))
      : 0.6;

    try {
      const result = await env.AI.run(model, {
        messages,
        max_completion_tokens: maxCompletionTokens,
        temperature,
        response_format: { type: "json_object" },
      });
      const content = extractText(result).trim();
      if (!content) return json({ error: "empty_model_response" }, 502);
      return json({
        id: crypto.randomUUID(),
        object: "chat.completion",
        model,
        choices: [{
          index: 0,
          message: { role: "assistant", content },
          finish_reason: "stop",
        }],
        usage: result && typeof result === "object" && result.usage
          ? result.usage
          : {},
      });
    } catch (error) {
      console.error("[stella-gateway] inference failed:", error?.name ?? "unknown_error");
      return json({ error: "workers_ai_inference_failed" }, 502);
    }
  },
};
