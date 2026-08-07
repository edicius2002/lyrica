# -*- coding: utf-8 -*-
"""Probe: score the overlay's interpolation against the page's own clock.

`probe_browser_session.py` showed that Chrome states its position once and never
restates it, so there is no second reading to compare a prediction against. The
only ground truth left is the media element itself.

Collect samples in the page's console first:

    const v = document.querySelector('video');
    const samples = [];
    for (let i = 0; i < 12; i++) {
      samples.push({wall: Date.now(), pos: v.currentTime, paused: v.paused});
      await new Promise(r => setTimeout(r, 2500));
    }
    JSON.stringify({duration: v.duration, samples})

Then pass that JSON to this script. Each sample carries the wall-clock time it
was taken, so the prediction is evaluated at that exact instant and the latency
of moving the data between the browser and here cannot distort the result.

    python probe_browser_drift.py samples.json [app_substring]
"""
import asyncio
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from winsdk.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as SessionManager,
)


async def anchor_for(app_substring: str) -> dict | None:
    """The reported position and the moment it was reported."""
    mgr = await SessionManager.request_async()
    for s in mgr.get_sessions():
        app = s.source_app_user_model_id or ""
        if app_substring.lower() not in app.lower():
            continue
        media = await s.try_get_media_properties_async()
        tl = s.get_timeline_properties()
        pb = s.get_playback_info()
        updated = tl.last_updated_time
        if updated is not None and updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        return {
            "app": app,
            "title": media.title or "",
            "position": tl.position.total_seconds(),
            "end": tl.end_time.total_seconds(),
            "updated_at": updated,
            "status": pb.playback_status.name if pb and pb.playback_status else "",
        }
    return None


def predict(anchor: dict, at: datetime) -> float:
    """The overlay's interpolation, evaluated at an arbitrary instant."""
    pos = anchor["position"]
    if anchor["status"] == "PLAYING" and anchor["updated_at"] is not None:
        pos += (at - anchor["updated_at"]).total_seconds()
    if anchor["end"] > 0:
        pos = min(pos, anchor["end"])
    return max(pos, 0.0)


async def main(path: str, app_substring: str):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    samples = data["samples"]

    anchor = await anchor_for(app_substring)
    if anchor is None:
        print(f"No media session matching {app_substring!r}")
        return

    print(f"Session : {anchor['app']} — {anchor['title']!r}")
    print(f"Anchor  : position={anchor['position']:.3f}s reported at {anchor['updated_at']}")
    print(f"Status  : {anchor['status']}, duration {anchor['end']:.1f}s")
    print(f"Page    : duration {data.get('duration')}s, {len(samples)} samples\n")

    errors = []
    print(f"{'page pos':>10} {'predicted':>10} {'error':>9}")
    for s in samples:
        at = datetime.fromtimestamp(s["wall"] / 1000, tz=timezone.utc)
        p = predict(anchor, at)
        err = p - s["pos"]
        errors.append(err)
        print(f"{s['pos']:10.3f} {p:10.3f} {err:+9.3f}s")

    print(f"\nError: median {statistics.median(errors):+.3f}s, "
          f"max |{max(abs(e) for e in errors):.3f}|s, "
          f"spread {max(errors) - min(errors):.3f}s")
    print("The spread is what matters: a constant offset can be nudged away, "
          "a growing one cannot.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "chrome"))
