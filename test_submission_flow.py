"""End-to-end flow checks for the in-process submission path.

These tests drive garminbot._run_garmin_script through the exit-code branches
with a fake Garmin client: token login, missing credentials, MFA resume,
submission failure, and optional LLM feedback append.

Run directly:  python test_submission_flow.py
Or pytest:     pytest test_submission_flow.py
"""
import os
import tempfile
import types

# garminbot validates these at import time; set them before importing it.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("ALLOWED_TELEGRAM_ID", "1")
os.environ["GARMINTOKENS_BASE"] = tempfile.mkdtemp(prefix="garmin_test_tokens_")
os.environ.pop("GOOGLE_API_KEY", None)

import garminbot
import garminconnectapi as gapi


DATA = {
    "weight": 80.0,
    "muscle_mass": 34.0,
    "bmi": 24.0,
    "percent_fat": 18.0,
    "visceral_fat_rating": 7,
    "percent_hydration": None,
    "bone_mass": None,
}


def make_fake_garmin(scenario: dict):
    """Return a Garmin stand-in whose behavior is driven by scenario."""

    class FakeGarmin:
        def __init__(self, email=None, password=None, is_cn=False, return_on_mfa=False):
            self.cred_mode = email is not None
            self.client = types.SimpleNamespace(dump=lambda path: None)

        def login(self, tokenstore=None):
            if not self.cred_mode:
                if scenario.get("token_ok"):
                    return True
                raise FileNotFoundError("no saved token")
            return scenario.get("cred_login_result")

        def resume_login(self, state, code):
            if scenario.get("mfa_raises"):
                raise scenario["mfa_raises"]
            return True

        def add_body_composition(self, timestamp, **kwargs):
            if scenario.get("submit_raises"):
                raise RuntimeError("garmin rejected submission")
            scenario.setdefault("submitted", []).append(kwargs)
            return True

    return FakeGarmin


def run(scenario, **kwargs):
    gapi.Garmin = make_fake_garmin(scenario)
    return garminbot._run_garmin_script(user_id=1, data=DATA, **kwargs)


def test_token_login_success_submits():
    scenario = {"token_ok": True}
    code, stdout, _ = run(scenario)
    assert code == gapi.EXIT_SUCCESS
    assert "Success: Data submitted." in stdout
    assert scenario["submitted"]


def test_no_token_no_creds_asks_for_credentials():
    code, _, _ = run({"token_ok": False})
    assert code == gapi.EXIT_TOKEN_FAILURE


def test_creds_need_mfa_without_code():
    code, _, _ = run(
        {"token_ok": False, "cred_login_result": ("needs_mfa", "STATE")},
        email="a@b.c",
        password="pw",
    )
    assert code == gapi.EXIT_MFA_REQUIRED


def test_creds_with_mfa_code_resumes_and_submits():
    scenario = {"token_ok": False, "cred_login_result": ("needs_mfa", "STATE")}
    code, stdout, _ = run(scenario, email="a@b.c", password="pw", mfa_code="123456")
    assert code == gapi.EXIT_SUCCESS
    assert "Success" in stdout
    assert scenario["submitted"]


def test_submission_failure_returns_error():
    code, _, _ = run({"token_ok": True, "submit_raises": True})
    assert code == gapi.EXIT_SUBMISSION_ERROR


def test_llm_feedback_appended_on_success():
    import llmfeedback

    saved = llmfeedback.get_feedback
    llmfeedback.get_feedback = lambda api: "Great progress!"
    try:
        code, stdout, _ = run({"token_ok": True})
    finally:
        llmfeedback.get_feedback = saved
    assert code == gapi.EXIT_SUCCESS
    assert "LLM: Great progress!" in stdout


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"all {len(tests)} passed")


if __name__ == "__main__":
    _run()
