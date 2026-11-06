#!/usr/bin/env python3
"""Interactive, headed multi-player launcher for local Round 1 testing."""
from __future__ import annotations

import getpass
import json
import re
import signal
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class TeamCredential:
    username: str
    password: str = field(repr=False)
    label: str


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or (default or "")


def ask_int(prompt: str, default: int, low: int, high: int) -> int:
    while True:
        raw = ask(prompt, str(default))
        try:
            value = int(raw)
            if low <= value <= high:
                return value
        except ValueError:
            pass
        print(f"Enter a number from {low} to {high}.")


def yes_no(prompt: str, default: bool = False) -> bool:
    marker = "Y/n" if default else "y/N"
    value = input(f"{prompt} [{marker}]: ").strip().lower()
    return default if not value else value in {"y", "yes"}


def api(base: str, path: str, *, method: str = "GET", body=None, token=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(urljoin(base, path), data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=15) as response:
            return json.load(response)
    except HTTPError as exc:
        try:
            detail = json.load(exc).get("detail", exc.reason)
            if isinstance(detail, dict):
                detail = detail.get("message", detail)
        except Exception:
            detail = exc.reason
        raise RuntimeError(f"{method} {path} failed ({exc.code}): {detail}") from None
    except URLError as exc:
        raise RuntimeError(f"Cannot reach {base}: {exc.reason}") from None


def admin_session(base: str) -> tuple[str, dict]:
    print("Admin credentials are used only in request bodies and are not stored or printed.")
    username = ask("Admin username")
    password = getpass.getpass("Admin password: ")
    try:
        login = api(base, "/api/auth/admin/login", method="POST",
                    body={"username": username, "password": password})
    finally:
        password = ""
    token = login["access_token"]
    facts = api(base, "/api/admin/simulator/inspect", token=token)
    if facts.get("environment") != "development":
        raise RuntimeError("Automatic provisioning is refused outside development.")
    return token, facts


def all_teams(base: str, token: str) -> list[dict]:
    result, offset = [], 0
    while True:
        page = api(base, f"/api/admin/teams?offset={offset}&limit=500", token=token)
        result.extend(page["items"])
        offset += len(page["items"])
        if offset >= page["total"] or not page["items"]:
            return result


def provision(base: str, round_code: str, count: int) -> list[TeamCredential]:
    token, facts = admin_session(base)
    if facts.get("login_scope") != round_code:
        raise RuntimeError(
            f"Server-owned login scope is {facts.get('login_scope') or 'unavailable'}, not {round_code}. "
            "Prepare the round lifecycle in the admin UI; the launcher will not override it."
        )
    prefix = ask("Simulator team prefix", f"SIM-{round_code}").upper()
    if not prefix.startswith("SIM-"):
        raise RuntimeError("Simulator prefixes must start with SIM-.")
    shared = getpass.getpass("Simulator team password (8+ characters; hidden): ")
    if len(shared) < 8:
        raise RuntimeError("Simulator password must be at least 8 characters.")
    existing = {row["code"]: row for row in all_teams(base, token)}
    credentials = []
    for number in range(1, count + 1):
        code = f"{prefix}-{number:03d}"
        username = code.lower()
        row = existing.get(code)
        if row is None:
            row = api(base, "/api/admin/teams", method="POST", token=token, body={
                "code": code, "name": f"Simulator {round_code} {number:03d}",
                "username": username, "password": shared,
            })
            print(f"Created {code} (credentials hidden).")
        else:
            if row["username"] != username:
                raise RuntimeError(f"Existing {code} has a different username; it was not changed.")
            if not row["enabled"]:
                raise RuntimeError(f"Existing {code} is disabled; enable it before launching.")
            detail = api(base, f"/api/admin/teams/{row['id']}", token=token)
            if not detail["has_password"]:
                raise RuntimeError(f"Existing {code} is not activated; activate it before launching.")
            if round_code == "R1A" and facts["rounds"].get("R1A", {}).get("started_at") \
                    and "R1A" not in detail["enrolled_rounds"]:
                raise RuntimeError(
                    f"Existing {code} is not in the R1A start-time enrollment snapshot. "
                    "R1A simulator teams must exist and be enabled BEFORE round start."
                )
            print(f"Reusing {code}; its password was not changed.")
        credentials.append(TeamCredential(username, shared, code))
    return credentials


def manual_credentials(count: int) -> list[TeamCredential]:
    credentials = []
    for number in range(1, count + 1):
        username = ask(f"Team {number} username")
        password = getpass.getpass(f"Team {number} password (hidden): ")
        credentials.append(TeamCredential(username, password, f"TEAM-{number:03d}"))
    return credentials


def badge_script(label: str) -> str:
    safe = json.dumps(label)
    return f"""(() => {{
      const install = () => {{
        document.title = {safe} + ' · ' + document.title;
        if (document.getElementById('simulator-device-badge')) return;
        const badge = document.createElement('div'); badge.id = 'simulator-device-badge';
        badge.textContent = {safe};
        badge.style.cssText = 'position:fixed;z-index:2147483647;top:4px;right:4px;padding:6px 9px;background:#ffdf57;color:#111;font:700 12px system-ui;border:2px solid #111;border-radius:5px;pointer-events:none';
        document.documentElement.appendChild(badge);
      }};
      if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install);
      else install();
    }})()"""


def login_error_message(status: int, detail) -> str:
    if isinstance(detail, dict):
        detail = detail.get("message") or detail.get("code") or "Login failed"
    detail = str(detail or "Login failed")
    lowered = detail.lower()
    if status == 401:
        return "Invalid team credentials. If this team has not been activated, activate it through the supported player flow first."
    if status == 409 and "capacity" in lowered:
        return "This team's session capacity is full for the server-selected round. Log out an existing player or wait for its real lease to expire."
    if status == 409 and "login" in lowered:
        return "No Round 1 player login is currently available. Prepare the round lifecycle in the admin UI."
    return f"Player login failed ({status}): {detail}"


def save_login_diagnostic(page, label: str, reason: str) -> str:
    """Save an actionable screenshot and metadata without credential values."""
    for name in ("password", "activation"):
        fields = page.locator(f'input[name="{name}"]')
        for index in range(fields.count()):
            try:
                fields.nth(index).fill("")
            except Exception:
                pass
    root = Path(".preview/launcher-diagnostics")
    root.mkdir(parents=True, exist_ok=True)
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "-", label).strip("-")[:80] or "player"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    screenshot = root / f"{stamp}-{safe_label}.png"
    page.screenshot(path=str(screenshot), full_page=False)
    headings = page.get_by_role("heading").all_inner_texts()[:5]
    alerts = page.get_by_role("alert").all_inner_texts()[:3]
    submit = page.get_by_role("button", name="Enter the arena", exact=True)
    metadata = {
        "reason": reason,
        "url": page.url,
        "title": page.title(),
        "headings": headings,
        "alerts": alerts,
        "login_button_count": submit.count(),
        "login_button_visible": submit.is_visible() if submit.count() == 1 else False,
        "login_button_enabled": submit.is_enabled() if submit.count() == 1 else False,
        "screenshot": str(screenshot),
    }
    diagnostic = screenshot.with_suffix(".json")
    diagnostic.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return str(diagnostic)


