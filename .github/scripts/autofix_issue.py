import os
import re
import subprocess
import sys


def _set_output(key: str, value: str) -> None:
    output_path = os.getenv("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as fh:
        fh.write(f"{key}={value}\n")


def _run(command: str) -> int:
    print(f"Running: {command}")
    completed = subprocess.run(command, shell=True)
    return completed.returncode


def _safe_text(value: str) -> str:
    return (value or "").strip().lower()


def main() -> int:
    issue_title = _safe_text(os.getenv("ISSUE_TITLE", ""))
    issue_body = _safe_text(os.getenv("ISSUE_BODY", ""))
    extra_command = os.getenv("AUTOFIX_EXTRA_COMMAND", "").strip()
    validate_command = os.getenv("AUTOFIX_VALIDATE_COMMAND", "").strip()

    issue_text = f"{issue_title}\n{issue_body}"
    commands = []
    notes = []

    if re.search(r"unused import|\bruff\b|\blint\b", issue_text):
        commands.append("ruff check . --fix")
        notes.append("Applied ruff auto-fixes for lint/import issues")

    if extra_command:
        # Repository-level override for targeted, explicit fix routines.
        commands.append(extra_command)
        notes.append(f"Ran AUTOFIX_EXTRA_COMMAND: {extra_command}")

    if not commands:
        _set_output("changes_detected", "false")
        _set_output("validation_passed", "false")
        _set_output("notes", "No applicable low-risk autofix heuristic matched")
        return 0

    for command in commands:
        rc = _run(command)
        if rc != 0:
            _set_output("changes_detected", "false")
            _set_output("validation_passed", "false")
            _set_output("notes", f"Autofix command failed: {command}")
            return 0

    status = subprocess.run(
        "git status --porcelain",
        shell=True,
        check=False,
        capture_output=True,
        text=True,
    )
    changed = bool(status.stdout.strip())
    _set_output("changes_detected", "true" if changed else "false")

    if not changed:
        _set_output("validation_passed", "false")
        _set_output("notes", "Autofix commands ran but produced no file changes")
        return 0

    if not validate_command:
        validate_command = "ruff check . && python -m compileall -q ."

    validate_rc = _run(validate_command)
    validation_passed = validate_rc == 0
    _set_output("validation_passed", "true" if validation_passed else "false")
    _set_output("notes", "; ".join(notes) if notes else "Autofix applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
