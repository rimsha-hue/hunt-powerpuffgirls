"""Bring a .env file in line with .env.example without losing any values.

- Layout and comments come from the template, with every comment on its own line (Docker,
  CI and hosting dashboards read a trailing "# comment" as part of the value).
- Every value already in .env is kept, and keys the template has gained are added with their
  defaults. Keys .env has that the template doesn't are kept at the end.
- The original is backed up to .env.bak first. No values are ever printed.

Usage: python scripts/sync_env.py [--env .env] [--template .env.example] [--demo-account]
  --demo-account  fill DEMO_USER_EMAIL and DEMO_USER_PASSWORD if they are empty, and print them.
"""

import argparse
import re
import secrets
import shutil
from pathlib import Path

LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def parse_value(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("#"):
        return ""  # "KEY=   # note" is an empty value followed by a comment
    if raw[:1] in {'"', "'"}:
        quote = raw[0]
        end = raw.find(quote, 1)
        return raw[1:end] if end != -1 else raw[1:]
    return re.split(r"\s+#", raw, maxsplit=1)[0].strip()


def read_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = LINE.match(line)
        if match and not line.lstrip().startswith("#"):
            values[match.group(1)] = parse_value(match.group(2))
    return values


def render(value: str) -> str:
    # Quote only when needed, so values with spaces or "#" survive every parser.
    if value and (re.search(r"\s|#|['\"]", value)):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--env", default=".env")
    parser.add_argument("--template", default=".env.example")
    parser.add_argument("--demo-account", action="store_true")
    args = parser.parse_args()

    env_path, template_path = Path(args.env), Path(args.template)
    current = read_values(env_path)
    if env_path.exists():
        shutil.copy2(env_path, env_path.with_name(env_path.name + ".bak"))

    created_demo = None
    if args.demo_account and not (
        current.get("DEMO_USER_EMAIL") and current.get("DEMO_USER_PASSWORD")
    ):
        created_demo = (
            current.get("DEMO_USER_EMAIL") or "judge@powerhunt.demo",
            secrets.token_urlsafe(12),
        )
        current["DEMO_USER_EMAIL"], current["DEMO_USER_PASSWORD"] = created_demo

    out: list[str] = []
    seen: set[str] = set()
    added: list[str] = []
    for line in template_path.read_text(encoding="utf-8").splitlines():
        match = LINE.match(line)
        if not match or line.lstrip().startswith("#"):
            out.append(line)
            continue
        key = match.group(1)
        seen.add(key)
        if key in current:
            out.append(f"{key}={render(current[key])}")
        else:
            out.append(f"{key}={render(parse_value(match.group(2)))}")
            added.append(key)
    extras = [k for k in current if k not in seen]
    if extras:
        out += ["", "# ----- Kept from your .env (not in the template) -----"]
        out += [f"{k}={render(current[k])}" for k in extras]

    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Wrote {env_path} ({len(seen)} template keys; backup at {env_path.name}.bak)")
    print(f"Added with defaults: {', '.join(added) or 'none'}")
    print(f"Kept extra keys: {', '.join(extras) or 'none'}")
    if created_demo:
        print(f"Demo account: {created_demo[0]} / {created_demo[1]}")


if __name__ == "__main__":
    main()
