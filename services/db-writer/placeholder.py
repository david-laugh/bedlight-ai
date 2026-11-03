"""BedLight AI placeholder process.

This process intentionally does NOTHING except stay alive so that the
container lifecycle (build / start / stop) can be verified.
It does not connect to MQTT or Supabase and never reads SUPABASE_* secrets.
A running container MUST NOT be interpreted as a working service.
"""

import os
import signal
import time

SERVICE = os.getenv("SERVICE_NAME", "unknown")
_running = True


def _stop(signum, _frame):
    global _running
    print(f"[{SERVICE}] received signal {signum}, exiting placeholder", flush=True)
    _running = False


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)

print(
    f"[{SERVICE}] PLACEHOLDER ONLY - no functionality implemented "
    "(no MQTT subscription, no schema validation, no DB insert). Idling.",
    flush=True,
)

while _running:
    time.sleep(1)
