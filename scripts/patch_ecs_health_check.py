"""Patch ECS task definition container health check (optional register)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HEALTH_CHECK = {
    "command": [
        "CMD-SHELL",
        (
            "python -c \"import urllib.request; "
            "urllib.request.urlopen('http://127.0.0.1:8000/api/health/', timeout=4)\" "
            "|| exit 1"
        ),
    ],
    "interval": 30,
    "timeout": 5,
    "retries": 3,
    "startPeriod": 60,
}

READ_ONLY_KEYS = (
    "taskDefinitionArn",
    "revision",
    "status",
    "requiresAttributes",
    "compatibilities",
    "registeredAt",
    "registeredBy",
)


def patch_task_definition(task_def: dict) -> dict:
    patched = dict(task_def)
    for key in READ_ONLY_KEYS:
        patched.pop(key, None)
    patched["containerDefinitions"] = list(patched["containerDefinitions"])
    container = dict(patched["containerDefinitions"][0])
    container["healthCheck"] = HEALTH_CHECK
    patched["containerDefinitions"][0] = container
    return patched


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-def-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--region", default="us-east-2")
    args = parser.parse_args()

    task_def = json.loads(args.task_def_json.read_text(encoding="utf-8-sig"))
    patched = patch_task_definition(task_def)

    out_path = args.output or args.task_def_json.with_name("task-def-register.json")
    out_path.write_text(json.dumps(patched, indent=2), encoding="utf-8")
    print(f"Wrote patched task definition to {out_path}")

    if not args.register:
        return 0

    result = subprocess.run(
        [
            "aws",
            "ecs",
            "register-task-definition",
            "--region",
            args.region,
            "--cli-input-json",
            f"file://{out_path.resolve()}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.stdout:
        print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
