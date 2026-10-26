import test from "node:test";
import assert from "node:assert/strict";
import { api, errorMessage, roundActions, time } from "../src/api.js";
test("round actions reflect the persisted lifecycle", () => {
  assert.deepEqual(roundActions({ status: "DRAFT" }), ["validate", "start"]);
  assert.deepEqual(roundActions({ status: "LIVE" }), ["pause", "end"]);
  assert.deepEqual(roundActions({ status: "PAUSED" }), ["resume", "end"]);
  assert.deepEqual(roundActions({ status: "ENDED" }), []);
  assert.deepEqual(roundActions(null), []);
});
test("time clamps expired budgets and rounds up correctly", () => {
  assert.equal(time(-500), "0:00");
  assert.equal(time(60001), "1:01");
  assert.equal(time(28 * 60000), "28:00");
});
test("structured validation errors remain useful", () => {
  assert.equal(errorMessage({ message: "Locked" }, 409), "Locked");
  assert.equal(
    errorMessage([{ loc: ["body", "password"], msg: "Too short" }], 422),
    "password: Too short",
  );
});
test("JSON admin setup never places credentials in URLs", async () => {
  const old = globalThis.fetch;
  let url, options;
  globalThis.fetch = async (u, o) => {
    url = u;
    options = o;
    return new Response('{"created":true}', { status: 200 });
  };
  try {
    await api("/api/auth/admin/setup", {
      method: "POST",
      body: { username: "test", password: "fixture-password" },
    });
    assert.equal(url, "/api/auth/admin/setup");
    assert.equal(JSON.parse(options.body).password, "fixture-password");
    assert(!url.includes("fixture-password"));
  } finally {
    globalThis.fetch = old;
  }
});
test("FormData upload preserves browser multipart boundary", async () => {
  const old = globalThis.fetch;
  let options;
  globalThis.fetch = async (u, o) => {
    options = o;
    return new Response("{}");
  };
  try {
    const body = new FormData();
    body.append("image", new Blob(["fixture"]), "fixture.png");
    await api("/api/admin/questions/1/image", {
      method: "POST",
      token: "fixture",
      body,
    });
    assert.equal(options.body, body);
    assert.equal(options.headers["Content-Type"], undefined);
    assert.equal(options.headers.Authorization, "Bearer fixture");
  } finally {
    globalThis.fetch = old;
  }
});
test("Excel downloads reject unexpected HTML or JSON responses", async () => {
  const old = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response("<html>login</html>", {
      headers: { "Content-Type": "text/html" },
    });
  try {
    await assert.rejects(
      () => api("/api/admin/exports/results.xlsx", { download: true }),
      /did not return an Excel/,
    );
  } finally {
    globalThis.fetch = old;
  }
});
test("authorization errors retain status and code", async () => {
  const old = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(
      JSON.stringify({
        detail: { code: "ADMIN_REQUIRED", message: "Admin only" },
      }),
      { status: 403 },
    );
  try {
    await assert.rejects(
      () => api("/api/admin/dashboard"),
      (e) => e.status === 403 && e.code === "ADMIN_REQUIRED",
    );
  } finally {
    globalThis.fetch = old;
  }
});
