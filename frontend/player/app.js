"use strict";
const $ = id => document.getElementById(id);
const mineButton = document.createElement("button"); mineButton.dataset.view = "mine"; mineButton.textContent = "My rank & points";
$("refresh").before(mineButton);
let token = localStorage.getItem("r1token"), identity = null, view = "A", generation = 0, scheduled, ticker, heartbeatTimer;
let busy = false, activePosition = null;
const accepted = new Set();
function notice(text, error = false) { $("msg").textContent = text; $("msg").classList.toggle("error", error); }
function errorDetail(detail) { return detail && typeof detail === "object" ? detail.message : detail; }
function stop() { clearTimeout(scheduled); clearInterval(ticker); }
function signedOut(reason) { stop(); clearInterval(heartbeatTimer); generation++; token = null; identity = null; localStorage.removeItem("r1token"); localStorage.removeItem("r1team"); $("app").hidden = true; $("login").hidden = false; accepted.clear(); if (reason) notice(reason, true); }
async function api(path, body, publicRequest = false) {
  const headers = {"Content-Type": "application/json"};
  if (token && !publicRequest) headers.Authorization = "Bearer " + token;
  const response = await fetch(path, {method: body === undefined ? "GET" : "POST", headers, body: body === undefined ? undefined : JSON.stringify(body)});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && !publicRequest) signedOut("Your session expired or was revoked. Log in again.");
    const detail = Array.isArray(data.detail) ? data.detail.map(e => e.loc.join(".") + ": " + e.msg).join("\n") : errorDetail(data.detail);
    const error = Error(detail || "Request failed (" + response.status + ")"); error.status = response.status; throw error;
  }
  return data;
}
async function apiPut(path, body) {
  const headers = {"Content-Type": "application/json", Authorization: "Bearer " + token};
  const response = await fetch(path, {method: "PUT", headers, body: JSON.stringify(body)});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) signedOut("Your session expired or was revoked. Log in again.");
    const detail = Array.isArray(data.detail) ? data.detail.map(e => e.loc.join(".") + ": " + e.msg).join("\n") : errorDetail(data.detail);
    const error = Error(detail || "Request failed (" + response.status + ")"); error.status = response.status; throw error;
  }
  return data;
}
async function enter(loginIdentity) {
  $("login").hidden = true; $("app").hidden = false; $("team").textContent = localStorage.getItem("r1team") || "Team session";
  identity = loginIdentity || await api("/api/auth/team/me");
  $("team").textContent += " · " + identity.round_code + " · Slot " + identity.slot;
  clearInterval(heartbeatTimer); await api("/api/auth/team/heartbeat", {});
  heartbeatTimer = setInterval(() => { if (token) api("/api/auth/team/heartbeat", {}).catch(() => {}); }, (identity.heartbeat_interval_seconds || 15) * 1000);
  navigate(identity.round_code === "R1B" ? "B" : "A");
}
async function action(fn) {
  if (busy) return;
  busy = true; document.querySelectorAll("button").forEach(b => b.disabled = true);
  try { await fn(); } catch (e) { notice(e.message, true); }
  finally { busy = false; document.querySelectorAll("button").forEach(b => b.disabled = false); }
}
$("loginForm").onsubmit = e => { e.preventDefault(); action(async () => {
  const data = await api("/api/auth/team/login", {username: $("username").value, password: $("password").value}, true);
  token = data.access_token; localStorage.setItem("r1token", token); localStorage.setItem("r1team", data.team_code + " · " + data.team_name);
  $("password").value = ""; notice(""); await enter(data);
}); };
$("activationForm").onsubmit = e => { e.preventDefault(); action(async () => {
  await api("/api/auth/team/set-password", {team_code: $("activateCode").value, activation_token: $("activateToken").value, new_password: $("activatePassword").value}, true);
  $("activationForm").reset(); notice("Password created. Log in with your team username.");
}); };
$("logout").onclick = () => action(async () => {
  await api("/api/auth/team/logout", {}); signedOut(); notice("Logged out. This slot is available again.");
});
document.querySelectorAll("[data-view]").forEach(b => b.onclick = () => navigate(b.dataset.view));
$("refresh").onclick = () => navigate(view);
function navigate(next) {
  stop(); view = next; generation++; activePosition = null;
  $("content").replaceChildren(); notice("");
  document.querySelectorAll("[data-view]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.view === view)));
  render(generation);
}
function node(tag, text, parent = $("content")) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; parent.append(el); return el; }
function image(url, parent = $("content")) { if (!url) return; const el = node("img", undefined, parent); el.src = url; el.alt = "Question illustration"; el.className = "question-image"; }
function button(text, fn) { const el = node("button", text); el.onclick = () => action(fn); return el; }
function later(id, delay = 2500) { clearTimeout(scheduled); scheduled = setTimeout(() => render(id), delay); }
function countdown(ms, id) {
  const el = node("div"); el.className = "timer"; el.setAttribute("aria-label", "Time remaining");
  const target = performance.now() + Math.max(0, ms); clearInterval(ticker);
  const tick = () => {
    if (id !== generation) return;
    const left = Math.max(0, target - performance.now()), seconds = Math.ceil(left / 1000);
    el.textContent = Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
    if (left <= 0) { clearInterval(ticker); later(id, 200); }
  };
  ticker = setInterval(tick, 200); tick();
}
async function render(id) {
  if (id !== generation || !token) return;
  try {
    if (view === "A") {
      const state = await api("/api/r1a/state"); if (id !== generation) return;
      if (state.status !== "LIVE") {
        $("content").replaceChildren(); node("h2", "Round 1(a) · WizTime");
        node("p", state.status === "ENDED" ? "Round complete." : state.status === "PAUSED" ?
          "The admin paused this round. The question clock is frozen and this page will resume automatically." : state.status === "WAITING" ?
          "Question closed. Waiting for the admin to reveal the next question…" : "Waiting for the admin to start. This page updates automatically.");
        if (state.status !== "ENDED") later(id); return;
      }
      const data = await api("/api/r1a/question"); if (id !== generation) return;
      $("content").replaceChildren(); node("h2", "Question " + data.position + " of 23"); countdown(data.remaining_ms, id);
      activePosition = data.position;
      if (accepted.has(data.position)) { node("p", "Answer locked. Waiting for the next question…"); return; }
      node("p", data.question.text || "").className = "question-text"; image(data.question.image_url);
      let selected = null;
      for (const option of data.question.options) {
        const el = node("button", option.text || "Option " + option.position); el.className = "option"; image(option.image_url, el);
        el.onclick = () => { if (busy) return; selected = option.id; document.querySelectorAll(".option").forEach(b => b.classList.remove("selected")); el.classList.add("selected"); };
      }
      button("Lock answer", async () => {
        if (!selected) throw Error("Select an option first.");
        const position = data.position;
        try {
          const result = await api("/api/r1a/submit", {option_id: selected});
          accepted.add(position);
          if (id === generation && activePosition === position) { notice(result.result + " · " + result.points + " points"); await render(id); }
        } catch (e) {
          if (e.status === 409 && /Already submitted/i.test(e.message)) { accepted.add(position); await render(id); }
          else throw e;
        }
      });
    } else if (view === "B") {
      const state = await api("/api/r1b/state"); if (id !== generation) return;
      $("content").replaceChildren(); node("h2", "Round 1(b) · Math Rally");
      if (state.round_status === "PAUSED") { node("p", "The admin paused this round. Your team's 28-minute clock is frozen."); later(id); return; }
      if (["FINISHED", "EXPIRED"].includes(state.turn_status)) { node("p", state.turn_status === "FINISHED" ? "All four turns are complete." : "Your team's time has expired."); return; }
      if (state.turn_status === "NOT_STARTED") {
        node("p", "Prepare your relay here. The 28-minute clock starts only when Player 1 presses READY.");
        button("Prepare relay", async () => { await api("/api/r1b/start", {}); await render(id); }); return;
      }
      if (state.turn_status === "WAITING") {
        countdown(state.remaining_ms, id); node("h3", "Player " + state.current_player + " · Your turn");
        if (identity && identity.slot !== state.current_player) { node("p", "Relay slot " + state.current_player + " is next. Keep this page open; your heartbeat remains active."); later(id); return; }
        node("p", state.clock_running ? "Pass the device now. CONTINUOUS mode consumes time during this handover." :
          "Pass the device now. The team budget is frozen until READY.");
        button("Ready · Show my question", async () => { await api("/api/r1b/ready", {}); await render(id); }); return;
      }
      const data = await api("/api/r1b/question"); if (id !== generation) return;
      const restored = await api("/api/r1b/draft"); if (id !== generation || restored.turn_id !== data.turn_id) return;
      countdown(data.remaining_ms, id); node("h3", "Player " + data.player_number + " · Question " + data.position + " of 4");
      node("p", data.question.text || "").className = "question-text"; image(data.question.image_url);
      const label = node("label", "Decimal answer"), input = node("input", undefined, label); input.inputMode = "decimal"; input.placeholder = "e.g. -12.75";
      input.value = restored.answer;
      const saveStatus = node("small", restored.version ? "Draft restored · saved" : "Draft not yet saved");
      let draftVersion = restored.version, dirty = false, debounceTimer, maxTimer;
      let saveChain = Promise.resolve();
      const flushDraft = () => {
        clearTimeout(debounceTimer); clearTimeout(maxTimer); debounceTimer = maxTimer = null;
        if (!dirty || id !== generation) return;
        dirty = false; const answer = input.value, version = ++draftVersion;
        saveStatus.textContent = "Saving…";
        saveChain = saveChain.then(() => apiPut("/api/r1b/draft", {turn_id: data.turn_id, version, answer}))
          .then(saved => { if (id === generation && saved.version === draftVersion && !dirty) saveStatus.textContent = "Saved"; })
          .catch(error => { if (id === generation) saveStatus.textContent = "Unsaved · " + error.message; });
      };
      input.oninput = () => {
        dirty = true; saveStatus.textContent = "Unsaved changes";
        clearTimeout(debounceTimer); debounceTimer = setTimeout(flushDraft, 500);
        if (!maxTimer) maxTimer = setTimeout(flushDraft, 2000);
      };
      const submit = async skip => {
        if (!skip && (!input.value.trim() || !Number.isFinite(Number(input.value)))) throw Error("Enter a finite numerical answer, or choose Skip.");
        if (skip && !confirm("Skip this question permanently?")) return;
        clearTimeout(debounceTimer); clearTimeout(maxTimer); dirty = false;
        const result = await api("/api/r1b/submit", {answer: input.value, skip});
        notice(result.result + " · " + result.points + " points"); await render(id);
      };
      node("p", "Drafts are saved after a short pause in typing. Text that never reaches the server cannot be restored or counted when time expires.");
      node("p", "Submit or skip to unlock the next player's turn. Answers must reach the server before time expires.");
      button("Submit answer", () => submit(false)); button("Skip question", () => submit(true));
    } else if (view === "board") {
      const boards = await Promise.all([identity.round_code].map(r => api("/api/leaderboard/published?round_code=" + r)));
      if (id !== generation) return; $("content").replaceChildren();
      for (const board of boards) {
        node("h2", board.round_code === "R1A" ? "Round 1(a)" : "Round 1(b)");
        if (!board.published) { node("p", "The admin has not published scores yet."); continue; }
        node("p", "Published " + new Date(board.published_at).toLocaleString());
        const table = node("table"), head = node("tr", undefined, node("thead", undefined, table));
        ["Rank", "Team", "Score"].forEach(t => node("th", t, head));
        const body = node("tbody", undefined, table);
        board.data.forEach(row => { const tr = node("tr", undefined, body); [row.rank, row.team_name, row.score].forEach(t => node("td", t, tr)); });
      }
      later(id, 10000);
    } else {
      const standings = await Promise.all([identity.round_code].map(r => api("/api/leaderboard/mine?round_code=" + r)));
      if (id !== generation) return; $("content").replaceChildren();
      node("h2", "My published rank & points");
      node("p", "These values change only when the admin publishes a leaderboard.");
      for (const standing of standings) {
        const card = node("section"); card.className = "card";
        node("h3", standing.round_code === "R1A" ? "Round 1(a)" : "Round 1(b)", card);
        if (!standing.published) {
          node("p", "Not published yet.", card);
        } else if (standing.rank == null) {
          node("p", "Your team was not included in this publication.", card);
        } else {
          node("p", "Rank " + standing.rank + " · " + standing.score + " points", card);
          node("small", "Published " + new Date(standing.published_at).toLocaleString(), card);
        }
      }
      later(id, 10000);
    }
  } catch (e) {
    if (id !== generation) return;
    notice(e.message, true);
    if (token) { $("content").replaceChildren(); node("p", "Retrying automatically. You can also use Refresh."); later(id, 5000); }
  }
}
addEventListener("pagehide", () => {
  if (!token) return;
  fetch("/api/auth/team/departing", {method: "POST", keepalive: true,
    headers: {Authorization: "Bearer " + token, "Content-Type": "application/json"}, body: "{}"}).catch(() => {});
});
if (token) enter().catch(e => { if (token) notice(e.message, true); });
