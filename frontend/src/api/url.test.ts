import test from "node:test";
import assert from "node:assert/strict";
import {
  buildApiUrl,
  buildWebSocketUrl,
  formatAuthConnectionError,
  normalizeApiBaseUrl,
  resolveApiOrigin
} from "./url";

test("normalizeApiBaseUrl trims whitespace and trailing slashes", () => {
  assert.equal(normalizeApiBaseUrl(" https://otternest.newpos.online/// "), "https://otternest.newpos.online");
  assert.equal(normalizeApiBaseUrl(""), "");
  assert.equal(normalizeApiBaseUrl(undefined), "");
});

test("resolveApiOrigin falls back to browser origin when VITE_API_BASE_URL is empty", () => {
  assert.equal(resolveApiOrigin("", "http://localhost:3000"), "http://localhost:3000");
  assert.equal(resolveApiOrigin("https://otternest.newpos.online", "http://localhost:3000"), "https://otternest.newpos.online");
});

test("buildApiUrl constructs /api/v1 path cleanly", () => {
  assert.equal(buildApiUrl("https://otternest.newpos.online", "/auth/status"), "https://otternest.newpos.online/api/v1/auth/status");
  assert.equal(buildApiUrl("", "me"), "/api/v1/me");
});

test("buildWebSocketUrl upgrades https to wss and http to ws", () => {
  assert.equal(
    buildWebSocketUrl("https://otternest.newpos.online", "/api/v1/projects/p1/events/ws"),
    "wss://otternest.newpos.online/api/v1/projects/p1/events/ws"
  );
  assert.equal(
    buildWebSocketUrl("http://localhost:3000", "/api/v1/projects/p1/events/ws"),
    "ws://localhost:3000/api/v1/projects/p1/events/ws"
  );
});

test("formatAuthConnectionError explains CORS/network fetch failures clearly", () => {
  const fetchErr = new TypeError("Failed to fetch");
  const msg = formatAuthConnectionError(fetchErr, "https://otternest.newpos.online");
  assert.match(msg, /CORS/);
  assert.match(msg, /https:\/\/otternest\.newpos\.online\/api\/v1\/auth\/status/);
});