def login_player(page, player_url: str, team: TeamCredential, expected_round: str) -> dict:
    """Log in through the rendered frontend and confirm its authenticated state."""
    page.goto(player_url, wait_until="domcontentloaded")
    login_heading = page.get_by_role("heading", name="Your team. Your next move.", exact=True)
    try:
        login_heading.wait_for(state="visible", timeout=15000)
    except Exception:
        if page.get_by_role("heading", name="Activate your team.", exact=True).count():
            raise RuntimeError("The activation screen is open. Switch back to sign in, or complete supported activation before launching.")
        raise RuntimeError("The player login screen did not become ready.")

    username = page.get_by_label("Team username", exact=True)
    password = page.get_by_label("Password", exact=True)
    submit = page.get_by_role("button", name="Enter the arena", exact=True)
    username.wait_for(state="visible")
    password.wait_for(state="visible")
    submit.wait_for(state="visible")
    if not submit.is_enabled():
        raise RuntimeError("The player login button is disabled before submission.")
    username.fill(team.username)
    password.fill(team.password)

    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.split("?", 1)[0].endswith("/api/auth/team/login"),
        timeout=15000,
    ) as response_info:
        submit.click()
    response = response_info.value
    payload = response.json()
    if not response.ok:
        alert = page.get_by_role("alert")
        try:
            alert.wait_for(state="visible", timeout=3000)
        except Exception:
            pass
        raise RuntimeError(login_error_message(response.status, payload.get("detail")))

    page.wait_for_function("() => !!localStorage.getItem('r1token')", timeout=12000)
    page.locator(".game-shell").wait_for(state="visible", timeout=15000)
    identity = page.evaluate("""async () => {
      const token=localStorage.getItem('r1token');
      const response=await fetch('/api/auth/team/me',{headers:{Authorization:'Bearer '+token}});
      return {ok:response.ok, body:await response.json(), has_token:!!token};
    }""")
    if not identity["ok"] or not identity["has_token"]:
        raise RuntimeError("Login returned success, but the authenticated player session could not be confirmed.")
    actual = identity["body"]
    if actual["round_code"] != expected_round:
        page.evaluate("""async () => {
          const token=localStorage.getItem('r1token');
          await fetch('/api/auth/team/logout',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:'{}'});
          localStorage.removeItem('r1token');
        }""")
        raise RuntimeError(
            f"Server-owned login scope is {actual['round_code']}, not {expected_round}. "
            "The mismatched session was logged out; prepare the selected round in the admin UI."
        )
    return actual


