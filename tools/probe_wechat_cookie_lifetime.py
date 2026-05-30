#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.config.settings import load_settings
from backend.app.integrations.enterprise_wechat import EnterpriseWechatClient
from backend.app.services.models import RoomSelection
from backend.app.shared.errors import AppError
from backend.app.shared.http import utc_now_iso
from tools.analyze_wechat_har import load_har
from tools.import_wechat_har import extract_import_data


@dataclass(frozen=True)
class ProbeConfig:
    interval_seconds: int
    max_checks: int
    keep_alive_only: bool


@dataclass(frozen=True)
class ProbeResult:
    checked_at: str
    ok: bool
    action: str
    message: str


def load_probe_source(har_file: Path, building: str | None = None, room: str | None = None) -> tuple[str, RoomSelection | None]:
    import_data = extract_import_data(load_har(har_file))
    if import_data.cookie_header is None:
        raise ValueError("No ASP.NET_SessionId cookie was found in the HAR.")
    if building and room:
        return import_data.cookie_header, RoomSelection(building=building, room=room)
    room_payload = import_data.room_selection_payload
    if room_payload is None:
        return import_data.cookie_header, None
    return import_data.cookie_header, RoomSelection(building=room_payload["building"], room=room_payload["room"])


def probe_once(client: EnterpriseWechatClient, selection: RoomSelection | None, *, keep_alive_only: bool = False) -> ProbeResult:
    checked_at = utc_now_iso()
    try:
        if selection is not None and not keep_alive_only:
            reading = client.fetch_reading(selection)
            return ProbeResult(
                checked_at=checked_at,
                ok=True,
                action="query",
                message=f"ok: {reading.building} {reading.room} = {reading.numeric_value:.2f} {reading.unit}".strip(),
            )
        client.keep_alive()
        return ProbeResult(checked_at=checked_at, ok=True, action="keep-alive", message="ok")
    except AppError as exc:
        return ProbeResult(checked_at=checked_at, ok=False, action="query" if selection and not keep_alive_only else "keep-alive", message=f"{exc.code}: {exc.message}")


def render_probe_header(selection: RoomSelection | None, config: ProbeConfig) -> str:
    target = f"{selection.building} {selection.room}" if selection else "keep-alive only"
    checks = "until failure/stop" if config.max_checks <= 0 else str(config.max_checks)
    return "\n".join(
        [
            "Enterprise WeChat cookie lifetime probe",
            "- Cookie: found `ASP.NET_SessionId` (value redacted)",
            f"- Target: {target}",
            f"- Interval seconds: {config.interval_seconds}",
            f"- Max checks: {checks}",
        ]
    )


def render_probe_result(result: ProbeResult) -> str:
    status = "OK" if result.ok else "FAIL"
    return f"[{result.checked_at}] {status} {result.action}: {result.message}"


def run_probe_loop(
    client: EnterpriseWechatClient,
    selection: RoomSelection | None,
    config: ProbeConfig,
    *,
    sleep_func: Callable[[float], None] = time.sleep,
    output: Callable[[str], None] = print,
) -> int:
    count = 0
    while config.max_checks <= 0 or count < config.max_checks:
        count += 1
        result = probe_once(client, selection, keep_alive_only=config.keep_alive_only)
        output(render_probe_result(result))
        if not result.ok:
            return 1
        if config.max_checks > 0 and count >= config.max_checks:
            break
        sleep_func(config.interval_seconds)
    return 0


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe how long an Enterprise WeChat ASP.NET_SessionId stays usable.")
    parser.add_argument("har_file", type=Path, help="HAR exported from the authorized Enterprise WeChat electricity flow.")
    parser.add_argument("--interval", type=positive_int, default=300, help="Seconds between probes. Default: 300.")
    parser.add_argument("--max-checks", type=int, default=0, help="Stop after N successful/failed checks. Default: 0 means run until failure or Ctrl+C.")
    parser.add_argument("--once", action="store_true", help="Run exactly one probe.")
    parser.add_argument("--keep-alive-only", action="store_true", help="Only verify the session page; do not query a room balance.")
    parser.add_argument("--building", help="Override building when the HAR does not contain room fields, e.g. C20.")
    parser.add_argument("--room", help="Override room when the HAR does not contain room fields, e.g. 2324.")
    args = parser.parse_args(argv)

    if bool(args.building) != bool(args.room):
        parser.error("--building and --room must be provided together.")

    try:
        cookie_header, selection = load_probe_source(args.har_file, building=args.building, room=args.room)
        config = ProbeConfig(
            interval_seconds=args.interval,
            max_checks=1 if args.once else args.max_checks,
            keep_alive_only=args.keep_alive_only,
        )
        client = EnterpriseWechatClient(load_settings())
        client.import_cookies(cookie_header)
    except (AppError, ValueError) as exc:
        print(f"Probe setup failed: {getattr(exc, 'message', str(exc))}", file=sys.stderr)
        return 1

    print(render_probe_header(selection, config))
    try:
        return run_probe_loop(client, selection, config)
    except KeyboardInterrupt:
        print("Probe stopped by user.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
