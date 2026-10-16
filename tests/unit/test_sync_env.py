import importlib.util
import sys
from pathlib import Path

from dotenv import dotenv_values

from app.config import ROOT_DIR

_spec = importlib.util.spec_from_file_location("sync_env", ROOT_DIR / "scripts" / "sync_env.py")
assert _spec and _spec.loader
sync_env = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync_env)

TEMPLATE = """# Core
# What the env is.
APP_ENV=development
SECRET_KEY=
# New since you copied the template.
NEW_SETTING=default-value
"""


def run(tmp_path: Path, env_text: str, *flags: str) -> tuple[str, dict[str, str | None]]:
    env, template = tmp_path / ".env", tmp_path / ".env.example"
    env.write_text(env_text, encoding="utf-8")
    template.write_text(TEMPLATE, encoding="utf-8")
    argv = sys.argv
    sys.argv = ["sync_env.py", "--env", str(env), "--template", str(template), *flags]
    try:
        sync_env.main()
    finally:
        sys.argv = argv
    return env.read_text(encoding="utf-8"), dotenv_values(env)


def test_moves_inline_comments_off_values_and_keeps_every_value(tmp_path: Path) -> None:
    text, values = run(
        tmp_path,
        "APP_ENV=production          # was an inline comment\n"
        'SECRET_KEY="s3cr3t # with hash"\n'
        "EXTRA_KEY=keep-me   # mine\n"
        "EMPTY_WITH_NOTE=                # optional, explains the empty value\n",
    )
    assert values == {
        "APP_ENV": "production",
        "SECRET_KEY": "s3cr3t # with hash",
        "NEW_SETTING": "default-value",
        "EXTRA_KEY": "keep-me",
        "EMPTY_WITH_NOTE": "",  # the note is not the value
    }
    assert "APP_ENV=production\n" in text  # nothing after the value any more
    assert (tmp_path / ".env.bak").exists()


def test_demo_account_is_generated_only_when_missing(tmp_path: Path) -> None:
    _, values = run(tmp_path, "APP_ENV=development\n", "--demo-account")
    assert values["DEMO_USER_EMAIL"] and len(values["DEMO_USER_PASSWORD"] or "") >= 12
    _, again = run(
        tmp_path,
        "DEMO_USER_EMAIL=me@example.com\nDEMO_USER_PASSWORD=already-set\n",
        "--demo-account",
    )
    assert again["DEMO_USER_PASSWORD"] == "already-set"
