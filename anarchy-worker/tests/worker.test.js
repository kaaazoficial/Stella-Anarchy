import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";
import { makeWebhookRequest, testEnv } from "./helpers.js";

function commandEvent(overrides = {}) {
  return { id: "evt-1", type: "command", data: { message_id: "msg-1", thread_id: "room-1", community_id: "community-1", content: "!hola, ¿cómo estás?", author: { id: "user-1", nickname: "Luna" }, ...overrides } };
}

test("health endpoint returns service status", async () => {
  const response = await worker.fetch(new Request("https://worker.example/health"), {});
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { status: "ok", service: "anarchy-worker" });
});

test("ping echoes the nonce after signature verification", async () => {
  const response = await worker.fetch(makeWebhookRequest({ id: "evt-ping", type: "ping", data: { nonce: "n-123" } }), testEnv(), {});
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { nonce: "n-123" });
});

test("rejects unsigned or invalid webhook requests", async () => {
  const request = new Request("https://worker.example/webhook", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(commandEvent()) });
  assert.equal((await worker.fetch(request, testEnv(), {})).status, 401);
});

test("answers addressed commands through Workers AI", async () => {
  let aiCalls = 0;
  const env = testEnv({ AI: { run: async (model, input) => { aiCalls += 1; assert.equal(model, "@cf/zai-org/glm-4.7-flash"); assert.match(input.messages[1].content, /Luna/); assert.match(input.messages[1].content, /hola/); return { response: "¡Todo bien por aquí!" }; } } });
  const response = await worker.fetch(makeWebhookRequest(commandEvent()), env, {});
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { reply: { content: "¡Todo bien por aquí!", reply_to: "msg-1" } });
  assert.equal(aiCalls, 1);
});

test("does not answer an unaddressed room message", async () => {
  let aiCalls = 0;
  const env = testEnv({ AI: { run: async () => { aiCalls += 1; return { response: "No" }; } } });
  const event = { id: "evt-2", type: "message", data: { content: "Hola a todos", thread_id: "room-1", author: { id: "user-1", nickname: "Luna" } } };
  const response = await worker.fetch(makeWebhookRequest(event), env, {});
  assert.deepEqual(await response.json(), { ok: true });
  assert.equal(aiCalls, 0);
});

test("does not answer messages written by the bot itself", async () => {
  let aiCalls = 0;
  const env = testEnv({ AI: { run: async () => { aiCalls += 1; return { response: "No" }; } } });
  const response = await worker.fetch(makeWebhookRequest(commandEvent({ author: { id: "bot-1", nickname: "Anarchy", is_bot: true } })), env, {});
  assert.deepEqual(await response.json(), { ok: true });
  assert.equal(aiCalls, 0);
});

test("API reply mode acknowledges immediately and schedules delivery", async () => {
  let scheduled;
  const env = testEnv({ ANARCHY_REPLY_MODE: "api", ANARCHY_API_URL: "https://api.example/v1", ANARCHY_BOT_TOKEN: "token-test", AI: { run: async () => ({ response: "Respuesta desde API" }) } });
  const originalFetch = globalThis.fetch;
  let outgoingUrl = "";
  let outgoingHeaders;
  let outgoingBody;
  globalThis.fetch = async (url, init) => { outgoingUrl = String(url); outgoingHeaders = init.headers; outgoingBody = JSON.parse(init.body); return new Response(null, { status: 204 }); };
  const ctx = { waitUntil(promise) { scheduled = promise; } };
  try {
    const response = await worker.fetch(makeWebhookRequest(commandEvent()), env, ctx);
    assert.deepEqual(await response.json(), { accepted: true });
    assert.ok(scheduled instanceof Promise);
    await scheduled;
    assert.equal(outgoingUrl, "https://api.example/v1/rooms/room-1/messages");
    assert.equal(outgoingHeaders.Authorization, "Bot token-test");
    assert.deepEqual(outgoingBody, { content: "Respuesta desde API", reply_to: "msg-1" });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
