"use strict";
const $ = id => document.getElementById(id);
for (const round of ["R1A", "R1B"]) {
  const start = document.querySelector('[data-action="start"][data-round="' + round + '"]');
  const validate = document.createElement("button"); validate.dataset.action = "validate"; validate.dataset.round = round;
  validate.textContent = "Validate " + (round === "R1A" ? "1(a)" : "1(b)"); validate.className = "secondary"; start.before(validate);
  for (const [action, label] of [["pause", "Pause " + (round === "R1A" ? "1(a)" : "1(b)")], ["resume", "Resume " + (round === "R1A" ? "1(a)" : "1(b)")]]) {
    const control = document.createElement("button"); control.dataset.action = action; control.dataset.round = round;
    control.textContent = label; control.className = "secondary"; start.after(control);
  }
}
const connectionHeading = document.createElement("th"); connectionHeading.textContent = "Connection";
$("teamRows").closest("table").querySelector("thead tr").append(connectionHeading);
let token = localStorage.getItem("adminToken");
function session(active) { $("loginPanel").hidden = active; $("panel").hidden = !active; $("logout").hidden = !active; }
function message(text, error = false) { $("message").textContent = text; $("message").classList.toggle("error", error); }
function errorDetail(detail) { return detail && typeof detail === "object" ? detail.message : detail; }
async function call(path, body, method = "POST") {
  const headers = token ? {Authorization: "Bearer " + token} : {};
  if (body && !(body instanceof FormData)) { headers["Content-Type"] = "application/json"; body = JSON.stringify(body); }
  const response = await fetch(path, {method, headers, body});
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) { localStorage.removeItem("adminToken"); token = null; session(false); }
    const detail = Array.isArray(result.detail) ? result.detail.map(e => e.loc.join(".") + ": " + e.msg).join("\n") : errorDetail(result.detail);
    throw Error(detail || "Request failed (" + response.status + ")");
  }
  return result;
}
async function run(element, action) {
  const buttons = element.matches("button") ? [element] : [...element.querySelectorAll("button")];
  buttons.forEach(b => b.disabled = true);
  message("Working…");
  try { const result = await action(); $("out").textContent = JSON.stringify(result, null, 2); message("Done. The result is shown below."); }
  catch (e) { message(e.message, true); }
  finally { buttons.forEach(b => b.disabled = false); }
}
function form(id, action) {
  $(id).addEventListener("submit", e => { e.preventDefault(); const data = Object.fromEntries(new FormData(e.currentTarget)); run(e.currentTarget, () => action(data)); });
}
form("loginForm", async data => {
  const result = await call("/api/auth/admin/login", data);
  token = result.access_token; localStorage.setItem("adminToken", token); session(true);
  $("loginForm").reset(); return {message: "Logged in"};
});
form("bootstrapForm", async data => {
  const result = await call("/api/admin/bootstrap?" + new URLSearchParams(data));
  $("loginForm").elements.username.value = data.username;
  $("bootstrapForm").reset(); return {message: "Admin created. Log in above.", username: result.username};
});
$("logout").onclick = () => { token = null; localStorage.removeItem("adminToken"); session(false); $("out").textContent = ""; message("Signed out."); };
$("downloadResults").onclick = () => run($("downloadResults"), async () => {
  const response = await fetch("/api/admin/exports/results.xlsx?scope=" + encodeURIComponent($("exportScope").value),
    {headers: {Authorization: "Bearer " + token}});
  if (!response.ok) { const data = await response.json().catch(() => ({})); throw Error(errorDetail(data.detail) || "Export failed"); }
  const blob = await response.blob(), link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  const disposition = response.headers.get("Content-Disposition") || "";
  link.download = disposition.match(/filename="([^"]+)"/)?.[1] || "round1-results.xlsx";
  link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  return {message: "Results workbook downloaded", scope: $("exportScope").value};
});
$("seed").onclick = e => run(e.target, () => call("/api/admin/rounds/seed"));
form("teamForm", async data => {
  const result = await call("/api/admin/teams", {...data, password: data.password || null});
  $("teamResult").textContent = "Created " + result.name + " · Team ID " + result.id + "\nUsername: " + result.username +
    (result.activation_token ? "\nActivation token (save and share privately): " + result.activation_token + "\nExpires: " + result.activation_expires_at : "\nUse the password you assigned.");
  $("teamForm").reset(); return result;
});
function questionMode() {
  const numeric = $("qRound").value === "R1B";
  $("numericFields").hidden = !numeric; $("mcqFields").hidden = numeric; $("qCorrect").required = numeric;
  $("qPosition").max = numeric ? "4" : "23"; $("qPosition").value = "1";
}
$("qRound").onchange = questionMode;
function renderOptions() {
  const wanted = Number($("optionCount").value), container = $("optionEditors");
  while (container.children.length > wanted) container.lastElementChild.remove();
  for (let i = container.children.length + 1; i <= wanted; i++) {
    const row = document.createElement("div"); row.className = "option-editor";
    row.innerHTML = '<label class="correct-choice"><input type="radio" name="correct_option" value="' + i + '" required> Correct</label>' +
      '<label>Option ' + i + ' text<input name="option_text" placeholder="Text, image, or both"></label>' +
      '<label>Option ' + i + ' image · optional<input name="option_image" type="file" accept="image/png,image/jpeg"></label>';
    container.append(row);
  }
}
for (let count = 11; count <= 20; count++) $("optionCount").append(new Option(count, count));
$("optionCount").onchange = renderOptions;
renderOptions();
async function uploadImage(kind, id, file) {
  if (!file || !file.size) return;
  if (file.size > 5 * 1024 * 1024) throw Error("Every image must be smaller than 5 MB.");
  const body = new FormData(); body.append("image", file);
  await call("/api/admin/" + kind + "/" + id + "/image", body);
}
$("questionForm").addEventListener("submit", e => {
  e.preventDefault();
  run(e.currentTarget, async () => {
    const data = new FormData(e.currentTarget), numeric = data.get("round_code") === "R1B";
    const questionText = String(data.get("question_text") || "").trim();
    const questionImage = data.get("question_image");
    if (!questionText && !questionImage.size) throw Error("Add question text, a question image, or both.");
    if (numeric && (!String(data.get("correct_numeric")).trim() || !Number.isFinite(Number(data.get("correct_numeric"))))) throw Error("Enter a finite decimal answer.");
    const rows = [...$("optionEditors").querySelectorAll(".option-editor")];
    if (!numeric && rows.some(row => !row.querySelector('[name="option_text"]').value.trim() && !row.querySelector('[name="option_image"]').files[0])) throw Error("Every option needs text, an image, or both.");
    const correct = numeric ? null : Number(data.get("correct_option"));
    if (!numeric && !correct) throw Error("Mark one option as correct.");
    const question = await call("/api/admin/questions", {round_code: data.get("round_code"), position: Number(data.get("position")),
      question_text: questionText || null, answer_type: numeric ? "NUMERIC" : "MCQ",
      correct_numeric: numeric ? data.get("correct_numeric") : null,
      tolerance_abs: numeric ? data.get("tolerance_abs") : "0", tolerance_rel: numeric ? data.get("tolerance_rel") : "0"});
    await uploadImage("questions", question.id, questionImage);
    if (!numeric) for (let index = 0; index < rows.length; index++) {
      const row = rows[index], option = await call("/api/admin/questions/" + question.id + "/options", {
        position: index + 1, option_text: row.querySelector('[name="option_text"]').value.trim() || null, is_correct: correct === index + 1});
      await uploadImage("options", option.id, row.querySelector('[name="option_image"]').files[0]);
    }
    $("questionResult").textContent = "Saved question " + data.get("position") + " for " + data.get("round_code") + (numeric ? "." : " with " + rows.length + " options.");
    const round = data.get("round_code"), next = Math.min(Number(data.get("position")) + 1, numeric ? 4 : 23);
    e.currentTarget.reset(); $("qRound").value = round; $("qPosition").value = next; $("optionCount").value = "4"; questionMode(); $("qPosition").value = next; renderOptions();
    return {question_id: question.id, round, position: Number(data.get("position"))};
  });
});
document.querySelectorAll("[data-action]").forEach(button => {
  button.onclick = () => {
    const {action, round} = button.dataset;
    if (!["publish", "validate"].includes(action) && !confirm(button.textContent + "? This affects every team.")) return;
    run(button, () => action === "validate"
      ? call("/api/admin/rounds/" + round + "/validate", undefined, "GET")
      : call(action === "publish" ? "/api/admin/leaderboard/publish" : "/api/admin/rounds/" + action, {round_code: round}));
  };
});
form("releaseForm", async data => {
  if (!confirm("Revoke " + (data.session_id ? "this session" : "all sessions") + " for team " + data.team_id + "?")) return {message: "Cancelled"};
  const suffix = data.session_id ? "/sessions/" + encodeURIComponent(data.session_id) + "/revoke" : "/sessions/revoke-all";
  return call("/api/admin/teams/" + data.team_id + suffix, {reason: data.reason});
});
$("listSessions").onclick = e => run(e.target, () => {
  const teamId = $("releaseForm").elements.team_id.value;
  if (!teamId) throw Error("Enter a team ID first.");
  return call("/api/admin/teams/" + teamId + "/sessions", undefined, "GET");
});
session(Boolean(token)); questionMode();

