import test from "node:test";
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { verifyWebhook } from "../src/signature.js";

function signedRequest(body, secret, timestamp = String(Math.floor(Date.now() / 1000))) {
  const signature = createHmac("sha256", secret).update(`${timestamp}.${body}`).digest("hex");
  return new Request("https://worker.example/webhook", { method: "POST", headers: { "X-Anarchy-Timestamp": timestamp, "X-Anarchy-Signature": signature }, body });
}

test("accepts a valid timestamped HMAC-SHA256 signature", async () => {
  const body = '{"type":"ping","data":{"nonce":"abc"}}';
  assert.equal(await verifyWebhook(signedRequest(body, "secret"), body, { ANARCHY_WEBHOOK_SECRET: "secret" }), true);
});

test("rejects a signature that does not match the raw body", async () => {
  const body = '{"type":"ping"}';
  assert.equal(await verifyWebhook(signedRequest(body, "secret"), `${body} `, { ANARCHY_WEBHOOK_SECRET: "secret" }), false);
});

test("rejects a stale timestamp", async () => {
  const body = '{"type":"ping"}';
  const oldTimestamp = String(Math.floor(Date.now() / 1000) - 900);
  assert.equal(await verifyWebhook(signedRequest(body, "secret", oldTimestamp), body, { ANARCHY_WEBHOOK_SECRET: "secret" }), false);
});

test("supports raw-body signatures when explicitly configured", async () => {
  const body = '{"type":"ping"}';
  const signature = createHmac("sha256", "secret").update(body).digest("hex");
  const request = new Request("https://worker.example/webhook", { method: "POST", headers: { "X-Anarchy-Signature": signature }, body });
  assert.equal(await verifyWebhook(request, body, { ANARCHY_WEBHOOK_SECRET: "secret", ANARCHY_SIGNATURE_FORMAT: "body" }), true);
});
