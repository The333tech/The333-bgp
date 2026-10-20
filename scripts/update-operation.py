#!/usr/bin/env python3
"""CLI entry point for the same transaction used by the portal updater."""

import argparse
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.update_runner import prepare_runtime, recover_operation, run_command, runtime_identity
from app.update_failures import FAILURES, record_failure

project = Path(os.environ.get("THE333_PROJECT_DIR", "/opt/the333-bgp")).resolve()
if sys.argv[1:] == ["prepare"]:
    prepare_runtime(project, *runtime_identity(project))
elif sys.argv[1:2] == ["failure"]:
    parser = argparse.ArgumentParser()
    parser.add_argument("code", choices=FAILURES)
    parser.add_argument("free_bytes", type=int, nargs="?")
    parser.add_argument("required_bytes", type=int, nargs="?")
    arguments = parser.parse_args(sys.argv[2:])
    record_failure(project / "runtime" / "operations", arguments.code,
                   arguments.free_bytes, arguments.required_bytes)
elif sys.argv[1:] == ["recover", "--confirm"]:
    try:
        result = recover_operation(project, dict(os.environ))
        print(f"Verified runtime; update state is now {result['status']}.")
    except (OSError, RuntimeError) as exc:
        print(f"Recovery refused: {exc}", file=sys.stderr)
        raise SystemExit(1)
elif sys.argv[1:] and not sys.argv[1].startswith("--"):
    print("Usage: update-operation.py [update flags] | prepare | recover --confirm", file=sys.stderr)
    raise SystemExit(2)
else:
    result = run_command(project, ["update", *sys.argv[1:]], uuid.uuid4().hex, "", "",
                         dict(os.environ), int(os.environ.get("PRODUCT_UPDATE_TIMEOUT_SECONDS", "1800")),
                         capture=False)
    if not result.get("ok"):
        print("Обновление не завершено. Проверь состояние операции в портале и журнал updater.", file=sys.stderr)
    raise SystemExit(0 if result.get("ok") else 1)
