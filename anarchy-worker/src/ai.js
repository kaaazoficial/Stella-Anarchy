const MODEL_DEFAULT = "@cf/zai-org/glm-4.7-flash";
const MAX_USER_TEXT = 3_000;
const MAX_CONTEXT_CHARS = 2_000;
const MAX_REPLY_CHARS = 1_700;

function extractText(result) {
  if (typeof result === "string") return result;
  if (!result || typeof result !== "object") return "";
  const candidate = result.response ?? result.output_text ?? result.text ?? result.result ?? result.choices?.[0]?.message?.content ?? result.choices?.[0]?.text;
  if (typeof candidate === "string") return candidate;
  if (Array.isArray(candidate)) return candidate.map((part) => typeof part === "string" ? part : part?.text ?? "").join("");
  if (candidate && typeof candidate === "object") return candidate.content ?? candidate.text ?? "";
  return "";
}

function collectContext(data) {
  const candidates = Array.isArray(data.context) ? data.context : Array.isArray(data.recent_messages) ? data.recent_messages : [];
  return candidates.slice(-4).map((item) => {
    const name = item?.author?.nickname ?? item?.nickname ?? "Miembro";
    const content = item?.content ?? item?.text ?? "";
    return `${String(name).slice(0, 40)}: ${String(content).slice(0, 500)}`;
  }).join("\n").slice(-MAX_CONTEXT_CHARS);
}

export async function generateReply(event, env) {
  if (!env.AI || typeof env.AI.run !== "function") throw new Error("Workers AI binding AI is missing");
  const data = event.data ?? {};
  const message = data.message ?? data;
  const author = data.author ?? message.author ?? data.user ?? {};
  const nickname = String(author.nickname ?? author.name ?? "alguien").slice(0, 60);
  let userText = String(data.content ?? data.text ?? message.content ?? message.text ?? "").trim();
  const prefix = String(env.ANARCHY_COMMAND_PREFIX ?? "!");
  if (prefix && userText.startsWith(prefix)) userText = userText.slice(prefix.length).trim();
  userText = userText.slice(0, MAX_USER_TEXT);
  if (!userText) return "";
  const roomContext = collectContext(data);
  const systemPrompt = String(env.ANARCHY_SYSTEM_PROMPT || "Eres un participante amable y espontáneo de una comunidad en español. Conversa con naturalidad, entiende el contexto antes de responder, evita sonar como un manual y no digas que eres una inteligencia artificial. No inventes datos personales ni afirmes haber hecho acciones fuera del chat. Responde de forma útil y proporcional al mensaje, sin listas innecesarias.").slice(0, 3_000);
  const prompt = [
    `La persona que escribió el mensaje se llama ${nickname}.`,
    roomContext ? `Contexto reciente entregado por la plataforma (solo para esta respuesta):\n${roomContext}` : "",
    `Mensaje actual:\n${userText}`,
  ].filter(Boolean).join("\n\n");
  const output = await env.AI.run(env.AI_MODEL || MODEL_DEFAULT, {
    messages: [{ role: "system", content: systemPrompt }, { role: "user", content: prompt }],
    max_completion_tokens: 384,
    temperature: 0.7,
  });
  const reply = extractText(output).trim();
  if (!reply) throw new Error("Workers AI returned an empty response");
  return reply.slice(0, MAX_REPLY_CHARS);
}
