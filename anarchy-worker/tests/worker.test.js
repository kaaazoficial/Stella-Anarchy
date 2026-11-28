import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";

const TOKEN = "gateway-test-secret";

function gatewayEnv(overrides = {}) {
  return {
    STELLA_GATEWAY_TOKEN: TOKEN,
    AI_MODEL: "@cf/zai-org/glm-4.7-flash",
    AI: { run: async () => ({ response: '{"answer":"Hola"}' }) },
    ...overrides,
  };
}

function inferRequest(payload, token = TOKEN, method = "POST") {
  const headers = { "Content-Type": "application/json" };
  if (token !== null) headers.Authorization = `Bearer ${token}`;
  return new Request("https://worker.example/internal/infer", {
    method,
    headers,
    body: method === "GET" ? undefined : JSON.stringify(payload),
  });
}

const samplePayload = {
  model: "model-sent-by-client-must-be-ignored",
  messages: [
    { role: "system", content: "Eres Stella. Devuelve JSON." },
    { role: "user", content: "Hola" },
  ],
  max_completion_tokens: 256,
  temperature: 0.4,
  response_format: { type: "json_object" },
};

test("health endpoint reports binding and gateway configuration without exposing secrets", async () => {
  const response = await worker.fetch(new Request("https://worker.example/health"), gatewayEnv());
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.service, "stella-cloudflare-ai-gateway");
  assert.equal(body.ai_binding_configured, true);
  assert.equal(body.gateway_auth_configured, true);
  assert.equal(JSON.stringify(body).includes(TOKEN), false);
});

test("inference requires a configured shared secret", async () => {
  const response = await worker.fetch(inferRequest(samplePayload), gatewayEnv({ STELLA_GATEWAY_TOKEN: "" }));
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: "gateway_not_configured" });
});

test("inference rejects missing and incorrect bearer secrets", async () => {
  const missing = await worker.fetch(inferRequest(samplePayload, null), gatewayEnv());
  const incorrect = await worker.fetch(inferRequest(samplePayload, "wrong-secret"), gatewayEnv());
  assert.equal(missing.status, 401);
  assert.equal(incorrect.status, 401);
});

test("inference calls the Workers AI binding and returns an OpenAI-compatible response", async () => {
  let captured;
  const env = gatewayEnv({
    AI: {
      run: async (model, input) => {
        captured = { model, input };
        return { response: '{"kind":"answer","answer":"Hola de vuelta","command":"","args":"","steps":[]}' };
      },
    },
  });
  const response = await worker.fetch(inferRequest(samplePayload), env);
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.object, "chat.completion");
  assert.equal(body.model, "@cf/zai-org/glm-4.7-flash");
  assert.equal(body.choices[0].message.role, "assistant");
  assert.match(body.choices[0].message.content, /Hola de vuelta/);
  assert.equal(captured.model, "@cf/zai-org/glm-4.7-flash");
  assert.deepEqual(captured.input.messages, samplePayload.messages);
  assert.equal(captured.input.max_completion_tokens, 256);
  assert.equal(captured.input.temperature, 0.4);
  assert.deepEqual(captured.input.response_format, { type: "json_object" });
});

test("inference clamps output tokens and ignores a caller-selected model", async () => {
  let captured;
  const env = gatewayEnv({ AI: { run: async (model, input) => {
    captured = { model, input };
    return { response: '{"answer":"ok"}' };
  } } });
  const response = await worker.fetch(inferRequest({
    ...samplePayload, max_completion_tokens: 50_000,
  }), env);
  assert.equal(response.status, 200);
  assert.equal(captured.model, "@cf/zai-org/glm-4.7-flash");
  assert.equal(captured.input.max_completion_tokens, 2048);
});

test("inference rejects invalid message roles before calling the model", async () => {
  let aiCalls = 0;
  const env = gatewayEnv({ AI: { run: async () => { aiCalls += 1; return { response: "{}" }; } } });
  const response = await worker.fetch(inferRequest({
    messages: [{ role: "tool", content: "do something" }],
  }), env);
  assert.equal(response.status, 400);
  assert.equal(aiCalls, 0);
});

test("inference rejects oversized request bodies", async () => {
  const huge = { messages: [{ role: "user", content: "x".repeat(100_000) }] };
  const response = await worker.fetch(inferRequest(huge), gatewayEnv());
  assert.equal(response.status, 413);
});

test("inference returns a gateway error when Workers AI returns no text", async () => {
  const env = gatewayEnv({ AI: { run: async () => ({ response: "" }) } });
  const response = await worker.fetch(inferRequest(samplePayload), env);
  assert.equal(response.status, 502);
  assert.deepEqual(await response.json(), { error: "empty_model_response" });
});

test("inference only accepts POST", async () => {
  const response = await worker.fetch(inferRequest(samplePayload, TOKEN, "GET"), gatewayEnv());
  assert.equal(response.status, 405);
  assert.deepEqual(await response.json(), { error: "method_not_allowed" });
});
