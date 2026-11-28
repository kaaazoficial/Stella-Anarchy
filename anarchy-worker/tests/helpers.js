import { createHmac } from "node:crypto";

export function makeWebhookRequest(event, secret = "test-secret", overrides = {}) {
  const body = JSON.stringify(event);
  const timestamp = String(Math.floor(Date.now() / 1000));
  const signature = createHmac("sha256", secret).update(`${timestamp}.${body}`).digest("hex");
  const headers = { "Content-Type": "application/json", "X-Anarchy-Timestamp": timestamp, "X-Anarchy-Signature": `sha256=${signature}`, ...overrides };
  return new Request("https://worker.example/webhook", { method: "POST", headers, body });
}

export function testEnv(overrides = {}) {
  return { ANARCHY_WEBHOOK_SECRET: "test-secret", ANARCHY_BOT_ID: "bot-1", ANARCHY_COMMAND_PREFIX: "!", ANARCHY_REPLY_MODE: "direct", AI: { run: async () => ({ response: "¡Hola!" }) }, ...overrides };
}
