// Contract-level UI checks with synthetic API fixtures. No live database.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
const root = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../..",
  ),
  shots = process.env.DX_SCREENSHOTS;
if (shots) await mkdir(shots, { recursive: true });
const mime = {
  ".html": "text/html",
  ".js": "text/javascript",
  ".css": "text/css",
};
const server = createServer(async (req, res) => {
  try {
    let p = decodeURIComponent(
      new URL(req.url, "http://local").pathname,
    ).replace(/^\/admin-ui\//, "/admin/");
    const f = path.resolve(
      root,
      "." + p + (p.endsWith("/") ? "index.html" : ""),
    );
    if (!f.startsWith(root + path.sep)) throw Error("path");
    res.writeHead(200, {
      "Content-Type": mime[path.extname(f)] || "application/octet-stream",
    });
    res.end(await readFile(f));
  } catch {
    res.writeHead(404);
    res.end("not found");
  }
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const base = "http://127.0.0.1:" + server.address().port;
const browser = await chromium.launch({
  headless: true,
  ...(process.env.DX_CHROMIUM
    ? {
        executablePath: process.env.DX_CHROMIUM,
        args: [
          "--no-sandbox",
          "--no-zygote",
          "--single-process",
          "--use-gl=angle",
          "--use-angle=swiftshader",
          "--enable-unsafe-swiftshader",
        ],
      }
    : {}),
});
const calls = [],
  errors = [];
let checks = 0,
  expired = false,
  nextQ = 2;
let rounds = [
  { code: "R1A", status: "DRAFT", position: 0, question_active: false, rules: {r1a:{question_count:23,question_duration_ms:30000}} },
  { code: "R1B", status: "DRAFT", timer_mode: "CONTINUOUS", rules: {r1b:{turn_count:4,total_answering_budget_ms:1680000}} },
];
let teams = [
  {
    id: 7,
    code: "DEMO001",
    name: "Number Ninjas",
    username: "team01",
    enabled: true,
    points: 1250,
    submission_count: 2,
    active_session_count: 1,
    enrolled_rounds: [],
  },
];
let questions = [
  {
    id: 1,
    round_code: "R1A",
    position: 1,
    question_text: "What is 7 + 8?",
    answer_type: "MCQ",
    enabled: true,
    correct_option_id: 11,
    options: [
      { id: 11, position: 1, option_text: "15" },
      { id: 12, position: 2, option_text: "16" },
    ],
  },
];
let sessions = [
  {
    session_id: "fixture-session-1234567890",
    round_code: "R1A",
    slot: 1,
    active: true,
    created_at: new Date().toISOString(),
    last_seen_at: new Date().toISOString(),
    lease_expires_at: new Date(Date.now() + 60000).toISOString(),
  },
];
const context = await browser.newContext({
  viewport: { width: 1360, height: 960 },
});
await context.addInitScript(() => {
  if (!sessionStorage.getItem("admin-fixture")) {
    localStorage.clear();
    sessionStorage.setItem("admin-fixture", "1");
  }
});
async function routes(page) {
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("**/api/**", async (route) => {
    const req = route.request(),
      url = new URL(req.url()),
      p = url.pathname,
      method = req.method(),
      json = req.headers()["content-type"]?.includes("application/json"),
      body = json ? req.postDataJSON() : null;
    calls.push({ p, method, body, query: url.searchParams.toString() });
    const send = (data, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(data),
      });
    if (p === "/api/auth/admin/setup")
      return body.username === "existing"
        ? send({ detail: "Admin already exists" }, 409)
        : send({ created: true, username: body.username });
    if (p === "/api/auth/admin/login")
      return body.password === "wrong-password"
        ? send({ detail: "Invalid admin credentials" }, 401)
        : send({ access_token: "fixture-admin-token" });
    if (expired)
      return send(
        {
          detail: {
            code: "TOKEN_INVALID",
            message: "Invalid or expired session",
          },
        },
        401,
      );
    if (p === "/api/admin/dashboard")
      return send({
        server_now: new Date().toISOString(),
        environment: "development",
        rounds,
        r1a_progress: { accepted: 3, unanswered: 1, finalized: 4 },
        r1b_teams: {
          total: 1,
          items: [
            {
              team_id: 7,
              team_code: "DEMO001",
              team_name: "Number Ninjas",
              current_player: 1,
              current_question_position: 1,
              turn_status: "WAITING",
              remaining_ms: 1680000,
              timer_frozen: true,
              active_session_count: 1,
            },
          ],
        },
      });
    if (p === "/api/admin/r1b/timer") {
      if (method === "PUT") rounds[1].timer_mode = body.timer_mode;
      return send({
        timer_mode: rounds[1].timer_mode,
        locked: rounds[1].status !== "DRAFT",
      });
    }
    if (p.endsWith("/validate"))
      return send({
        round_code: p.includes("R1A") ? "R1A" : "R1B",
        valid: false,
        issues: [
          {
            code: "QUESTION_POSITIONS",
            field: "questions",
            message: "Need all required question positions",
          },
        ],
      });
    if (p.startsWith("/api/admin/rounds/") && method === "POST") {
      const r = rounds.find((r) => r.code === body.round_code);
      r.status =
        { start: "LIVE", pause: "PAUSED", resume: "LIVE", end: "ENDED" }[
          p.split("/").at(-1)
        ] || r.status;
      if (p.endsWith("/start") && r.code === "R1A") {
        r.position = 1;
        r.question_active = true;
      }
      return send({ round_code: r.code, status: r.status });
    }
    if (p === "/api/admin/r1a/next") {
      rounds[0].position++;
      return send({ position: rounds[0].position });
    }
    if (p === "/api/admin/questions") {
      if (method === "GET")
        return send({
          total: questions.length,
          items: questions.filter(
            (q) => q.round_code === url.searchParams.get("round_code"),
          ),
        });
      const q = { ...body, id: nextQ++, options: [] };
      questions.push(q);
      return send({ id: q.id });
    }
    if (p.match(/^\/api\/admin\/questions\/\d+\/options$/)) {
      const q = questions.find((q) => q.id === Number(p.split("/")[4]));
      const o = { ...body, id: 100 + q.options.length };
      q.options.push(o);
      if (body.is_correct) q.correct_option_id = o.id;
      return send(o);
    }
    if (p.match(/^\/api\/admin\/questions\/\d+$/)) {
      const id = Number(p.split("/").at(-1)),
        q = questions.find((q) => q.id === id);
      if (method === "PUT") Object.assign(q, body);
      if (method === "DELETE") questions = questions.filter((q) => q.id !== id);
      return send(method === "DELETE" ? { deleted: true } : q);
    }
    if (p.match(/^\/api\/admin\/options\/\d+$/)) {
      const id = Number(p.split("/").at(-1)),
        q = questions.find((q) => q.options.some((o) => o.id === id));
      if (method === "PUT") {
        Object.assign(
          q.options.find((o) => o.id === id),
          body,
        );
        if (body.is_correct) q.correct_option_id = id;
      }
      return send({ ok: true });
    }
    if (p.endsWith("/image")) return send({ image_url: "/media/fixture.png" });
    if (p === "/api/admin/teams") {
      if (method === "GET") return send({ total: teams.length, items: teams });
      const t = {
        ...body,
        id: 8,
        enabled: true,
        points: 0,
        submission_count: 0,
        active_session_count: 0,
      };
      teams.push(t);
      return send(t);
    }
    if (p.match(/^\/api\/admin\/teams\/\d+$/)) {
      const t = teams.find((t) => t.id === Number(p.split("/").at(-1)));
      if (method === "PUT") Object.assign(t, body);
      return send(t);
    }
    if (p.endsWith("/password-reset"))
      return send({ ok: true, revoked_sessions: 1 });
    if (p.endsWith("/sessions")) return send({ team_id: 7, items: sessions });
    if (p.endsWith("/revoke") || p.endsWith("/revoke-all")) {
      sessions = sessions.map((s) => ({
        ...s,
        active: false,
        revocation_reason: body.reason,
      }));
      return send({ ok: true, revoked: 1 });
    }
    if (p === "/api/admin/submissions")
      return send({
        total: 1,
        items: [
          {
            id: 1,
            team_code: "DEMO001",
            team_name: "Number Ninjas",
            round_code: "R1A",
            position: 1,
            result: "UNANSWERED",
            points: 0,
            submitted_at: null,
            timed_out_at: new Date().toISOString(),
            recorded_at: new Date().toISOString(),
            finalization_source: "TIMEOUT",
          },
        ],
      });
    if (p === "/api/admin/leaderboard/current") return send({published:false,data:[]});
    if (p === "/api/admin/leaderboard/publish")
      return send({
        published: true,
        publication_id: 5,
        published_at: new Date().toISOString(),
        scope: { round_code: body.round_code },
        data: [
          {
            rank: 1,
            team_code: "DEMO001",
            team_name: "Number Ninjas",
            score: 1250,
          },
        ],
      });
    if (p === "/api/admin/exports/results.xlsx")
      return route.fulfill({
        status: 200,
        contentType:
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers: {
          "Content-Disposition": 'attachment; filename="fixture-results.xlsx"',
        },
        body: Buffer.from("fixture-export-response"),
      });
    if (p === "/api/admin/audit")
      return send({
        total: 1,
        items: [
          {
            id: 1,
            admin_username: "fixture-admin",
            action: "PAUSE_ROUND",
            details: "round=R1A",
            created_at: new Date().toISOString(),
          },
        ],
      });
    return send({ detail: "Unmapped test endpoint: " + p }, 404);
  });
}
const page = await context.newPage();
await routes(page);
try {
  await page.goto(base + "/admin-ui/");
  await page.getByLabel("Admin username", { exact: true }).fill("admin");
  await page.getByLabel("Password", { exact: true }).fill("wrong-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "not recognised" }).waitFor();
  checks++;
  await page.getByText("Login help", { exact: true }).click();
  await page
    .getByRole("button", { name: "Create a different development admin" })
    .click();
  await page.getByLabel("Admin username", { exact: true }).fill("existing");
  await page.getByLabel("Password", { exact: true }).fill("fixture-password");
  await page.getByRole("button", { name: "Create admin", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "already exists" }).waitFor();
  assert(
    calls.some(
      (c) =>
        c.p === "/api/auth/admin/setup" &&
        !c.query &&
        c.body.password === "fixture-password",
    ),
  );
  checks++;
  await page
    .getByRole("button", { name: "Back to sign in", exact: true })
    .click();
  await page.getByLabel("Admin username", { exact: true }).fill("admin");
  await page.getByLabel("Password", { exact: true }).fill("fixture-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("heading", { name: "Round controls" }).waitFor();
  checks++;
  if (shots)
    await page.screenshot({
      path: shots + "/admin-overview.png",
      fullPage: true,
    });
  await page
    .getByLabel("Timer mode", { exact: true })
    .selectOption("PAUSE_DURING_HANDOVER");
  await page.getByRole("button", { name: "Save mode", exact: true }).click();
  await page
    .getByRole("status")
    .filter({ hasText: "Timer mode saved" })
    .waitFor();
  assert.equal(rounds[1].timer_mode, "PAUSE_DURING_HANDOVER");
  checks++;
  await page
    .locator(".round-card")
    .first()
    .getByRole("button", { name: "Validate setup" })
    .click();
  await page
    .getByText("Need all required question positions", { exact: false })
    .waitFor();
  checks++;
  await page.getByRole("button", { name: "Questions", exact: true }).click();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page
    .getByLabel("Question text", { exact: false })
    .fill("Updated fixture question");
  await page
    .getByRole("button", { name: "Save question", exact: true })
    .click();
  await page
    .getByRole("status")
    .filter({ hasText: "Question saved" })
    .waitFor();
  assert.equal(questions[0].question_text, "Updated fixture question");
  checks++;
  await page.getByLabel("Option 1 text", { exact: true }).fill("15 (updated)");
  await page
    .getByRole("button", { name: "Save option", exact: true })
    .first()
    .click();
  await page.getByRole("status").filter({ hasText: "Option saved" }).waitFor();
  assert.equal(questions[0].options[0].option_text, "15 (updated)");
  checks++;
  await page.getByRole("button", { name: "Close editor", exact: true }).click();
  await page.getByRole("button", { name: "New question", exact: true }).click();
  await page.getByLabel("Question position").fill("2");
  await page
    .getByLabel("Question text", { exact: false })
    .fill("New fixture question");
  await page
    .getByRole("button", { name: "Create question", exact: true })
    .click();
  await page
    .getByRole("heading", { name: "Question editor", exact: true })
    .waitFor();
  assert.equal(questions[1].position, 2);
  checks++;
  await page
    .getByLabel("New option text", { exact: true })
    .fill("A fixture option");
  await page.getByRole("button", { name: "Add option", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Option added" }).waitFor();
  assert.equal(questions[1].options.length, 1);
  checks++;
  await page.getByRole("button", { name: "Teams", exact: true }).click();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByLabel("Team name", { exact: true }).fill("Updated Ninjas");
  await page.getByRole("button", { name: "Save team", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Team saved" }).waitFor();
  assert.equal(teams[0].name, "Updated Ninjas");
  checks++;
  await page
    .getByLabel("New team password", { exact: true })
    .fill("new-fixture-password");
  await page
    .getByRole("button", { name: "Reset password & revoke sessions" })
    .click();
  assert(!calls.some((c) => c.p.endsWith("/password-reset")));
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Confirm", exact: true })
    .click();
  await page
    .getByRole("status")
    .filter({ hasText: "Team password reset" })
    .waitFor();
  checks++;
  await page
    .getByRole("button", { name: "Manage sessions", exact: true })
    .click();
  await page.getByText("fixture-sess…", { exact: false }).waitFor();
  await page.getByLabel("Revocation reason").fill("Phone replacement");
  await page.getByRole("button", { name: "Revoke", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Confirm", exact: true })
    .click();
  await page
    .getByRole("status")
    .filter({ hasText: "revocation completed" })
    .waitFor();
  assert.equal(sessions[0].active, false);
  assert.equal(sessions[0].revocation_reason, "Phone replacement");
  checks++;
  await page.getByRole("button", { name: "Submissions", exact: true }).click();
  await page
    .getByText("Timeout, not a player submission", { exact: true })
    .waitFor();
  await page.getByLabel("Round", { exact: true }).selectOption("R1B");
  await page.getByLabel("Question", { exact: true }).fill("2");
  await page.getByLabel("Team ID", { exact: true }).fill("7");
  await page.getByRole("button", { name: "Apply filters" }).click();
  await page.waitForTimeout(150);
  assert(
    calls.some(
      (c) =>
        c.p.endsWith("/submissions") &&
        c.query.includes("round_code=R1B") &&
        c.query.includes("team_id=7") &&
        c.query.includes("position=2"),
    ),
  );
  checks++;
  await page.getByRole("button", { name: "Results", exact: true }).click();
  assert.equal(await page.getByRole("button",{name:"Publish R1A",exact:true}).count(),0);checks++;
  await page.getByRole("button", { name: "Publish R1B", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Confirm", exact: true })
    .click();
  await page
    .getByRole("heading", { name: "Snapshot published in this session" })
    .waitFor();
  checks++;
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download Excel" }).click();
  assert.equal((await download).suggestedFilename(), "fixture-results.xlsx");
  checks++;
  await page.getByRole("button", { name: "Audit log", exact: true }).click();
  await page.getByText("fixture-admin", { exact: true }).waitFor();
  checks++;
  await page.getByRole("button", { name: "Overview", exact: true }).click();
  await page
    .locator(".round-card")
    .first()
    .getByRole("button", { name: "Start", exact: true })
    .click();
  assert.equal(rounds[0].status, "DRAFT");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Confirm", exact: true })
    .click();
  await page
    .locator(".round-card")
    .first()
    .getByText("LIVE", { exact: true })
    .waitFor();
  assert.equal(rounds[0].status, "LIVE");
  checks++;
  await page.getByRole("button",{name:"Reset both rounds",exact:true}).waitFor();checks++;
  await page.getByRole("button", { name: "Questions", exact: true }).click();
  await page.getByRole("button", { name: "View", exact: true }).first().click();
  await page.getByLabel("Question text", { exact: false }).waitFor();
  assert(await page.getByLabel("Question text", { exact: false }).isDisabled());
  checks++;
  expired = true;
  await page.getByRole("button", { name: "Overview", exact: true }).click();
  await page.getByRole("heading", { name: "Admin sign in" }).waitFor();
  await page.getByRole("alert").filter({ hasText: "expired" }).waitFor();
  checks++;
  assert.deepEqual(errors, []);
  await page.close();
  // Mobile page with separate local-storage reset for this fixture context.
  expired = false;
  const mc = await browser.newContext({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  });
  await mc.addInitScript(() =>
    localStorage.setItem("adminToken", "fixture-mobile"),
  );
  const mp = await mc.newPage();
  await routes(mp);
  await mp.goto(base + "/admin-ui/");
  await mp.getByRole("heading", { name: "Round controls" }).waitFor();
  assert(
    await mp.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
  );
  checks++;
  if (shots)
    await mp.screenshot({ path: shots + "/admin-mobile.png", fullPage: true });
  assert.deepEqual(errors, []);
  await mp.close();
  console.log(
    `${checks} admin browser workflow checks passed (synthetic API fixtures, not a live database).`,
  );
} finally {
  await browser.close();
  await new Promise((r) => server.close(r));
}
