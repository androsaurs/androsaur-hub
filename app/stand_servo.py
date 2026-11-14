"""Direct pinned-HTTPS servo control using the hub's existing delegated credential.

Run from the hub directory: python -m app.stand_servo status|center|stop|move.
No running phone, active hub web server or exported credential is needed.
"""

import argparse
import json
import sys

from app.clients.stand import StandTransport
from app.repositories.stands import StandRepository
from app.services.stands import StandService


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "center", "stop", "move"])
    parser.add_argument("--stand", help="Stand ID; required if more than one stand is enrolled")
    parser.add_argument("--pan-us", type=int)
    parser.add_argument("--tilt-us", type=int)
    parser.add_argument("--duration-ms", type=int)
    args = parser.parse_args()
    if args.action == "move" and (args.pan_us is None or args.tilt_us is None):
        parser.error("move requires --pan-us and --tilt-us")
    if args.action != "move" and any(
        v is not None for v in (args.pan_us, args.tilt_us, args.duration_ms)
    ):
        parser.error("pulse and duration options apply only to move")
    try:
        rows = [r for r in StandRepository().rows() if args.stand is None or r["id"] == args.stand]
        if len(rows) != 1:
            parser.error("select exactly one enrolled stand using --stand")
        stand = StandService.enrollment(rows[0])
        transport = StandTransport()
        if args.action == "status":
            data = transport.request(stand)
        else:
            data = transport.servos(
                stand,
                args.action,
                pan_us=1500 if args.pan_us is None else args.pan_us,
                tilt_us=1500 if args.tilt_us is None else args.tilt_us,
                duration_ms=1000 if args.duration_ms is None else args.duration_ms,
            )
        print(
            json.dumps(
                {
                    "id": data["id"],
                    "firmware": data["firmware"],
                    "uptimeS": data["uptimeS"],
                    "tracking": data["tracking"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (OSError, ValueError, KeyError) as exc:
        # Avoid logging arbitrary connection URLs, response bodies or credentials.
        print(f"Stand command failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