let auditOffset = 0, auditTotal = 0, auditBusy = false, auditVersion = 0, shownPosition = 0;
let nextBusy = false;
async function refreshDashboard() {
  if (!token || auditBusy || document.hidden) return;
  auditBusy = true;
  const version = auditVersion;
  try {
    const params = new URLSearchParams({offset: auditOffset, limit: 100});
    if ($("auditRound").value) params.set("round_code", $("auditRound").value);
    if ($("auditPosition").value) params.set("position", $("auditPosition").value);
    if ($("auditTeam").value) params.set("team_id", $("auditTeam").value);
    const [state, data, teams] = await Promise.all([
      call("/api/admin/dashboard", undefined, "GET"),
      call("/api/admin/submissions?" + params, undefined, "GET"),
      call("/api/admin/teams", undefined, "GET")
    ]);
    if (!token || version !== auditVersion) return;
    const r = state.rounds.find(r => r.code === "R1A");
    shownPosition = r?.position || 0;
    const remaining = r?.question_deadline ? Math.max(0, Math.ceil((Date.parse(r.question_deadline) - Date.parse(r.paused_at || state.server_now)) / 1000)) : 0;
    $("roundState").textContent = !r ? "Initialize rounds first." :
      r.status === "PAUSED" ? "Round 1(a): PAUSED · " + remaining + " seconds preserved" :
      r.status !== "LIVE" ? "Round 1(a): " + r.status :
      r.question_active ? "Question " + shownPosition + " of 23 · " + remaining + " seconds remaining" :
      shownPosition === 23 ? "All 23 questions closed." : "Question " + shownPosition + " closed · Waiting for your next reveal";
    $("nextQuestion").disabled = nextBusy || !r || r.status !== "LIVE" || r.question_active || shownPosition >= 23;
    $("nextQuestion").textContent = shownPosition >= 23 ? "All questions revealed" : "Reveal question " + (shownPosition + 1);
    auditTotal = data.total;
    $("auditSummary").textContent = data.total ? "Showing " + (auditOffset + 1) + "–" + Math.min(auditOffset + 100, data.total) + " of " + data.total + " · Refreshes every 3 seconds" : "No submissions match these filters.";
    $("auditPrev").disabled = auditOffset === 0;
    $("auditNext").disabled = auditOffset + 100 >= data.total;
    const selectedTeam = $("auditTeam").value;
    $("auditTeam").replaceChildren(new Option("All teams", ""));
    for (const team of teams.items) $("auditTeam").append(new Option(team.code + " · " + team.name, team.id));
    $("auditTeam").value = selectedTeam;
    const selected = teams.items.find(team => String(team.id) === selectedTeam);
    $("activityTitle").textContent = selected ? selected.code + " · " + selected.name + " — complete activity" : "Live answer log";
    $("showAllTeams").hidden = !selected;
    $("teamRows").replaceChildren();
    for (const team of teams.items) {
      const tr = document.createElement("tr");
      if (String(team.id) === selectedTeam) tr.className = "selected-team";
      const teamCell = document.createElement("td"), teamButton = document.createElement("button");
      teamButton.className = "team-link"; teamButton.textContent = team.code + " · " + team.name;
      teamButton.onclick = () => selectTeam(team.id);
      teamCell.append(teamButton); tr.append(teamCell);
      const seenSeconds = team.last_seen_at ? (Date.now() - Date.parse(team.last_seen_at)) / 1000 : Infinity;
      const connection = !team.session_active ? "Signed out" : team.active_session_count + " active · " + (seenSeconds <= 35 ? "Online" : "Lease active");
      [team.username, team.submission_count, team.points,
       team.last_submission_at ? new Date(team.last_submission_at).toLocaleString() : "No activity yet", connection].forEach(value => {
        const td = document.createElement("td"); td.textContent = value; tr.append(td);
      });
      $("teamRows").append(tr);
    }
    $("auditRows").replaceChildren();
    for (const row of data.items) {
      const tr = document.createElement("tr");
      const answer = row.option_id ? "Option " + row.option_position + ": " + (row.option_text || "(image option)") : row.answer || "—";
      const values = [row.team_code + " · " + row.team_name + " (ID " + row.team_id + ")",
        row.position + (row.player_number ? " / Player " + row.player_number : ""), answer,
        row.submitted_at ? new Date(row.submitted_at).toLocaleString() + " ." + String(new Date(row.submitted_at).getMilliseconds()).padStart(3, "0") : row.timed_out_at ? "Timed out: " + new Date(row.timed_out_at).toLocaleString() : "Not submitted",
        row.elapsed_ms == null ? "—" : (row.elapsed_ms / 1000).toFixed(3) + " s", row.result, row.points];
      values.forEach(value => { const td = document.createElement("td"); td.textContent = value; tr.append(td); });
      if (row.option_image_url) {
        const img = document.createElement("img"); img.src = row.option_image_url; img.alt = "Submitted option"; img.style.maxWidth = "100px";
        tr.children[2].append(img);
      }
      $("auditRows").append(tr);
    }
  } catch (error) { $("auditSummary").textContent = error.message; }
  finally { auditBusy = false; }
}
$("nextQuestion").onclick = async () => {
  nextBusy = true; $("nextQuestion").disabled = true;
  try {
    await call("/api/admin/r1a/next", {expected_position: shownPosition});
    message("Next question revealed. Its 30-second timer has started.");
  } catch (error) { message(error.message, true); }
  finally { nextBusy = false; await refreshDashboard(); }
};
$("auditRefresh").onclick = () => { auditOffset = 0; auditVersion++; refreshDashboard(); };
function selectTeam(teamId) { $("auditTeam").value = String(teamId); $("auditRound").value = ""; $("auditPosition").value = ""; auditOffset = 0; auditVersion++; refreshDashboard(); window.scrollTo({top: 0, behavior: "smooth"}); }
$("auditTeam").onchange = () => { auditOffset = 0; auditVersion++; refreshDashboard(); };
$("showAllTeams").onclick = () => selectTeam("");
$("auditPrev").onclick = () => { auditOffset = Math.max(0, auditOffset - 100); auditVersion++; refreshDashboard(); };
$("auditNext").onclick = () => { if (auditOffset + 100 < auditTotal) auditOffset += 100; auditVersion++; refreshDashboard(); };
setInterval(refreshDashboard, 3000);
refreshDashboard();
