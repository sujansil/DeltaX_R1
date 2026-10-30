import importlib.util
from pathlib import Path
import sys


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "player_launcher.py"
SPEC = importlib.util.spec_from_file_location("player_launcher", MODULE_PATH)
launcher = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = launcher
SPEC.loader.exec_module(launcher)


def test_badge_script_escapes_untrusted_label():
    script = launcher.badge_script("SIM-' </script> SLOT")
    assert json_string("SIM-' </script> SLOT") in script
    assert "simulator-device-badge" in script


def json_string(value):
    import json
    return json.dumps(value)


def test_team_credential_does_not_expose_password_in_repr():
    credential = launcher.TeamCredential("sim-r1a-001", "hidden-value", "SIM-R1A-001")
    assert "hidden-value" not in repr(credential)


def test_login_errors_explain_activation_capacity_and_lifecycle():
    invalid = launcher.login_error_message(401, "Invalid team credentials")
    capacity = launcher.login_error_message(409, "R1B session capacity is full")
    lifecycle = launcher.login_error_message(409, "No Round 1 player login is currently available")
    assert "activated" in invalid
    assert "capacity is full" in capacity
    assert "round lifecycle" in lifecycle
