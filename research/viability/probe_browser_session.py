"""Probe: what browsers actually publish to the Windows media session.

Answers two questions the overlay currently assumes rather than observes:

1. **What fields arrive.** Records every media-session property verbatim, so
   `split_browser_title` / `clean_title` can be corrected against real payloads
   instead of strings written from expectation.
2. **Whether interpolation holds.** `Snapshot.live_position()` adds elapsed
   wall-clock time to a reported position. That is only sound if the source
   refreshes its timeline often enough and does not drift. Here the prediction
   is scored against the next genuine update.

A reported position is stale between updates, so comparing every poll would
measure nothing. Drift is only recorded when `last_updated_time` actually
moves: that is the moment the source states a fresh truth and the prediction
made from the previous one can be judged.

Usage:
    python probe_browser_session.py [seconds] [label]

Play something in the browser first, then run it. Results are appended to
`browser_session_report.json` next to this file.
"""
import asyncio
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from winsdk.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as SessionManager,
)

REPORT = Path(__file__).parent / "browser_session_report.json"
POLL = 0.25


async def read_all():
    """Every session, with every field the API exposes."""
    mgr = await SessionManager.request_async()
    out = []
    for s in mgr.get_sessions():
        try:
            media = await s.try_get_media_properties_async()
            tl = s.get_timeline_properties()
            pb = s.get_playback_info()
            updated = tl.last_updated_time
            if updated is not None and updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            out.append({
                "app": s.source_app_user_model_id or "",
                "artist": media.artist or "",
                "title": media.title or "",
                "album_artist": media.album_artist or "",
                "album_title": media.album_title or "",
                "subtitle": media.subtitle or "",
                "genres": list(media.genres or []),
                "track_number": media.track_number,
                "album_track_count": media.album_track_count,
                "playback_type": media.playback_type.name if media.playback_type else None,
                "has_thumbnail": media.thumbnail is not None,
                "status": pb.playback_status.name if pb and pb.playback_status else "",
                "position": tl.position.total_seconds(),
                "start": tl.start_time.total_seconds(),
                "end": tl.end_time.total_seconds(),
                "updated_at": updated,
            })
        except Exception as e:
            out.append({"app": s.source_app_user_model_id or "", "error": repr(e)})
    return out


def predict(prev: dict, at: datetime) -> float:
    """The overlay's own interpolation, replayed so it can be scored."""
    pos = prev["position"]
    if prev["status"] == "PLAYING" and prev["updated_at"] is not None:
        pos += (at - prev["updated_at"]).total_seconds()
    if prev["end"] > 0:
        pos = min(pos, prev["end"])
    return max(pos, 0.0)


async def run(seconds: float, label: str):
    print(f"Probing for {seconds:.0f}s (label: {label})\n")
    prev: dict = {}
    fields: dict = {}
    drift: dict = {}
    updates: dict = {}          # seconds between genuine timeline updates
    last_update_seen: dict = {}
    events: list = []

    t_end = asyncio.get_event_loop().time() + seconds
    while asyncio.get_event_loop().time() < t_end:
        for s in await read_all():
            app = s["app"]
            if "error" in s:
                continue
            fields.setdefault(app, s)  # first full payload seen, kept verbatim

            p = prev.get(app)
            if p:
                if s["title"] != p["title"]:
                    events.append(f"{app}: track changed -> {s['title']!r}")
                if s["status"] != p["status"]:
                    events.append(f"{app}: {p['status']} -> {s['status']} at {s['position']:.1f}s")
                # A genuine update: the source restated its timeline
                if s["updated_at"] and p["updated_at"] and s["updated_at"] > p["updated_at"]:
                    gap = (s["updated_at"] - p["updated_at"]).total_seconds()
                    updates.setdefault(app, []).append(gap)
                    last_update_seen[app] = s["updated_at"]
                    if p["status"] == "PLAYING" and s["title"] == p["title"]:
                        err = predict(p, s["updated_at"]) - s["position"]
                        # A seek is a real jump, not a prediction failure
                        if abs(err) < 5.0:
                            drift.setdefault(app, []).append(err)
                        else:
                            events.append(f"{app}: jump of {err:+.1f}s (seek?) at {s['position']:.1f}s")
            prev[app] = s
        await asyncio.sleep(POLL)

    # --- report ---
    result = {"label": label, "seconds": seconds, "sessions": {}}
    for app, f in fields.items():
        d = drift.get(app, [])
        u = updates.get(app, [])
        payload = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in f.items()}
        entry = {
            "raw_payload": payload,
            "timeline_updates": len(u),
            "update_gap_median_s": round(statistics.median(u), 2) if u else None,
            "update_gap_max_s": round(max(u), 2) if u else None,
            "drift_samples": len(d),
            "drift_median_s": round(statistics.median(d), 3) if d else None,
            "drift_max_abs_s": round(max(abs(x) for x in d), 3) if d else None,
        }
        result["sessions"][app] = entry

        print(f"--- {app} ---")
        print(f"  artist={f['artist']!r}")
        print(f"  title={f['title']!r}")
        print(f"  album={f['album_title']!r} subtitle={f['subtitle']!r}")
        print(f"  playback_type={f['playback_type']} duration={f['end']:.1f}s thumb={f['has_thumbnail']}")
        if u:
            print(f"  timeline updates: {len(u)}, gap median {statistics.median(u):.2f}s, max {max(u):.2f}s")
        else:
            print("  timeline updates: NONE — the source never restated its position")
        if d:
            print(f"  interpolation error: median {statistics.median(d):+.3f}s, "
                  f"max |{max(abs(x) for x in d):.3f}|s over {len(d)} samples")
        else:
            print("  interpolation error: not measurable (no update while playing)")
        print()

    if events:
        result["events"] = events
        print("Events:")
        for e in events[:20]:
            print(f"  {e}")

    history = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else []
    history.append(result)
    REPORT.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nAppended to {REPORT.name}")


if __name__ == "__main__":
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    lbl = sys.argv[2] if len(sys.argv) > 2 else "unlabelled"
    asyncio.run(run(secs, lbl))
