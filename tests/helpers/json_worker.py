"""Test-only JSON worker used to exercise process supervision."""

import json
from pathlib import Path
import sys
import time


def main() -> int:
    request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    mode = request.get("mode", "echo")
    if mode == "hang":
        Path(request["started_path"]).write_text("started", encoding="utf-8")
        time.sleep(float(request.get("delay", 60)))
        Path(request["completed_path"]).write_text("completed", encoding="utf-8")
        response = {"status": "completed"}
    elif mode == "fail":
        sys.stderr.write(str(request.get("diagnostic", "worker failed")))
        return int(request.get("exit_code", 7))
    elif mode == "array":
        response = ["not", "an", "object"]
    else:
        response = {"echo": request.get("value")}

    sys.stdout.buffer.write(
        json.dumps(response, ensure_ascii=False).encode("utf-8")
    )
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
