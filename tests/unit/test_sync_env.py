import importlib.util
import sys
from pathlib import Path

from dotenv import dotenv_values

from app.config import ROOT_DIR, Settings

_spec = importlib.util.spec_from_file_location("sync_env", ROOT_DIR / "scripts" / "sync_env.py")
assert _spec and _spec.loader
sync_env = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync_env)


def run(tmp_path: Path, env_text: str | None, *flags: str) -> tuple[str, dict[str, str | None]]:
    env = tmp_path / ".env"
    if env_text is not None:
        env.write_text(env_text, encoding="utf-8")
    argv = sys.argv
    sys.argv = ["sync_env.py", "--env", str(env), *flags]
    try:
        sync_env.main()
    finally:
        sys.argv = argv
    return env.read_text(encoding="utf-8"), dotenv_values(env)


def test_adds_every_declared_setting_with_its_default(tmp_path: Path) -> None:
    _, values = run(tmp_path, None)
    expected = {name.upper() for name in Settings.model_fields} - {"STATIC_DIR"}
    assert set(values) == expected
    assert values["SCOUT_INTERVAL_HOURS"] == "6"
    assert values["SCHEDULER_ENABLED"] == "true"
    assert values["GROQ_API_KEY"] == ""  # secrets stay empty
    assert len(values["SECRET_KEY"] or "") >= 40  # except a fresh signing key
    Settings(_env_file=tmp_path / ".env")  # type: ignore[call-arg]  # the result loads


def test_moves_inline_comments_off_values_and_keeps_every_value(tmp_path: Path) -> None:
    text, values = run(
        tmp_path,
        "APP_ENV=production          # was an inline comment\n"
        'SECRET_KEY="s3cr3t # with hash"\n'
        "EXTRA_KEY=keep-me   # mine\n"
        "GROQ_API_KEY=                # optional, explains the empty value\n",
    )
    assert values["APP_ENV"] == "production"
    assert values["SECRET_KEY"] == "s3cr3t # with hash"
    assert values["EXTRA_KEY"] == "keep-me"
    assert values["GROQ_API_KEY"] == ""  # the note is not the value
    assert "# was an inline comment\nAPP_ENV=production\n" in text
    assert (tmp_path / ".env.bak").exists()


def test_a_later_duplicate_wins_as_dotenv_reads_it(tmp_path: Path) -> None:
    _, values = run(tmp_path, "LOG_LEVEL=INFO\nLOG_LEVEL=DEBUG\n")
    assert values["LOG_LEVEL"] == "DEBUG"


def test_demo_account_is_generated_only_when_missing(tmp_path: Path) -> None:
    _, values = run(tmp_path, "APP_ENV=development\n", "--demo-account")
    assert values["DEMO_USER_EMAIL"] and len(values["DEMO_USER_PASSWORD"] or "") >= 12
    _, again = run(
        tmp_path,
        "DEMO_USER_EMAIL=me@example.com\nDEMO_USER_PASSWORD=already-set\n",
        "--demo-account",
    )
    assert again["DEMO_USER_PASSWORD"] == "already-set"
