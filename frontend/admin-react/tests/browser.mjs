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
let rejectNextAudienceWrite=false;
let combinedPublished=false;
const combinedRows=Array.from({length:19},(_,i)=>({rank:i+1,team_id:i+1,team_code:'T'+String(i+1).padStart(2,'0'),team_name:i?'TEAM '+(i+1):'Number Ninjas',r1a_score:4000-i*500,r1b_score:-1500,score:2500-i*500}));
const combinedPayload=()=>({round_code:'R1',ready:rounds.every(r=>r.status==='ENDED'),published:combinedPublished,final:combinedPublished,publication_id:combinedPublished?6:null,published_at:combinedPublished?new Date().toISOString():null,data:rounds.every(r=>r.status==='ENDED')?combinedRows:[],total_teams:19,ties:[],qualification:{status:combinedPublished?'READY':'NOT_PUBLISHED',team_ids:combinedPublished?combinedRows.slice(0,16).map(r=>r.team_id):[]}});
let audienceState={revision:0,round_code:'R1A',view:'STANDBY',snapshot_at:new Date().toISOString(),paused:false,question:null,
 controls:{preview_upcoming:false,leaderboard_visible:false},review:{ended:false,step:-1,total_slides:8,can_start:false,can_next:false}};
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
    if(p==='/api/admin/audience'||p==='/api/admin/audience/review'){
      if(expired)return send({detail:{code:'TOKEN_INVALID',message:'Expired'}},401);
      if(method==='GET')return send(audienceState);
      if(rejectNextAudienceWrite){rejectNextAudienceWrite=false;audienceState.revision++;}
      if(body.expected_revision!==audienceState.revision)return send({detail:{message:'Refresh audience controls'}},409);
      if(method==='PUT')audienceState.controls={preview_upcoming:body.preview_upcoming,leaderboard_visible:body.leaderboard_visible};
      else{
        if(!audienceState.review.ended)return send({detail:{message:'Round not ended'}},409);
        audienceState.review.step=body.action==='start'?0:audienceState.review.step+1;
        audienceState.review.can_start=false;audienceState.review.can_next=audienceState.review.step<8;
      }
      audienceState.revision++;return send({revision:audienceState.revision,...audienceState.controls});
    }
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
        environment: "rehearsal",
        reset_control:{authorized:true,available:rounds.every(r=>!['LIVE','PAUSED'].includes(r.status)),reason:rounds.some(r=>['LIVE','PAUSED'].includes(r.status))?'End both live and paused rounds first.':'Disposable rehearsal database authorized.'},
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
    if(p==='/api/admin/rounds/reset'){
      assert.equal(body.confirmation,'RESET ROUND 1');assert(body.reason.trim());
      rounds.forEach(r=>{r.status='DRAFT';r.position=0;r.question_active=false;});combinedPublished=false;
      return send({ok:true,affected_records:{score_events:2,submissions:2}});
    }
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
      if (method === "DELETE") {
        assert.equal(body.confirmation, t.name);
        assert(body.reason.trim());
        if (t.submission_count || t.active_session_count)
          return send({detail:{code:"TEAM_HAS_COMPETITION_HISTORY",message:"Team has competition history and cannot be deleted"}},409);
        teams = teams.filter((team) => team.id !== t.id);
        return send({deleted:true,team_id:t.id});
      }
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
    if (p === "/api/admin/leaderboard/combined") return send(combinedPayload());
    if (p === "/api/admin/leaderboard/publish" && body.round_code==='R1') {combinedPublished=true;return send({...combinedPayload(),scope:{round_code:'R1'}});}
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
  const maintenanceCopy=await page.locator('.maintenance-panel').innerText();
  assert.match(maintenanceCopy,/This deletes R1 competition state/);
  assert.match(maintenanceCopy,/admin-selected R1\(b\) timer configuration/);
  assert(!maintenanceCopy.includes('historical records'));checks++;
  checks++;
  await page.getByLabel('Audited reason',{exact:true}).fill('Another disposable rehearsal');
  await page.getByLabel('Type RESET ROUND 1 to confirm',{exact:true}).fill('incorrect');
  await page.getByRole('button',{name:'Reset both rounds',exact:true}).click();
  assert.equal(calls.filter(c=>c.p==='/api/admin/rounds/reset').length,0);
  await page.getByLabel('Type RESET ROUND 1 to confirm',{exact:true}).fill('RESET ROUND 1');
  await page.getByRole('button',{name:'Reset both rounds',exact:true}).click();
  await page.getByRole('status').filter({hasText:'Round 1 reset completed'}).waitFor();
  assert.equal(calls.filter(c=>c.p==='/api/admin/rounds/reset').length,1);checks++;
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
  const deletionButton = page.getByRole("button", {name:"Delete Team",exact:true});
  assert(await deletionButton.isDisabled());
  await page.getByLabel("Deletion reason", {exact:true}).fill("Duplicate registration");
  await page.getByLabel("Type the exact team name: Updated Ninjas", {exact:true}).fill("updated ninjas");
  assert(await deletionButton.isDisabled());
  await page.getByLabel("Type the exact team name: Updated Ninjas", {exact:true}).fill("Updated Ninjas ");
  assert(await deletionButton.isDisabled());
  await page.getByLabel("Type the exact team name: Updated Ninjas", {exact:true}).fill("Updated Ninjas");
  await deletionButton.click();
  assert.equal(calls.filter(c=>c.method==="DELETE"&&c.p.startsWith("/api/admin/teams/")).length,0);
  await page.getByRole("dialog").getByRole("button",{name:"Cancel",exact:true}).click();
  assert.equal(calls.filter(c=>c.method==="DELETE"&&c.p.startsWith("/api/admin/teams/")).length,0);
  await deletionButton.click();
  await page.getByRole("dialog").getByRole("button",{name:"Confirm",exact:true}).click();
  await page.getByRole("dialog").getByRole("alert").filter({hasText:"Team has competition history"}).waitFor();
  assert.equal(teams.length,1);
  await page.getByRole("dialog").getByRole("button",{name:"Cancel",exact:true}).click();
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
  await page.getByRole("button", {name:"Teams",exact:true}).click();
  await page.getByRole("button", {name:"Create team",exact:true}).click();
  const creation = page.locator(".panel").filter({has:page.getByRole("heading",{name:"Create a team",exact:true})});
  await creation.getByLabel("Team code",{exact:true}).fill("UNUSED");
  await creation.getByLabel("Team name",{exact:true}).fill("Unused Browser Team");
  await creation.getByLabel("Login username",{exact:true}).fill("unused-browser");
  await creation.getByRole("button",{name:"Create team",exact:true}).click();
  await page.getByLabel("Type the exact team name: Unused Browser Team",{exact:true}).fill("Unused Browser Team");
  await page.getByLabel("Deletion reason",{exact:true}).fill("Unused test registration");
  await page.getByRole("button",{name:"Delete Team",exact:true}).click();
  await page.getByRole("dialog").getByRole("button",{name:"Confirm",exact:true}).click();
  await page.getByRole("status").filter({hasText:"Unused team deleted"}).waitFor();
  assert.equal(teams.length,1);
  assert.equal(teams[0].id,7);
  assert.equal(await page.getByRole("heading",{name:"Team #8",exact:true}).count(),0);
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
  assert.equal(await page.getByRole("button",{name:"Publish final R1A",exact:true}).count(),1);checks++;
  assert(await page.getByRole('button',{name:'Publish final combined R1',exact:true}).isDisabled());checks++;
  rounds[1].status="ENDED";
  await page.reload();await page.getByRole("button", { name: "Results", exact: true }).click();
  await page.getByRole("button", { name: "Publish final R1B", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Confirm", exact: true })
    .click();
  await page
    .getByRole("heading", { name: "Snapshot published in this session" })
    .waitFor();
  rounds[0].status='ENDED';
  await page.reload();await page.getByRole('button',{name:'Results',exact:true}).click();
  const combinedPanel=page.locator('.panel').filter({has:page.getByRole('heading',{name:'Combined Round 1 standings',exact:true})});
  await combinedPanel.getByText('Admin preview · not yet published',{exact:false}).waitFor();
  assert.equal(await combinedPanel.locator('tbody tr').count(),19);
  assert.match(await combinedPanel.locator('tbody tr').last().innerText(),/-1,500/);
  await page.getByRole('button',{name:'Publish final combined R1',exact:true}).click();
  assert.equal(calls.filter(c=>c.p==='/api/admin/leaderboard/publish'&&c.body.round_code==='R1').length,0);
  await page.getByRole('dialog').getByRole('button',{name:'Confirm',exact:true}).click();
  await combinedPanel.getByText('Immutable publication #6',{exact:false}).waitFor();
  assert(await page.getByRole('button',{name:'Publish final combined R1',exact:true}).isDisabled());checks++;
  checks++;
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download Excel" }).click();
  assert.equal((await download).suggestedFilename(), "fixture-results.xlsx");
  checks++;
  await page.getByRole("button", { name: "Audit log", exact: true }).click();
  await page.getByText("fixture-admin", { exact: true }).waitFor();
  checks++;
  await page.getByRole("button", { name: "Overview", exact: true }).click();
  await page.getByLabel('Audited reason',{exact:true}).fill('Rehearsal after final publication');
  await page.getByLabel('Type RESET ROUND 1 to confirm',{exact:true}).fill('RESET ROUND 1');
  await page.getByRole('button',{name:'Reset both rounds',exact:true}).click();
  await page.getByRole('status').filter({hasText:'Round 1 reset completed'}).waitFor();
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
  await page.getByRole("button",{name:"Reset both rounds",exact:true}).waitFor();assert(await page.getByRole('button',{name:'Reset both rounds',exact:true}).isDisabled());checks++;
  await page.getByRole("button", { name: "Questions", exact: true }).click();
  await page.getByRole("button", { name: "View", exact: true }).first().click();
  await page.getByLabel("Question text", { exact: false }).waitFor();
  assert(await page.getByLabel("Question text", { exact: false }).isDisabled());
  checks++;
  await page.getByRole('button',{name:'Audience Control',exact:true}).click();
  await page.getByRole('switch',{name:'Upcoming Question Preview',exact:true,checked:false}).click();
  await page.getByRole('switch',{name:'Upcoming Question Preview',exact:true,checked:true}).waitFor();
  await page.getByRole('switch',{name:'Audience Leaderboard',exact:true,checked:false}).click();
  await page.getByRole('switch',{name:'Audience Leaderboard',exact:true,checked:true}).waitFor();
  assert.equal(await page.getByRole('switch',{name:'Audience Leaderboard',exact:true}).count(),1);
  assert(await page.getByRole('button',{name:'Start R1B review',exact:true}).isDisabled());assert(await page.getByRole('button',{name:'Next review slide',exact:true}).isDisabled());checks++;
  audienceState.review.ended=true;audienceState.review.can_start=true;
  await page.getByRole('button',{name:'Start R1B review',exact:true}).click();
  await page.getByText('Slide 1 / 8',{exact:true}).waitFor();
  await page.getByRole('button',{name:'Next review slide',exact:true}).click();
  await page.getByText('Slide 2 / 8',{exact:true}).waitFor();checks++;
  // Stale revisions surface the existing server error without changing the switch.
  rejectNextAudienceWrite=true;
  const writesBefore=calls.filter(c=>c.p==='/api/admin/audience'&&c.method==='PUT').length;
  await page.getByRole('switch',{name:'Upcoming Question Preview',checked:true}).click();
  await page.getByRole('alert').filter({hasText:'Refresh audience controls'}).waitFor();
  assert.equal(audienceState.controls.preview_upcoming,true);
  assert.equal(calls.filter(c=>c.p==='/api/admin/audience'&&c.method==='PUT').length,writesBefore+1);
  assert(await page.getByRole('switch',{name:'Upcoming Question Preview'}).isChecked());checks++;
  // Fresh polling restores the revision; OFF/ON keep the same complete settings contract.
  await page.waitForTimeout(1200);
  await page.getByRole('switch',{name:'Upcoming Question Preview',checked:true}).focus();
  await page.keyboard.press('Space');
  await page.getByRole('switch',{name:'Upcoming Question Preview',checked:false}).waitFor();
  await page.getByRole('switch',{name:'Upcoming Question Preview',checked:false}).focus();
  await page.keyboard.press('Space');
  await page.getByRole('switch',{name:'Upcoming Question Preview',checked:true}).waitFor();
  await page.getByRole('switch',{name:'Audience Leaderboard',checked:true}).click();
  await page.getByRole('switch',{name:'Audience Leaderboard',checked:false}).waitFor();
  await page.getByRole('switch',{name:'Audience Leaderboard',checked:false}).click();
  await page.getByRole('switch',{name:'Audience Leaderboard',checked:true}).waitFor();checks++;
  audienceState.round_code='R1B';audienceState.view='REVIEW_ANSWER';audienceState.question={position:1};audienceState.question_count=4;
  await page.getByText('R1B · REVIEW ANSWER',{exact:true}).waitFor();
  for(const viewport of [{width:390,height:844},{width:768,height:1024},{width:1366,height:768},{width:1920,height:1080}]){
    await page.setViewportSize(viewport);
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    for(const control of await page.locator('.audience-control button,.audience-control a').all()){
      const box=await control.boundingBox();assert(box&&box.x>=0&&box.x+box.width<=viewport.width&&box.height>=44);
    }
    assert.equal(await page.getByRole('switch').count(),2);
    assert(await page.getByRole('button',{name:'Start R1B review'}).isDisabled());
    assert(await page.getByRole('button',{name:'Next review slide'}).isEnabled());
    if(shots)await page.screenshot({path:shots+`/audience-control-${viewport.width}.png`,fullPage:true});
    checks++;
  }
  await page.setViewportSize({width:1366,height:768});
  if(shots)await page.screenshot({path:shots+'/admin-audience.png',fullPage:true});
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
  await mp.getByRole('button',{name:'Audience Control',exact:true}).click();
  await mp.getByRole('switch',{name:'Audience Leaderboard',exact:true,checked:true}).waitFor();
  assert(await mp.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));checks++;
  if(shots)await mp.screenshot({path:shots+'/admin-audience-mobile.png',fullPage:true});
  assert.deepEqual(errors, []);
  await mp.close();
  console.log(
    `${checks} admin browser workflow checks passed (synthetic API fixtures, not a live database).`,
  );
} finally {
  await browser.close();
  await new Promise((r) => server.close(r));
}
