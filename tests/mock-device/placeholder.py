"""BedLight AI mock-device placeholder.

Mock event generation is NOT implemented. This script only prints a notice
and exits. It does not connect to MQTT and does not publish any event.
"""

import os

device_id = os.getenv("MOCK_DEVICE_ID", "mock-xiao-01")
print(
    f"[mock-device] PLACEHOLDER ONLY - event generation not implemented. "
    f"Nothing was published (device_id={device_id}, source=mock).",
    flush=True,
)
