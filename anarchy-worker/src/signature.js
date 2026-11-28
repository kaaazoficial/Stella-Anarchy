const encoder = new TextEncoder();

function decodeSignature(value) {
  const cleaned = String(value ?? "").trim().replace(/^sha256=/i, "");
  if (!cleaned) return null;
  if (/^[0-9a-f]+$/i.test(cleaned) && cleaned.length % 2 === 0) {
    const bytes = new Uint8Array(cleaned.length / 2);
    for (let i = 0; i < bytes.length; i += 1) bytes[i] = Number.parseInt(cleaned.slice(i * 2, i * 2 + 2), 16);
    return bytes;
  }
  try {
    const binary = atob(cleaned);
    return Uint8Array.from(binary, (character) => character.charCodeAt(0));
  } catch {
    return null;
  }
}

/** Verify HMAC-SHA256 over the untouched webhook body. */
export async function verifyWebhook(request, rawBody, env, now = Date.now()) {
  const secret = env.ANARCHY_WEBHOOK_SECRET;
  if (!secret) return false;
  const signature = request.headers.get(env.ANARCHY_SIGNATURE_HEADER || "X-Anarchy-Signature");
  if (!signature) return false;
  const format = String(env.ANARCHY_SIGNATURE_FORMAT || "timestamp-dot-body").toLowerCase();
  const bodyBytes = rawBody instanceof Uint8Array ? rawBody : encoder.encode(String(rawBody));
  let signedBytes = bodyBytes;
  if (format === "timestamp-dot-body") {
    const timestamp = request.headers.get(env.ANARCHY_TIMESTAMP_HEADER || "X-Anarchy-Timestamp");
    if (!timestamp || !/^\d+$/.test(timestamp)) return false;
    const seconds = Number(timestamp);
    if (!Number.isSafeInteger(seconds) || Math.abs(now / 1000 - seconds) > 300) return false;
    const prefix = encoder.encode(`${timestamp}.`);
    signedBytes = new Uint8Array(prefix.byteLength + bodyBytes.byteLength);
    signedBytes.set(prefix, 0);
    signedBytes.set(bodyBytes, prefix.byteLength);
  } else if (format !== "body") return false;

  const signatureBytes = decodeSignature(signature);
  if (!signatureBytes || signatureBytes.length !== 32) return false;
  try {
    const key = await crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["verify"]);
    return await crypto.subtle.verify("HMAC", key, signatureBytes, signedBytes);
  } catch {
    return false;
  }
}
