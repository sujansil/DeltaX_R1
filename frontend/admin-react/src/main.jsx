import React, { useState, useEffect, useRef, useCallback } from "react";
import { createRoot } from "react-dom/client";
import {
  api,
  date,
  points,
  time,
  roundName,
  roundActions,
  text,
} from "./api.js";
import "./style.css";
const NAV = [
  ["overview", "Overview"],
  ["questions", "Questions"],
  ["teams", "Teams"],
  ["sessions", "Sessions"],
  ["submissions", "Submissions"],
  ["results", "Results"],
  ["audit", "Audit log"],
];
const Context = React.createContext();
const useApp = () => React.useContext(Context);
function useData(path, { interval = 0 } = {}) {
  const { request, revision } = useApp(),
    [data, setData] = useState(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [version, setVersion] = useState(0);
  const reload = useCallback(() => setVersion((x) => x + 1), []);
  useEffect(() => {
    if (!path) {
      setData(null);
      setLoading(false);
      setError("");
      return;
    }
    let live = true,
      timer;
    const controller = new AbortController();
    setData(null);
    setError("");
    setLoading(true);
    async function get() {
      try {
        const d = await request(path, { signal: controller.signal });
        if (live) {
          setData(d);
          setError("");
        }
      } catch (e) {
        if (live && !controller.signal.aborted) setError(e.message);
      } finally {
        if (live) {
          setLoading(false);
          if (interval)
            timer = setTimeout(
              get,
              document.hidden ? Math.max(interval, 10000) : interval,
            );
        }
      }
    }
    get();
    return () => {
      live = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [path, version, revision, request, interval]);
  return { data, error, loading, reload };
}
function Message({ children, error = false }) {
  return children ? (
    <div
      className={"message " + (error ? "error" : "")}
      role={error ? "alert" : "status"}
    >
      {children}
    </div>
  ) : null;
}
function Field({ label, children, hint }) {
  const id = React.useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {React.cloneElement(children, {
        id,
        "aria-describedby": hint ? id + "-hint" : undefined,
      })}
      {hint && <small id={id + "-hint"}>{hint}</small>}
    </div>
  );
}
function Button({
  children,
  onClick,
  kind = "",
  disabled = false,
  type = "button",
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      className={"button " + kind}
      disabled={disabled}
    >
      {children}
    </button>
  );
}
function BusyButton({ label = "Save", ...props }) {
  const { busy } = useApp();
  return (
    <Button {...props} disabled={busy || props.disabled}>
      {busy ? "Working…" : label}
    </Button>
  );
}
function Panel({ title, description, children, actions }) {
  return (
    <section className="panel">
      <div className="panel-head">
        <div>
          <h2>{title}</h2>
          {description && <p>{description}</p>}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}
function Badge({ children }) {
  return (
    <span className={"badge " + String(children).toLowerCase()}>
      {children}
    </span>
  );
}
function DataState({ r, children }) {
  if (r.error)
    return (
      <Message error>
        {r.error}{" "}
        <Button kind="small" onClick={r.reload}>
          Retry
        </Button>
      </Message>
    );
  if (!r.data)
    return (
      <p className="empty">{r.loading ? "Loading…" : "No data available."}</p>
    );
  return children(r.data);
}
function Table({ columns, rows, empty = "No records." }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.id || r.session_id || i}>
              {columns.map((c) => (
                <td key={c.key}>{c.render ? c.render(r) : text(r[c.key])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {!rows.length && <p className="empty">{empty}</p>}
    </div>
  );
}
function Pager({ offset, total, limit, setOffset }) {
  return (
    <div className="pager">
      <span>
        {total
          ? `${offset + 1}–${Math.min(offset + limit, total)} of ${total}`
          : "0 records"}
      </span>
      <Button
        kind="small"
        disabled={!offset}
        onClick={() => setOffset(Math.max(0, offset - limit))}
      >
        Previous
      </Button>
      <Button
        kind="small"
        disabled={offset + limit >= total}
        onClick={() => setOffset(offset + limit)}
      >
        Next
      </Button>
    </div>
  );
}
function Confirm({ config, onClose, onConfirm, busy, error }) {
  const cancel = useRef(null);
  useEffect(() => {
    cancel.current?.focus();
    const f = (e) => {
      if (e.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  }, []);
  return (
    <div
      className="modal"
      onClick={() => {
        if (!busy) onClose();
      }}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-label={config.title}
        className="dialog"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Tab") {
            const els = [
              ...e.currentTarget.querySelectorAll("button:not(:disabled)"),
            ];
            if (!els.length) {
              e.preventDefault();
              return;
            }
            if (e.shiftKey && document.activeElement === els[0]) {
              e.preventDefault();
              els.at(-1).focus();
            } else if (!e.shiftKey && document.activeElement === els.at(-1)) {
              e.preventDefault();
              els[0].focus();
            }
          }
        }}
      >
        <h2>{config.title}</h2>
        <p>{config.description}</p>
        <Message error>{error}</Message>
        <div className="actions">
          <button
            ref={cancel}
            className="button"
            disabled={busy}
            onClick={onClose}
          >
            Cancel
          </button>
          <Button
            kind={config.danger ? "danger" : "primary"}
            disabled={busy}
            onClick={onConfirm}
          >
            {busy ? "Working…" : config.label || "Confirm"}
          </Button>
        </div>
      </section>
    </div>
  );
}
function Login({ onLogin, notice }) {
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [setup, setSetup] = useState(false),
    [success, setSuccess] = useState("");
  async function submit(e) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    setSuccess("");
    const form = e.currentTarget,
      d = new FormData(form),
      username = String(d.get("username")).trim();
    try {
      const r = await api(
        setup ? "/api/auth/admin/setup" : "/api/auth/admin/login",
        { method: "POST", body: { username, password: d.get("password") } },
      );
      if (setup) {
        setSetup(false);
        setSuccess(
          "Admin created. Sign in with the username and password you just chose.",
        );
      } else {
        onLogin(r.access_token);
        form.reset();
      }
    } catch (e) {
      setError(
        e.status === 401
          ? "Admin username or password not recognised. Demo team accounts cannot sign in here."
          : setup && e.status === 404
            ? "Development setup is disabled on this server. Use local admin provisioning/recovery."
            : setup && e.status === 409
              ? "That admin already exists. Creating it again does not reset its password."
              : e.message,
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login-shell">
      <div className="login-card">
        <div className="wordmark">
          DELTA X <span>CONTROL ROOM</span>
        </div>
        <h1>{setup ? "Create a development admin" : "Admin sign in"}</h1>
        <p>Round One · WizTime & Math Rally</p>
        <Message error>{error || notice}</Message>
        <Message>{success}</Message>
        <form onSubmit={submit} key={setup ? "setup" : "login"}>
          <Field label="Admin username">
            <input
              name="username"
              autoComplete="username"
              autoCapitalize="none"
              required
              maxLength={120}
            />
          </Field>
          <Field label="Password">
            <input
              name="password"
              type="password"
              autoComplete={setup ? "new-password" : "current-password"}
              required
              minLength={setup ? 8 : 1}
              maxLength={setup ? 128 : 256}
            />
          </Field>
          <Button kind="primary" type="submit" disabled={busy}>
            {busy ? "Connecting…" : setup ? "Create admin" : "Sign in"}
          </Button>
        </form>
        <details>
          <summary>Login help</summary>
          <p>
            Passwords cannot be retrieved. For the local start.sh preview, use
            reset_preview_admin.py from the project root. Existing admin
            accounts keep their old passwords when the preview is reseeded.
          </p>
          <Button
            kind="small"
            onClick={() => {
              setSetup(!setup);
              setError("");
              setSuccess("");
            }}
          >
            {setup ? "Back to sign in" : "Create a different development admin"}
          </Button>
          <p className="help">
            Account creation works only when ENVIRONMENT=development. It does
            not reset existing accounts.
          </p>
        </details>
        <a className="quiet-link" href="/player/">
          Open player page ↗
        </a>
      </div>
    </main>
  );
}
function Overview() {
  const { run, confirm, request, busy } = useApp(),
    r = useData("/api/admin/dashboard", { interval: 3000 }),
    timer = useData("/api/admin/r1b/timer"),
    [mode, setMode] = useState(""),
    [validation, setValidation] = useState(null),
    [offset, setOffset] = useState(0);
  const progress = useData(
    "/api/admin/dashboard?r1b_offset=" + offset + "&r1b_limit=25",
    { interval: 5000 },
  );
  async function validate(code) {
    await run(async () => {
      const d = await request("/api/admin/rounds/" + code + "/validate");
      setValidation(d);
      return d.valid
        ? "Configuration valid."
        : "Configuration has issues; review the list below.";
    });
  }
  function action(round, act) {
    confirm({
      title: `${act === "start" ? "Start" : act === "end" ? "End permanently" : act === "pause" ? "Pause" : "Resume"} ${roundName(round.code)}?`,
      description:
        act === "end"
          ? "Recorded scores remain. Unfinished drafts are not submitted early. This cannot be resumed."
          : act === "start" && round.code === "R1A"
            ? `Question 1 is revealed immediately and its ${round.rules.r1a.question_duration_ms / 1000}-second clock begins for every team.`
            : "This changes the round for every team.",
      danger: act === "end",
      action: () =>
        run(
          () =>
            request("/api/admin/rounds/" + act, {
              method: "POST",
              body: { round_code: round.code },
            }),
          "Round updated.",
        ),
    });
  }
  async function resetRoundOne(e) {
    e.preventDefault();
    const form = e.currentTarget,
      fields = new FormData(form),
      reason = String(fields.get("reason") || "").trim(),
      confirmation = String(fields.get("confirmation") || "");
    const ok = await run(async () => {
      const result = await request("/api/admin/rounds/reset", {
        method: "POST",
        body: { reason, confirmation },
      });
      const total = Object.values(result.affected_records || {}).reduce(
        (sum, count) => sum + Number(count || 0),
        0,
      );
      return `Round 1 reset completed. ${total} competition-state records were cleared or revoked.`;
    });
    if (ok) form.reset();
  }
  return (
    <>
      <Panel
        title="Round controls"
        description="Shared round state refreshes every 3 seconds."
        actions={
          <Button kind="small" onClick={r.reload}>
            Refresh
          </Button>
        }
      >
        <DataState r={r}>
          {(d) => (
            <>
              {!d.rounds.length ? (
                <>
                  <p>No round records exist yet.</p>
                  <BusyButton
                    label="Initialize round records"
                    onClick={() =>
                      run(
                        () =>
                          request("/api/admin/rounds/seed", {
                            method: "POST",
                            body: {},
                          }),
                        "Rounds initialized.",
                      )
                    }
                  />
                </>
              ) : (
                <div className="round-grid">
                  {["R1A", "R1B"].map((code) => {
                    const round = d.rounds.find((x) => x.code === code);
                    if (!round)
                      return (
                        <div className="round-card" key={code}>
                          <h3>{roundName(code)}</h3>
                          <p>Missing round record.</p>
                          <BusyButton
                            label="Initialize missing rounds"
                            onClick={() =>
                              run(
                                () =>
                                  request("/api/admin/rounds/seed", {
                                    method: "POST",
                                    body: {},
                                  }),
                                "Round records initialized.",
                              )
                            }
                          />
                        </div>
                      );
                    return (
                      <article className="round-card" key={code}>
                        <div className="row between">
                          <h3>{roundName(code)}</h3>
                          <Badge>{round.status}</Badge>
                        </div>
                        {code === "R1A" ? (
                          <p>
                            Question {round.position || 0} / {round.rules.r1a.question_count} ·{" "}
                            {round.question_active
                              ? "answer window open"
                              : "no open answer window"}
                            {round.question_deadline && (
                              <>
                                {" "}
                                ·{" "}
                                {time(
                                  new Date(round.question_deadline) -
                                    new Date(round.paused_at || d.server_now),
                                )}{" "}
                                remaining at last update
                              </>
                            )}
                          </p>
                        ) : (
                          <p>
                            {round.timer_mode === "PAUSE_DURING_HANDOVER"
                              ? "Clock pauses during handovers"
                              : "Continuous team clock"}
                          </p>
                        )}
                        <div className="actions">
                          {roundActions(round).includes("validate") && (
                            <BusyButton
                              label="Validate setup"
                              onClick={() => validate(code)}
                            />
                          )}{" "}
                          {roundActions(round)
                            .filter((a) => a !== "validate")
                            .map((a) => (
                              <BusyButton
                                key={a}
                                label={
                                  a === "end"
                                    ? "End permanently"
                                    : a[0].toUpperCase() + a.slice(1)
                                }
                                kind={
                                  a === "end"
                                    ? "danger"
                                    : a === "start"
                                      ? "primary"
                                      : ""
                                }
                                onClick={() => action(round, a)}
                              />
                            ))}
                        </div>
                        {code === "R1A" && (
                          <div className="next-question">
                            <BusyButton
                              label={`${(round.position || 0) >= round.rules.r1a.question_count ? "All questions revealed" : "Reveal question " + ((round.position || 0) + 1)}`}
                              disabled={
                                round.status !== "LIVE" ||
                                round.question_active ||
                                (round.position || 0) >= round.rules.r1a.question_count
                              }
                              onClick={() =>
                                confirm({
                                  title: `Reveal question ${(round.position || 0) + 1}?`,
                                  description:
                                    `The shared ${round.rules.r1a.question_duration_ms / 1000}-second timer begins immediately.`,
                                  action: () =>
                                    run(
                                      () =>
                                        request("/api/admin/r1a/next", {
                                          method: "POST",
                                          body: {
                                            expected_position:
                                              round.position || 0,
                                          },
                                        }),
                                      "Question revealed.",
                                    ),
                                })
                              }
                            />
                            <small>
                              Questions 2–{round.rules.r1a.question_count} are revealed manually after the
                              previous window closes.
                            </small>
                          </div>
                        )}
                      </article>
                    );
                  })}
                </div>
              )}
              <div className="stats">
                <span>
                  Current R1A accepted <b>{d.r1a_progress?.accepted ?? 0}</b>
                </span>
                <span>
                  Unanswered <b>{d.r1a_progress?.unanswered ?? 0}</b>
                </span>
                <span>
                  Finalized <b>{d.r1a_progress?.finalized ?? 0}</b>
                </span>
              </div>
            </>
          )}
        </DataState>
      </Panel>
      {validation && (
        <Panel title={roundName(validation.round_code) + " validation"}>
          <Message error={!validation.valid}>
            {validation.valid
              ? "Ready to start."
              : "Resolve these issues before starting."}
          </Message>
          {validation.issues?.map((v, i) => (
            <p className="validation-issue" key={i}>
              <b>{v.field}</b> · {v.message}
            </p>
          ))}
        </Panel>
      )}
      <Panel
        title="R1B clock policy"
        description="Player 1 Ready starts the configured team budget. Mode is locked after the round starts."
      >
        <DataState r={timer}>
          {(t) => (
            <form
              className="inline-form"
              onSubmit={(e) => {
                e.preventDefault();
                run(
                  () =>
                    request("/api/admin/r1b/timer", {
                      method: "PUT",
                      body: { timer_mode: mode || t.timer_mode },
                    }),
                  "Timer mode saved.",
                );
              }}
            >
              <Field label="Timer mode">
                <select
                  disabled={t.locked}
                  value={t.locked ? t.timer_mode : mode || t.timer_mode}
                  onChange={(e) => setMode(e.target.value)}
                >
                  <option value="CONTINUOUS">
                    Continuous · handovers consume time
                  </option>
                  <option value="PAUSE_DURING_HANDOVER">
                    Pause during handovers
                  </option>
                </select>
              </Field>
              <BusyButton
                type="submit"
                label={t.locked ? "Locked" : "Save mode"}
                disabled={t.locked}
              />
            </form>
          )}
        </DataState>
      </Panel>
      <Panel
        title="Relay progress"
        description="Session presence is a server lease, not proof that a browser is open."
      >
        <DataState r={progress}>
          {(d) => (
            <>
              <Table
                columns={[
                  {
                    key: "team_code",
                    label: "Team",
                    render: (x) => (
                      <>
                        {x.team_code}
                        <small className="block">{x.team_name}</small>
                      </>
                    ),
                  },
                  {
                    key: "current_player",
                    label: "Player / question",
                    render: (x) =>
                      `${x.current_player} / ${x.current_question_position}`,
                  },
                  {
                    key: "turn_status",
                    label: "State",
                    render: (x) => <Badge>{x.turn_status}</Badge>,
                  },
                  {
                    key: "remaining_ms",
                    label: "Time",
                    render: (x) =>
                      time(x.remaining_ms) +
                      (x.timer_frozen ? " · frozen" : ""),
                  },
                  { key: "active_session_count", label: "Sessions" },
                ]}
                rows={d.r1b_teams.items}
              />
              <Pager
                offset={offset}
                limit={25}
                total={d.r1b_teams.total}
                setOffset={setOffset}
              />
            </>
          )}
        </DataState>
      </Panel>
      <DataState r={r}>
        {(d) =>
          d.environment === "development" ? (
            <section className="panel maintenance-panel">
              <div className="panel-head">
                <div>
                  <span className="eyebrow">DEVELOPMENT MAINTENANCE</span>
                  <h2>Reset Round 1</h2>
                  <p>
                    Resets R1A and R1B together. This is rejected while either
                    round is live; pause or end live rounds first.
                  </p>
                </div>
              </div>
              <div className="maintenance-details">
                <p>
                  <b>Cleared:</b> submissions, score events, numerical drafts,
                  relay progress, enrolments, published leaderboard snapshots,
                  all lifecycle and question-clock timestamps, and current
                  positions. All player sessions are revoked.
                </p>
                <p>
                  <b>Kept:</b> teams, credentials, activation details,
                  questions, options, media, admin audit history, and the
                  selected R1B timer mode. Database sequences are unchanged.
                </p>
              </div>
              <form className="reset-form" onSubmit={resetRoundOne}>
                <Field label="Audited reason">
                  <textarea
                    name="reason"
                    rows={3}
                    required
                    maxLength={500}
                    disabled={busy}
                    placeholder="Why is this development reset required?"
                  />
                </Field>
                <Field
                  label="Type RESET ROUND 1 to confirm"
                  hint="The text is case-sensitive and must match exactly."
                >
                  <input
                    name="confirmation"
                    required
                    autoComplete="off"
                    disabled={busy}
                    pattern="RESET ROUND 1"
                  />
                </Field>
                <BusyButton
                  type="submit"
                  kind="danger"
                  label="Reset both rounds"
                />
              </form>
            </section>
          ) : null
        }
      </DataState>
    </>
  );
}
function ImageEditor({ kind, id, url, locked }) {
  const { request, run } = useApp(),
    file = useRef(null);
  return (
    <div className="image-editor">
      {url && (
        <a href={url} target="_blank" rel="noopener">
          <img src={url} alt="Uploaded question or option" />
        </a>
      )}
      <Field
        label={url ? "Replace image" : "Attach image"}
        hint="PNG/JPEG, up to 5 MB."
      >
        <input
          ref={file}
          type="file"
          accept="image/png,image/jpeg"
          disabled={locked}
        />
      </Field>
      <BusyButton
        label="Upload image"
        disabled={locked}
        onClick={() =>
          run(async () => {
            const image = file.current.files[0];
            if (!image) throw Error("Choose an image first.");
            if (image.size > 5 * 1024 * 1024)
              throw Error("Image must be at most 5 MB.");
            const body = new FormData();
            body.append("image", image);
            await request(`/api/admin/${kind}/${id}/image`, {
              method: "POST",
              body,
            });
            file.current.value = "";
            return "Image uploaded.";
          })
        }
      />
      {url && (
        <p className="help">
          Deleting a question/option does not remove its stored media file.
        </p>
      )}
    </div>
  );
}
function QuestionForm({ question, round, locked, onSaved, maxPosition }) {
  const { run, request } = useApp(),
    isNew = !question,
    numeric = round === "R1B";
  async function save(e) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const body = {
      position: Number(f.get("position")),
      question_text: f.get("question_text") || null,
      enabled: f.get("enabled") === "on",
    };
    if (numeric)
      Object.assign(body, {
        correct_numeric: f.get("correct_numeric"),
        tolerance_abs: f.get("tolerance_abs"),
        tolerance_rel: f.get("tolerance_rel"),
      });
    if (isNew)
      Object.assign(body, {
        round_code: round,
        answer_type: numeric ? "NUMERIC" : "MCQ",
      });
    await run(async () => {
      const q = await request(
        "/api/admin/questions" + (isNew ? "" : "/" + question.id),
        { method: isNew ? "POST" : "PUT", body },
      );
      onSaved(q.id || question.id);
      return isNew
        ? "Question created. Add options and images below."
        : "Question saved.";
    });
  }
  return (
    <form onSubmit={save}>
      <fieldset disabled={locked}>
        <div className="form-grid">
          <Field label="Question position">
            <input
              name="position"
              type="number"
              min={1}
              max={maxPosition}
              defaultValue={question?.position || 1}
              required
            />
          </Field>
          <label className="check">
            <input
              type="checkbox"
              name="enabled"
              defaultChecked={question?.enabled ?? true}
            />{" "}
            Enabled
          </label>
        </div>
        <Field
          label="Question text"
          hint="Equations can use $...$ or $$...$$. Image-only questions are supported."
        >
          <textarea
            name="question_text"
            rows={5}
            defaultValue={question?.question_text || ""}
          />
        </Field>
        {numeric && (
          <div className="form-grid">
            <Field label="Correct numerical answer">
              <input
                name="correct_numeric"
                defaultValue={question?.correct_numeric ?? ""}
                required
              />
            </Field>
            <Field label="Absolute tolerance">
              <input
                name="tolerance_abs"
                defaultValue={question?.tolerance_abs ?? "0"}
                required
              />
            </Field>
            <Field label="Relative tolerance">
              <input
                name="tolerance_rel"
                defaultValue={question?.tolerance_rel ?? "0"}
                required
              />
            </Field>
          </div>
        )}
        <BusyButton
          type="submit"
          kind="primary"
          label={isNew ? "Create question" : "Save question"}
          disabled={locked}
        />
      </fieldset>
    </form>
  );
}
function OptionEditor({ option, correct, locked }) {
  const { run, request, confirm } = useApp();
  return (
    <div className="option-editor">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          run(
            () =>
              request("/api/admin/options/" + option.id, {
                method: "PUT",
                body: {
                  position: Number(f.get("position")),
                  option_text: f.get("option_text") || null,
                  is_correct: f.get("is_correct") === "on",
                },
              }),
            "Option saved.",
          );
        }}
      >
        <fieldset disabled={locked}>
          <div className="form-grid">
            <Field label={"Option " + option.position + " text"}>
              <textarea
                name="option_text"
                rows={2}
                defaultValue={option.option_text || ""}
              />
            </Field>
            <Field label={"Option " + option.position + " position"}>
              <input
                type="number"
                name="position"
                min={1}
                max={20}
                defaultValue={option.position}
                required
              />
            </Field>
          </div>
          <label className="check">
            <input type="checkbox" name="is_correct" defaultChecked={correct} />{" "}
            Correct option
          </label>
          <div className="actions">
            <BusyButton label="Save option" type="submit" disabled={locked} />
            <BusyButton
              label="Delete option"
              kind="danger"
              disabled={locked}
              onClick={() =>
                confirm({
                  title: "Delete option " + option.position + "?",
                  description:
                    "This removes the pre-start option. Revalidate the question afterward.",
                  danger: true,
                  action: () =>
                    run(
                      () =>
                        request("/api/admin/options/" + option.id, {
                          method: "DELETE",
                        }),
                      "Option deleted.",
                    ),
                })
              }
            />
          </div>
        </fieldset>
      </form>
      <ImageEditor
        kind="options"
        id={option.id}
        url={option.image_url}
        locked={locked}
      />
    </div>
  );
}
function QuestionDetail({ id, round, onSaved, onClose, locked, maxPosition }) {
  const { run, request, confirm } = useApp(),
    q = useData(id === "new" ? null : "/api/admin/questions/" + id),
    [adding, setAdding] = useState(false);
  const content = (data) => (
    <>
      <QuestionForm
        question={data}
        round={round}
        locked={locked}
        onSaved={onSaved}
        maxPosition={maxPosition}
      />
      {data && (
        <>
          <ImageEditor
            kind="questions"
            id={data.id}
            url={data.question_image_url}
            locked={locked}
          />
          {round === "R1A" && (
            <>
              <h3 className="subheading">Answer options</h3>
              {data.options?.map((o) => (
                <OptionEditor
                  key={o.id}
                  option={o}
                  correct={data.correct_option_id === o.id}
                  locked={locked}
                />
              ))}
              <form
                className="new-option"
                onSubmit={(e) => {
                  e.preventDefault();
                  const f = new FormData(e.currentTarget);
                  run(
                    () =>
                      request("/api/admin/questions/" + data.id + "/options", {
                        method: "POST",
                        body: {
                          position: Number(f.get("position")),
                          option_text: f.get("option_text") || null,
                          is_correct: f.get("is_correct") === "on",
                        },
                      }),
                    "Option added. You can attach its image below.",
                  );
                }}
              >
                <fieldset disabled={locked}>
                  <h3>Add an option</h3>
                  <div className="form-grid">
                    <Field label="New option text">
                      <input name="option_text" />
                    </Field>
                    <Field label="New option position">
                      <input
                        name="position"
                        type="number"
                        min={1}
                        max={20}
                        defaultValue={Math.min(
                          20,
                          Math.max(
                            0,
                            ...(data.options || []).map((o) => o.position),
                          ) + 1,
                        )}
                        required
                      />
                    </Field>
                  </div>
                  <label className="check">
                    <input type="checkbox" name="is_correct" /> Mark correct
                  </label>
                  <BusyButton
                    label="Add option"
                    type="submit"
                    disabled={locked}
                  />
                  <p className="help">
                    For image-only options, add the option first, then attach
                    its image.
                  </p>
                </fieldset>
              </form>
            </>
          )}
          <div className="delete-row">
            <BusyButton
              label="Delete question"
              kind="danger"
              disabled={locked}
              onClick={() =>
                confirm({
                  title: "Delete this question?",
                  description:
                    "This removes the pre-start question and its options. Historical media files are retained.",
                  danger: true,
                  action: () =>
                    run(async () => {
                      await request("/api/admin/questions/" + data.id, {
                        method: "DELETE",
                      });
                      onClose();
                      return "Question deleted.";
                    }),
                })
              }
            />
          </div>
        </>
      )}
    </>
  );
  return (
    <Panel
      title={id === "new" ? "New " + roundName(round) : "Question editor"}
      description={
        locked
          ? "This round has started. Content is read-only."
          : "Content locks when this round starts."
      }
      actions={
        <Button kind="small" onClick={onClose}>
          Close editor
        </Button>
      }
    >
      {id === "new" ? content(null) : <DataState r={q}>{content}</DataState>}
    </Panel>
  );
}
function Questions() {
  const [round, setRound] = useState("R1A"),
    [id, setId] = useState(null);
  const r = useData("/api/admin/questions?round_code=" + round + "&limit=100"),
    d = useData("/api/admin/dashboard");
  const locked =
    d.data?.rounds.find((x) => x.code === round)?.status !== "DRAFT";
  const selectedRound = d.data?.rounds.find((x) => x.code === round);
  const maxPosition = round === "R1A"
    ? selectedRound?.rules?.r1a?.question_count
    : selectedRound?.rules?.r1b?.turn_count;
  return (
    <>
      <Panel
        title="Questions"
        description="Create the question, then attach images and options. Use Overview validation before starting."
        actions={
          <div className="actions">
            <select
              aria-label="Question round"
              value={round}
              onChange={(e) => {
                setRound(e.target.value);
                setId(null);
              }}
            >
              <option value="R1A">WizTime / R1A</option>
              <option value="R1B">Math Rally / R1B</option>
            </select>
            <Button disabled={locked} onClick={() => setId("new")}>
              New question
            </Button>
            <Button kind="small" onClick={r.reload}>
              Refresh
            </Button>
          </div>
        }
      >
        <DataState r={r}>
          {(data) => (
            <Table
              columns={[
                { key: "position", label: "#" },
                {
                  key: "question_text",
                  label: "Question",
                  render: (q) => (
                    <span className="excerpt">
                      {q.question_text || "(image question)"}
                    </span>
                  ),
                },
                { key: "answer_type", label: "Type" },
                {
                  key: "enabled",
                  label: "Status",
                  render: (q) => (
                    <Badge>{q.enabled ? "Enabled" : "Disabled"}</Badge>
                  ),
                },
                {
                  key: "edit",
                  label: "",
                  render: (q) => (
                    <Button kind="small" onClick={() => setId(q.id)}>
                      {locked ? "View" : "Edit"}
                    </Button>
                  ),
                },
              ]}
              rows={data.items}
              empty="No questions. Initialize round records in Overview if needed."
            />
          )}
        </DataState>
      </Panel>
      {id && (
        <QuestionDetail
          key={round + ":" + id}
          id={id}
          round={round}
          locked={locked}
          maxPosition={maxPosition}
          onSaved={setId}
          onClose={() => setId(null)}
        />
      )}
    </>
  );
}
function TeamDetail({ id, onClose, onSessions, onActivation }) {
  const { run, request, confirm } = useApp(),
    r = useData("/api/admin/teams/" + id),
    dashboard = useData("/api/admin/dashboard");
  const locked =
    dashboard.data?.rounds.some((x) => x.status !== "DRAFT") ?? true;
  return (
    <Panel
      title={"Team #" + id}
      actions={
        <Button kind="small" onClick={onClose}>
          Close
        </Button>
      }
    >
      <DataState r={r}>
        {(t) => (
          <>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                const f = new FormData(e.currentTarget);
                const body = {
                  code: f.get("code"),
                  name: f.get("name"),
                  username: f.get("username"),
                };
                if (!locked) body.enabled = f.get("enabled") === "on";
                run(
                  () =>
                    request("/api/admin/teams/" + id, { method: "PUT", body }),
                  "Team saved.",
                );
              }}
            >
              <div className="form-grid">
                <Field label="Team code">
                  <input
                    name="code"
                    required
                    maxLength={32}
                    defaultValue={t.code}
                  />
                </Field>
                <Field label="Team name">
                  <input
                    name="name"
                    required
                    maxLength={120}
                    defaultValue={t.name}
                  />
                </Field>
                <Field label="Login username">
                  <input
                    name="username"
                    required
                    maxLength={120}
                    defaultValue={t.username}
                  />
                </Field>
              </div>
              <label className="check">
                <input
                  type="checkbox"
                  name="enabled"
                  defaultChecked={t.enabled}
                  disabled={locked}
                />{" "}
                Enabled{" "}
                {locked && (
                  <small>· locked after competition enrolment starts</small>
                )}
              </label>
              <p className="help">
                Enrolled rounds: {t.enrolled_rounds?.join(", ") || "none"} ·
                Created {date(t.created_at)}
              </p>
              <div className="actions">
                <BusyButton type="submit" label="Save team" kind="primary" />
                <Button onClick={() => onSessions(id)}>Manage sessions</Button>
              </div>
            </form>
            <form
              className="password-reset"
              onSubmit={(e) => {
                e.preventDefault();
                const input = e.currentTarget.elements.new_password;
                const value = input.value;
                confirm({
                  title: "Reset this team’s password?",
                  description:
                    "This revokes all active team sessions. Scores and progress remain unchanged.",
                  danger: true,
                  action: () =>
                    run(async () => {
                      await request(
                        "/api/admin/teams/" + id + "/password-reset",
                        { method: "POST", body: { new_password: value } },
                      );
                      input.value = "";
                      return "Team password reset; sessions revoked.";
                    }),
                });
              }}
            >
              <Field label="New team password">
                <input
                  name="new_password"
                  type="password"
                  minLength={8}
                  maxLength={128}
                  required
                  autoComplete="new-password"
                />
              </Field>
              <BusyButton
                label="Reset password & revoke sessions"
                type="submit"
                kind="danger"
              />
            </form>
            {!t.has_password && (
              <form className="password-reset" onSubmit={(e) => {
                e.preventDefault();
                const reason = e.currentTarget.elements.reason.value.trim();
                confirm({
                  title: "Regenerate activation code?",
                  description: "The previous code stops working immediately. The new code is shown once.",
                  action: () => run(async () => {
                    let d;
                    try {
                      d = await request(`/api/admin/teams/${id}/activation/regenerate`, {method:"POST", body:{reason}});
                    } catch (error) {
                      if (error.status === 404 && error.code === "TEAM_NOT_FOUND")
                        throw new Error("This team no longer exists. Refresh the team directory.");
                      if (error.status === 404)
                        throw new Error("Activation regeneration is unavailable on the running server. Restart this Round 1 project with ./start.sh start.");
                      throw error;
                    }
                    onActivation({...d, code:t.code, name:t.name});
                    return "Activation code regenerated.";
                  }),
                });
              }}>
                <Field label="Regeneration reason" hint="Required confirmation. The authenticated action and team ID are audited; code and reason text are not.">
                  <input name="reason" required maxLength={500} />
                </Field>
                <BusyButton label="Regenerate activation code" type="submit" />
              </form>
            )}
          </>
        )}
      </DataState>
    </Panel>
  );
}
function Teams() {
  const { run, request, go } = useApp(),
    [offset, setOffset] = useState(0),
    [selected, setSelected] = useState(null),
    [creating, setCreating] = useState(false),
    [activation, setActivation] = useState(null);
  const r = useData("/api/admin/teams?offset=" + offset + "&limit=25");
  return (
    <>
      <Panel
        title="Teams"
        description="Team passwords cannot be retrieved. Reset an existing password from the team editor."
        actions={
          <div className="actions">
            <Button onClick={() => setCreating(!creating)}>
              {creating ? "Close creation" : "Create team"}
            </Button>
            <Button kind="small" onClick={r.reload}>
              Refresh
            </Button>
          </div>
        }
      >
        <DataState r={r}>
          {(d) => (
            <>
              <Table
                columns={[
                  {
                    key: "code",
                    label: "Team",
                    render: (t) => (
                      <>
                        <b>{t.code}</b>
                        <small className="block">{t.name}</small>
                      </>
                    ),
                  },
                  { key: "username", label: "Username" },
                  {
                    key: "points",
                    label: "Combined points",
                    render: (t) => points(t.points),
                  },
                  { key: "submission_count", label: "Outcomes" },
                  { key: "active_session_count", label: "Leased sessions" },
                  {
                    key: "edit",
                    label: "",
                    render: (t) => (
                      <div className="actions">
                        <Button kind="small" onClick={() => setSelected(t.id)}>
                          Edit
                        </Button>
                        <Button
                          kind="small"
                          onClick={() => go("sessions", t.id)}
                        >
                          Sessions
                        </Button>
                      </div>
                    ),
                  },
                ]}
                rows={d.items}
              />
              <Pager
                offset={offset}
                total={d.total}
                limit={25}
                setOffset={setOffset}
              />
            </>
          )}
        </DataState>
      </Panel>
      {creating && (
        <Panel title="Create a team">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              run(async () => {
                const d = await request("/api/admin/teams", {
                  method: "POST",
                  body: {
                    code: f.get("code"),
                    name: f.get("name"),
                    username: f.get("username"),
                    password: f.get("password") || null,
                  },
                });
                setActivation(d.activation_token ? d : null);
                setCreating(false);
                setSelected(d.id);
                return "Team created.";
              });
            }}
          >
            <div className="form-grid">
              <Field label="Team code">
                <input name="code" required maxLength={32} />
              </Field>
              <Field label="Team name">
                <input name="name" required maxLength={120} />
              </Field>
              <Field label="Login username">
                <input name="username" required maxLength={120} />
              </Field>
              <Field
                label="Assigned password (optional)"
                hint="Leave blank to issue a one-time activation token."
              >
                <input
                  name="password"
                  type="password"
                  minLength={8}
                  maxLength={128}
                  autoComplete="new-password"
                />
              </Field>
            </div>
            <BusyButton label="Create team" type="submit" kind="primary" />
          </form>
        </Panel>
      )}
      {activation && (
        <Panel
          title="Save this activation code privately"
          description="Shown only when generated in this browser. It is not a password."
        >
          <p>
            <b>
              {activation.code} · {activation.name}
            </b>
          </p>
          <code className="activation-secret">
            {activation.activation_token}
          </code>
          <p className="help">
            Login username: <b>{activation.username}</b> · Expires {date(activation.activation_expires_at)}. Share directly with the team.
          </p>
          <div className="actions">
            <Button kind="small" onClick={() => navigator.clipboard.writeText(activation.activation_token)}>Copy code</Button>
            <Button kind="small" onClick={() => setActivation(null)}>Hide code</Button>
          </div>
        </Panel>
      )}
      {selected && (
        <TeamDetail
          key={selected}
          id={selected}
          onClose={() => setSelected(null)}
          onSessions={(id) => go("sessions", id)}
          onActivation={setActivation}
        />
      )}
    </>
  );
}
function Sessions() {
  const { request, run, confirm, selectedTeam, setSelectedTeam } = useApp(),
    [entered, setEntered] = useState(String(selectedTeam || "")),
    [reason, setReason] = useState("");
  const r = useData(
    selectedTeam ? "/api/admin/teams/" + selectedTeam + "/sessions" : null,
  );
  function revoke(sid) {
    if (!reason.trim())
      throw Error("Enter an audited revocation reason first.");
    confirm({
      title: sid
        ? "Revoke this session?"
        : "Revoke every session for this team?",
      description:
        "This frees login slots. Team scores and relay progress remain unchanged.",
      danger: true,
      action: () =>
        run(
          () =>
            request(
              "/api/admin/teams/" +
                selectedTeam +
                (sid
                  ? "/sessions/" + encodeURIComponent(sid) + "/revoke"
                  : "/sessions/revoke-all"),
              { method: "POST", body: { reason: reason.trim() } },
            ),
          "Session revocation completed.",
        ),
    });
  }
  const [localError, setLocalError] = useState("");
  return (
    <Panel
      title="Player sessions"
      description="R1A permits one leased session; R1B permits four relay slots. Closed-browser detection uses lease expiry, not a physical-device fingerprint."
    >
      <form
        className="inline-form"
        onSubmit={(e) => {
          e.preventDefault();
          setLocalError("");
          setSelectedTeam(Number(entered));
          r.reload();
        }}
      >
        <Field label="Team ID">
          <input
            type="number"
            min={1}
            required
            value={entered}
            onChange={(e) => setEntered(e.target.value)}
          />
        </Field>
        <Button type="submit">Load sessions</Button>
      </form>
      <Field label="Revocation reason">
        <input
          maxLength={500}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="e.g. replacement phone after battery failure"
        />
      </Field>
      <Message error>{localError}</Message>
      {selectedTeam ? (
        <>
          <div className="actions">
            <BusyButton
              label="Revoke all team sessions"
              kind="danger"
              disabled={!reason.trim()}
              onClick={() => {
                try {
                  revoke(null);
                } catch (e) {
                  setLocalError(e.message);
                }
              }}
            />
            <Button kind="small" onClick={r.reload}>
              Refresh
            </Button>
          </div>
          <DataState r={r}>
            {(d) => (
              <Table
                columns={[
                  { key: "round_code", label: "Round" },
                  { key: "slot", label: "Slot" },
                  {
                    key: "session_id",
                    label: "Session",
                    render: (s) => (
                      <code title={s.session_id}>
                        {s.session_id.slice(0, 12)}…
                      </code>
                    ),
                  },
                  {
                    key: "active",
                    label: "Lease",
                    render: (s) => (
                      <Badge>{s.active ? "Active" : "Inactive"}</Badge>
                    ),
                  },
                  {
                    key: "last_seen_at",
                    label: "Last heartbeat",
                    render: (s) => date(s.last_seen_at),
                  },
                  {
                    key: "lease_expires_at",
                    label: "Lease expires",
                    render: (s) => date(s.lease_expires_at),
                  },
                  { key: "revocation_reason", label: "Revocation reason" },
                  {
                    key: "action",
                    label: "",
                    render: (s) => (
                      <BusyButton
                        kind="danger small"
                        label="Revoke"
                        disabled={!s.active || !reason.trim()}
                        onClick={() => revoke(s.session_id)}
                      />
                    ),
                  },
                ]}
                rows={d.items}
              />
            )}
          </DataState>
        </>
      ) : (
        <p className="empty">
          Load a team ID, or open Sessions from the Teams tab.
        </p>
      )}
    </Panel>
  );
}
function Submissions() {
  const [filter, setFilter] = useState({ round: "", position: "", team: "" }),
    [applied, setApplied] = useState(filter),
    [offset, setOffset] = useState(0),
    [auto, setAuto] = useState(true);
  const params = new URLSearchParams({ offset, limit: 50 });
  if (applied.round) params.set("round_code", applied.round);
  if (applied.position) params.set("position", applied.position);
  if (applied.team) params.set("team_id", applied.team);
  const r = useData("/api/admin/submissions?" + params, {
    interval: auto ? 3000 : 0,
  });
  const rulesData = useData("/api/admin/dashboard");
  const roundRules = rulesData.data?.rounds || [];
  const r1aCount = roundRules.find((x) => x.code === "R1A")?.rules?.r1a?.question_count;
  const r1bCount = roundRules.find((x) => x.code === "R1B")?.rules?.r1b?.turn_count;
  const maxPosition = filter.round === "R1B" ? r1bCount
    : filter.round === "R1A" ? r1aCount : Math.max(r1aCount || 0, r1bCount || 0) || undefined;
  return (
    <Panel
      title="Question outcomes"
      description="Accepted submissions, skips and zero-point timeouts. Timestamps use your browser’s timezone."
    >
      <form
        className="filter-row"
        onSubmit={(e) => {
          e.preventDefault();
          setApplied({ ...filter });
          setOffset(0);
          r.reload();
        }}
      >
        <Field label="Round">
          <select
            value={filter.round}
            onChange={(e) => setFilter({ ...filter, round: e.target.value })}
          >
            <option value="">Both</option>
            <option value="R1A">R1A</option>
            <option value="R1B">R1B</option>
          </select>
        </Field>
        <Field label="Question">
          <input
            type="number"
            min={1}
            max={maxPosition}
            value={filter.position}
            onChange={(e) => setFilter({ ...filter, position: e.target.value })}
            placeholder="All"
          />
        </Field>
        <Field label="Team ID">
          <input
            type="number"
            min={1}
            value={filter.team}
            onChange={(e) => setFilter({ ...filter, team: e.target.value })}
            placeholder="All"
          />
        </Field>
        <Button type="submit">Apply filters</Button>
      </form>
      <label className="check">
        <input
          type="checkbox"
          checked={auto}
          onChange={(e) => setAuto(e.target.checked)}
        />{" "}
        Refresh every 3 seconds
      </label>
      <DataState r={r}>
        {(d) => (
          <>
            <Table
              columns={[
                {
                  key: "team_code",
                  label: "Team",
                  render: (s) => (
                    <>
                      {s.team_code}
                      <small className="block">{s.team_name}</small>
                    </>
                  ),
                },
                {
                  key: "round_code",
                  label: "Round / question",
                  render: (s) =>
                    `${s.round_code} / ${s.position}${s.player_number ? " · P" + s.player_number : ""}`,
                },
                {
                  key: "answer",
                  label: "Input",
                  render: (s) =>
                    s.option_id ? (
                      <>
                        <span>{s.option_text || "(image option)"}</span>
                        {s.option_image_url && (
                          <a
                            href={s.option_image_url}
                            target="_blank"
                            rel="noopener"
                          >
                            {" "}
                            View image ↗
                          </a>
                        )}
                      </>
                    ) : (
                      text(s.answer)
                    ),
                },
                {
                  key: "result",
                  label: "Result",
                  render: (s) => <Badge>{s.result}</Badge>,
                },
                {
                  key: "points",
                  label: "Points",
                  render: (s) => points(s.points),
                },
                {
                  key: "submitted_at",
                  label: "Submitted / timed out",
                  render: (s) => (
                    <>
                      {date(s.submitted_at || s.timed_out_at)}
                      {!s.submitted_at && (
                        <small className="block">
                          Timeout, not a player submission
                        </small>
                      )}
                    </>
                  ),
                },
                {
                  key: "recorded_at",
                  label: "Recorded",
                  render: (s) => date(s.recorded_at),
                },
                {
                  key: "finalization_source",
                  label: "Source",
                  render: (s) => (
                    <>
                      {s.finalization_source || "MANUAL"}
                      {s.draft_version != null && (
                        <small className="block">
                          Draft v{s.draft_version}
                        </small>
                      )}
                    </>
                  ),
                },
                {
                  key: "elapsed_ms",
                  label: "Elapsed",
                  render: (s) =>
                    s.elapsed_ms == null
                      ? "—"
                      : (s.elapsed_ms / 1000).toFixed(3) + " s",
                },
              ]}
              rows={d.items}
            />
            <Pager
              offset={offset}
              limit={50}
              total={d.total}
              setOffset={setOffset}
            />
          </>
        )}
      </DataState>
    </Panel>
  );
}
function Results() {
  const automatic=useData("/api/admin/leaderboard/current?round_code=R1A",{interval:3000});
  const { request, run, confirm } = useApp(),
    [scope, setScope] = useState("BOTH"),
    [publication, setPublication] = useState(null);
  function publish(round) {
    confirm({
      title: "Publish " + roundName(round) + " scores?",
      description:
        "This creates an immutable snapshot visible to players. Publishing again creates a new snapshot.",
      action: () =>
        run(async () => {
          const p = await request("/api/admin/leaderboard/publish", {
            method: "POST",
            body: { round_code: round },
          });
          setPublication(p);
          return "Leaderboard snapshot published.";
        }),
    });
  }
  async function download() {
    await run(async () => {
      const d = await request(
          "/api/admin/exports/results.xlsx?scope=" + scope,
          { download: true },
        ),
        url = URL.createObjectURL(d.blob),
        a = document.createElement("a");
      a.href = url;
      a.download = d.name;
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      return "Excel workbook downloaded.";
    });
  }
  return (
    <>
      <Panel
        title="Leaderboard reveal"
        description="R1A scores and leaderboard reveal automatically after each configured question window. The next question still requires the organiser’s reveal."
      >
        <div className="actions">
          <span className="help">WizTime: automatic · no Publish step</span>
          <BusyButton label="Publish R1B" onClick={() => publish("R1B")} />
        </div>
      </Panel>
      <Panel title="Latest automatic WizTime leaderboard">
        <DataState r={automatic}>{b=>b.published?<><p className="help">After question {b.after_question} · {date(b.published_at)}</p><Table columns={[{key:"rank",label:"Rank"},{key:"team_name",label:"Team"},{key:"score",label:"Score",render:r=>points(r.score)}]} rows={b.data}/></>:<p className="empty">Appears after the first question closes.</p>}</DataState>
      </Panel>
      <Panel
        title="Results export"
        description="A real Excel workbook with totals, question outcomes, enrolment, published snapshots and admin audit."
      >
        <div className="inline-form">
          <Field label="Export scope">
            <select value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="BOTH">Both rounds</option>
              <option value="R1A">R1A only</option>
              <option value="R1B">R1B only</option>
            </select>
          </Field>
          <BusyButton
            label="Download Excel"
            kind="primary"
            onClick={download}
          />
        </div>
        <p className="help">
          Database/media backups remain available through start.sh. They are
          separate from result exports.
        </p>
      </Panel>
      {publication && (
        <Panel
          title="Snapshot published in this session"
          description={`Publication #${publication.publication_id} · ${publication.scope?.round_code} · ${date(publication.published_at)}`}
        >
          <Table
            columns={[
              { key: "rank", label: "Rank" },
              { key: "team_code", label: "Team code" },
              { key: "team_name", label: "Team name" },
              { key: "score", label: "Score", render: (r) => points(r.score) },
            ]}
            rows={publication.data || []}
          />
        </Panel>
      )}
    </>
  );
}
function Audit() {
  const [offset, setOffset] = useState(0),
    r = useData("/api/admin/audit?offset=" + offset + "&limit=50");
  return (
    <Panel
      title="Admin audit log"
      description="Attributed changes recorded by the backend."
      actions={
        <Button kind="small" onClick={r.reload}>
          Refresh
        </Button>
      }
    >
      <DataState r={r}>
        {(d) => (
          <>
            <Table
              columns={[
                { key: "id", label: "#" },
                {
                  key: "created_at",
                  label: "Time",
                  render: (x) => date(x.created_at),
                },
                {
                  key: "admin_username",
                  label: "Admin",
                  render: (x) =>
                    x.admin_username || "Unattributed legacy record",
                },
                { key: "action", label: "Action" },
                {
                  key: "details",
                  label: "Details",
                  render: (x) => (
                    <span className="audit-details">{x.details}</span>
                  ),
                },
              ]}
              rows={d.items}
            />
            <Pager
              offset={offset}
              limit={50}
              total={d.total}
              setOffset={setOffset}
            />
          </>
        )}
      </DataState>
    </Panel>
  );
}
function App() {
  const [token, setToken] = useState(() => localStorage.getItem("adminToken")),
    [tab, setTab] = useState(() =>
      NAV.some((n) => n[0] === location.hash.slice(1))
        ? location.hash.slice(1)
        : "overview",
    ),
    [revision, setRevision] = useState(0),
    [busy, setBusy] = useState(false),
    [toast, setToast] = useState(null),
    [confirmation, setConfirmation] = useState(null),
    [selectedTeam, setSelectedTeam] = useState(null),
    [notice, setNotice] = useState("");
  const gate = useRef(false),
    session = useRef(token);
  session.current = token;
  const logout = useCallback((msg = "") => {
    session.current = null;
    localStorage.removeItem("adminToken");
    setToken(null);
    setConfirmation(null);
    setToast(null);
    setNotice(msg);
  }, []);
  const request = useCallback(
    async (path, opts = {}) => {
      const current = session.current;
      try {
        return await api(path, { ...opts, token: current });
      } catch (e) {
        if (e.status === 401 && current === session.current)
          logout("Your admin session expired. Sign in again.");
        if (e.status === 403 && e.code === "ADMIN_REQUIRED")
          logout("This token is not an admin session.");
        throw e;
      }
    },
    [logout],
  );
  const run = useCallback(async (fn, message) => {
    if (gate.current) return false;
    gate.current = true;
    setBusy(true);
    setToast(null);
    const current = session.current;
    try {
      const result = await fn();
      if (session.current === current) {
        setToast({
          text:
            typeof result === "string"
              ? result
              : message || "Action completed.",
          error: false,
        });
        setRevision((x) => x + 1);
      }
      return true;
    } catch (e) {
      if (session.current === current)
        setToast({ text: e.message, error: true });
      return false;
    } finally {
      gate.current = false;
      setBusy(false);
    }
  }, []);
  const go = useCallback((next, team) => {
    if (team) setSelectedTeam(team);
    setTab(next);
    location.hash = next;
    setToast(null);
    window.scrollTo(0, 0);
  }, []);
  useEffect(() => {
    const f = () => {
      const next = location.hash.slice(1);
      if (NAV.some((n) => n[0] === next)) setTab(next);
    };
    window.addEventListener("hashchange", f);
    const storage = (e) => {
      if (e.key === "adminToken" && e.newValue !== session.current) {
        if (!e.newValue) logout("Signed out in another tab.");
        else setToken(e.newValue);
      }
    };
    window.addEventListener("storage", storage);
    return () => {
      window.removeEventListener("hashchange", f);
      window.removeEventListener("storage", storage);
    };
  }, [logout]);
  if (!token)
    return (
      <Login
        notice={notice}
        onLogin={(t) => {
          localStorage.setItem("adminToken", t);
          session.current = t;
          setToken(t);
          setNotice("");
          setRevision((x) => x + 1);
        }}
      />
    );
  const value = {
    request,
    run,
    confirm: setConfirmation,
    busy,
    revision,
    selectedTeam,
    setSelectedTeam,
    go,
  };
  const views = {
      overview: Overview,
      questions: Questions,
      teams: Teams,
      sessions: Sessions,
      submissions: Submissions,
      results: Results,
      audit: Audit,
    },
    View = views[tab];
  return (
    <Context.Provider value={value}>
      <div className="admin-layout">
        <aside className="sidebar">
          <div className="wordmark">
            DELTA X<span>ROUND ONE / ADMIN</span>
          </div>
          <nav aria-label="Admin sections">
            {NAV.map(([id, label]) => (
              <button
                key={id}
                className={tab === id ? "active" : ""}
                aria-current={tab === id ? "page" : undefined}
                onClick={() => go(id)}
              >
                {label}
              </button>
            ))}
          </nav>
          <div className="sidebar-footer">
            <a href="/player/" target="_blank" rel="noopener">
              Open player page ↗
            </a>
            <Button kind="small" onClick={() => logout()}>
              Sign out
            </Button>
          </div>
        </aside>
        <main className="workspace">
          <header className="page-header">
            <div>
              <span className="eyebrow">COMPETITION CONTROL</span>
              <h1>{NAV.find((n) => n[0] === tab)[1]}</h1>
            </div>
            <span className="workspace-tag">Round 1</span>
          </header>
          {toast && (
            <Message error={toast.error}>
              {toast.text}
              <button
                aria-label="Dismiss message"
                className="dismiss"
                onClick={() => setToast(null)}
              >
                ×
              </button>
            </Message>
          )}
          <View />
          {confirmation && (
            <Confirm
              config={confirmation}
              busy={busy}
              error={toast?.error ? toast.text : null}
              onClose={() => setConfirmation(null)}
              onConfirm={async () => {
                const ok = await confirmation.action();
                if (ok !== false) setConfirmation(null);
              }}
            />
          )}
          <footer className="workspace-footer">
            Server rules are authoritative. Ended rounds cannot be restarted
            here.
          </footer>
        </main>
      </div>
    </Context.Provider>
  );
}
class Boundary extends React.Component {
  constructor(p) {
    super(p);
    this.state = { failed: false };
  }
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <main className="login-shell">
        <div className="login-card">
          <h1>Admin interface interrupted</h1>
          <p>Reload to reconnect. Stored competition data is unaffected.</p>
          <button className="button primary" onClick={() => location.reload()}>
            Reload
          </button>
        </div>
      </main>
    ) : (
      this.props.children
    );
  }
}
createRoot(document.getElementById("admin-root")).render(
  <Boundary>
    <App />
  </Boundary>,
);
