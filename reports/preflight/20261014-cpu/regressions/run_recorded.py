#!/usr/bin/env python3
"""Keep separate stdout/stderr and the exit code for a port-shim invocation."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

OUTPUT = Path(__file__).resolve().parent
ROOT = OUTPUT.parents[1]
name = sys.argv[1]
command = [str(ROOT / ".venv/bin/python"), str(OUTPUT / "port_shim.py"), name]
record = {"started_utc": datetime.now(timezone.utc).isoformat(), "command": command,
          "cwd": str(ROOT), "stdout": str(OUTPUT / (name + ".stdout.log")),
          "stderr": str(OUTPUT / (name + ".stderr.log"))}
print(json.dumps({"started": name, "command": command}), flush=True)
t0 = time.monotonic()
with (OUTPUT / (name + ".stdout.log")).open("w") as stdout, (OUTPUT / (name + ".stderr.log")).open("w") as stderr:
    process = subprocess.run(command, cwd=ROOT, stdout=stdout, stderr=stderr)
record.update(returncode=process.returncode, elapsed_seconds=time.monotonic() - t0)
(OUTPUT / (name + "-command.json")).write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps(record, indent=2), flush=True)
raise SystemExit(process.returncode)