def launch(base: str, round_code: str, credentials: list[TeamCredential], sessions: int) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("Playwright is not installed. Run: .venv/bin/pip install -r requirements-dev.txt")
    player_url = urljoin(base, "/player/")
    contexts = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        try:
            for team in credentials:
                # Both rounds intentionally use one browser context per team.
                for _ in range(1):
                    label = f"{team.label} · {round_code} · shared team device"
                    context = browser.new_context(
                        viewport={"width": 390, "height": 844},
                        screen={"width": 390, "height": 844},
                        is_mobile=True, has_touch=True, device_scale_factor=1,
                    )
                    context.add_init_script(badge_script(label))
                    page = context.new_page()
                    try:
                        actual = login_player(page, player_url, team, round_code)
                        actual_label = f"{team.label} · {round_code} · TEAM DEVICE"
                        page.evaluate("label => { document.title=label; document.getElementById('simulator-device-badge').textContent=label; }", actual_label)
                        print(f"Opened {actual_label} at 390x844 with touch emulation.")
                        contexts.append((context, actual["session_id"]))
                    except Exception as exc:
                        try:
                            diagnostic = save_login_diagnostic(page, label, str(exc))
                        except Exception as diagnostic_error:
                            diagnostic = f"diagnostic capture failed: {diagnostic_error}"
                        context.close()
                        raise RuntimeError(f"{label}: {exc} Sanitized diagnostic: {diagnostic}") from None
            session_ids = [session_id for _, session_id in contexts]
            if len(session_ids) != len(set(session_ids)):
                raise RuntimeError("Storage isolation check failed: two contexts expose the same backend session identity.")
            print(f"Storage isolation check passed for {len(contexts)} independent contexts.")
            print("Windows remain open for manual READY, submit, skip, refresh, logout, and leaderboard checks.")
            input("Press Enter to close every simulator browser and exit... ")
        finally:
            for context, _ in contexts:
                try:
                    context.close()
                except Exception:
                    pass
            browser.close()


def main() -> int:
    print("Round 1 headed multi-player launcher (manual functional testing, not stress testing)")
    base = ask("Application URL", "http://127.0.0.1:8000")
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("Application URL must be an http(s) origin.")
    round_code = ask("Round (R1A or R1B)", "R1A").upper()
    if round_code not in {"R1A", "R1B"}:
        raise RuntimeError("Round must be R1A or R1B.")
    count = ask_int("Number of test teams", 2, 1, 100)
    sessions = 1
    print(f"{round_code} uses exactly one session per team. R1B players pass the same device between turns.")
    if round_code == "R1A":
        print("Teams must exist and be enabled BEFORE round start because enrollment is snapshotted.")
    print(f"This will open {count * sessions} visible mobile-sized windows; start small and increase based on laptop RAM/CPU.")
    if yes_no("Create/reuse SIM-* teams through development admin APIs?"):
        credentials = provision(base, round_code, count)
    else:
        credentials = manual_credentials(count)
    launch(base, round_code, credentials, sessions)
    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nLauncher stopped; simulator browsers are being cleaned up.")
        raise SystemExit(130)
    except RuntimeError as exc:
        print(f"Launcher error: {exc}", file=sys.stderr)
        raise SystemExit(1)
