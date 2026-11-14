"""Direct pinned-HTTPS charger control, using the existing private hub credential."""

import argparse
import json
import sys

from app.clients.stand import StandTransport
from app.repositories.stands import StandRepository
from app.services.stands import StandService


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "on", "off", "auto"])
    parser.add_argument("--stand", help="Stand ID; select exactly one enrolled stand")
    parser.add_argument("--duration-ms", type=int, default=60000)
    args = parser.parse_args()
    try:
        rows = [r for r in StandRepository().rows() if args.stand is None or r["id"] == args.stand]
        if len(rows) != 1:
            parser.error("select exactly one enrolled stand using --stand")
        stand = StandService.enrollment(rows[0])
        client = StandTransport()
        if args.action == "status":
            data = client.request(stand)
        else:
            enabled = None if args.action == "auto" else args.action == "on"
            data = client.charger(stand, enabled, args.duration_ms)
        print(
            json.dumps(
                {
                    "id": data["id"],
                    "firmware": data["firmware"],
                    "uptimeS": data["uptimeS"],
                    "battery": data["battery"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(f"Stand command failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
