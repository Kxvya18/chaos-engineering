"""Repository consistency checks run by CI (and locally: python3 scripts/check_repo.py)."""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
REQUIRED_DOCS = ["CLAUDE.md", "docs/REQUIREMENTS.md", "docs/MVP.md", "docs/PLAN.md",
                 "docs/SAFETY.md", "docs/STATE.md", "docs/GITHUB.md"]
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def check_docs(errors):
    for rel in REQUIRED_DOCS:
        if not (ROOT / rel).is_file():
            errors.append(f"missing required file: {rel}")


def check_skills(errors):
    for skill in sorted((ROOT / ".claude/skills").glob("*/SKILL.md")):
        m = FRONTMATTER.match(skill.read_text())
        if not m:
            errors.append(f"{skill.relative_to(ROOT)}: no YAML frontmatter")
            continue
        fields = dict(line.split(": ", 1) for line in m.group(1).splitlines() if ": " in line)
        if fields.get("name") != skill.parent.name:
            errors.append(f"{skill.relative_to(ROOT)}: name must equal directory name")
        if len(fields.get("description", "")) < 20:
            errors.append(f"{skill.relative_to(ROOT)}: description missing or too short")
        if fields.get("disable-model-invocation") != "true":
            errors.append(f"{skill.relative_to(ROOT)}: disable-model-invocation must be true")


def check_json(errors):
    for path in sorted(ROOT.glob("artifacts/**/*.json")) + [ROOT / ".claude/settings.json"]:
        if not path.is_file():
            continue
        try:
            json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            errors.append(f"{path.relative_to(ROOT)}: invalid JSON ({exc})")


def main():
    errors = []
    check_docs(errors)
    check_skills(errors)
    check_json(errors)
    for e in errors:
        print(f"FAIL {e}")
    print("repo checks:", "FAILED" if errors else "OK")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
