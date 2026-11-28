function getEventData(event) {
  return event?.data && typeof event.data === "object" ? event.data : {};
}

export function normalizeEvent(event, env) {
  const data = getEventData(event);
  const message = data.message && typeof data.message === "object" ? data.message : data;
  const author = data.author ?? message.author ?? data.user ?? {};
  const content = String(data.content ?? data.text ?? message.content ?? message.text ?? "");
  const threadId = data.thread_id ?? data.room_id ?? message.thread_id ?? message.room_id;
  const messageId = data.message_id ?? message.id ?? data.id;
  const eventType = String(event?.type ?? "").toLowerCase();
  const mentions = Array.isArray(data.mentions) ? data.mentions : [];
  const botId = String(env.ANARCHY_BOT_ID ?? "");
  const isOwnMessage = author?.is_bot === true || author?.bot === true || (botId && String(author?.id ?? "") === botId);
  const prefix = String(env.ANARCHY_COMMAND_PREFIX ?? "!");
  const isCommand = Boolean(prefix && content.trimStart().startsWith(prefix));
  const isMentioned = Boolean(data.is_mention === true || data.mentioned === true || (botId && mentions.some((item) => String(item?.id ?? "") === botId)));
  const isReply = Boolean(data.is_reply === true || data.reply_to || data.reply_to_message_id || message.reply_to);
  const explicitlyAddressed = ["command", "mention", "reply"].includes(eventType);
  const genericMessageAddressed = ["message", "message.created", "message_created"].includes(eventType) && (isCommand || isMentioned || isReply);
  return { eventType, data, author, content, threadId: threadId ? String(threadId) : "", messageId: messageId ? String(messageId) : "", isOwnMessage, shouldRespond: explicitlyAddressed || genericMessageAddressed };
}

export function directReply(content, messageId) {
  const reply = { content };
  if (messageId) reply.reply_to = messageId;
  return { reply };
}

export async function sendMessageThroughApi({ content, threadId, messageId }, env) {
  if (!env.ANARCHY_API_URL || !env.ANARCHY_BOT_TOKEN) throw new Error("ANARCHY_API_URL and ANARCHY_BOT_TOKEN are required for api reply mode");
  if (!threadId) throw new Error("The event did not include a room/thread id");
  const baseUrl = new URL(env.ANARCHY_API_URL);
  const routeTemplate = env.ANARCHY_SEND_MESSAGE_PATH || "/rooms/{thread_id}/messages";
  const route = routeTemplate.replace("{thread_id}", encodeURIComponent(threadId)).replace(/^\/+/, "");
  const basePath = `${baseUrl.pathname.replace(/\/+$/, "")}/`.replace(/^\/?$/, "/");
  const targetUrl = new URL(route, `${baseUrl.origin}${basePath}`);
  const authScheme = env.ANARCHY_AUTH_SCHEME || "Bot";
  const body = { content };
  if (messageId) body.reply_to = messageId;
  const response = await fetch(targetUrl, {
    method: "POST",
    headers: { Authorization: `${authScheme} ${env.ANARCHY_BOT_TOKEN}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`Anarchy message API returned HTTP ${response.status}`);
}
