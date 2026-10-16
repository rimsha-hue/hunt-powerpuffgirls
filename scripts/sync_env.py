"""Create or update .env from the settings declared in app/config.py, without losing any values.

app/config.py is the one place settings are declared and documented; there is no template file.

- Every value already in .env is kept, and settings the app has gained are added at the end with
  their defaults. A new SECRET_KEY is generated when missing; other secrets are left empty.
- Inline comments are moved onto their own line (Docker, CI and hosting dashboards read a trailing
  "# comment" as part of the value).
- The original is backed up to .env.bak first. No values are ever printed.

Usage: python scripts/sync_env.py [--env .env] [--demo-account]
  --demo-account  fill DEMO_USER_EMAIL and DEMO_USER_PASSWORD if they are empty, and print them.
"""

import argparse
import re
import secrets
import shutil
import sys
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402 (needs the repo root on the path)

LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
# Paths inside the image, not deployment settings.
INTERNAL_FIELDS = frozenset({"static_dir"})
SECRET_KEY_BYTES = 36


def parse_value(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("#"):
        return ""  # "KEY=   # note" is an empty value followed by a comment
    if raw[:1] in {'"', "'"}:
        quote = raw[0]
        end = raw.find(quote, 1)
        return raw[1:end] if end != -1 else raw[1:]
    return re.split(r"\s+#", raw, maxsplit=1)[0].strip()


def inline_comment(raw: str) -> str | None:
    """The trailing "# note" of an unquoted value, if any."""
    raw = raw.strip()
    if raw[:1] in {'"', "'"}:
        return None
    parts = re.split(r"\s+#", raw, maxsplit=1) if not raw.startswith("#") else ["", raw[1:]]
    return parts[1].strip() if len(parts) == 2 and parts[1].strip() else None


def render(value: str) -> str:
    # Quote only when needed, so values with spaces or "#" survive every parser.
    if value and (re.search(r"\s|#|['\"]", value)):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def default_text(value: Any) -> str:
    if value is None or isinstance(value, SecretStr):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def declared_settings() -> dict[str, str]:
    """ENV_NAME -> default, in the order app/config.py declares them."""
    return {
        name.upper(): default_text(field.default)
        for name, field in Settings.model_fields.items()
        if name not in INTERNAL_FIELDS
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--env", default=".env")
    parser.add_argument("--demo-account", action="store_true")
    args = parser.parse_args()

    env_path = Path(args.env)
    lines = env_path.read_text(encoding="utf-8-sig").splitlines() if env_path.exists() else []
    if env_path.exists():
        shutil.copy2(env_path, env_path.with_name(env_path.name + ".bak"))

    out: list[str] = []
    present: dict[str, str] = {}
    for line in lines:
        match = LINE.match(line)
        if not match or line.lstrip().startswith("#"):
            out.append(line)
            continue
        key, raw = match.group(1), match.group(2)
        value = parse_value(raw)
        present[key] = value  # a later duplicate wins, as dotenv and Docker read it
        note = inline_comment(raw)
        if note:
            out.append(f"# {note}")
        out.append(f"{key}={render(value)}")

    def set_value(key: str, value: str) -> None:
        """Every definition of the key gets the value (it was added above if missing)."""
        present[key] = value
        for i, line in enumerate(out):
            match = LINE.match(line)
            if match and match.group(1) == key and not line.lstrip().startswith("#"):
                out[i] = f"{key}={render(value)}"

    created_demo = None
    if args.demo_account and not (
        present.get("DEMO_USER_EMAIL") and present.get("DEMO_USER_PASSWORD")
    ):
        created_demo = (
            present.get("DEMO_USER_EMAIL") or "judge@powerhunt.demo",
            secrets.token_urlsafe(12),
        )

    declared = declared_settings()
    added = [key for key in declared if key not in present]
    if added:
        out += ["", "# ----- Added by scripts/sync_env.py (documented in app/config.py) -----"]
        for key in added:
            value = (
                secrets.token_urlsafe(SECRET_KEY_BYTES) if key == "SECRET_KEY" else declared[key]
            )
            out.append(f"{key}={render(value)}")
            present[key] = value
    if created_demo:
        set_value("DEMO_USER_EMAIL", created_demo[0])
        set_value("DEMO_USER_PASSWORD", created_demo[1])

    unknown = [key for key in present if key not in declared]
    env_path.write_text("\n".join(out).strip("\n") + "\n", encoding="utf-8")
    print(f"Wrote {env_path} ({len(declared)} settings; backup at {env_path.name}.bak)")
    print(f"Added with defaults: {', '.join(added) or 'none'}")
    print(f"Not used by the app (kept): {', '.join(unknown) or 'none'}")
    if created_demo:
        print(f"Demo account: {created_demo[0]} / {created_demo[1]}")


if __name__ == "__main__":
    main()
