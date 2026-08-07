"""Probe: can the overlay seek, or only observe?

better-lyrics can jump to a line because it lives inside the page and owns the
player. Lyrica watches from outside, so seeking is only possible if the media
session itself accepts a position command — and whether it does is up to each
app, not to us.

Reports what every session claims to support, then offers to nudge the current
one to prove the claim.
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from winsdk.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as SessionManager,
)

CONTROLS = [
    "is_play_enabled", "is_pause_enabled", "is_stop_enabled",
    "is_next_enabled", "is_previous_enabled",
    "is_playback_position_enabled",     # the one that matters
    "is_fast_forward_enabled", "is_rewind_enabled",
]


async def main(do_seek: bool) -> None:
    mgr = await SessionManager.request_async()
    sessions = list(mgr.get_sessions())
    if not sessions:
        print("Nothing is playing.")
        return

    for s in sessions:
        app = s.source_app_user_model_id or "?"
        media = await s.try_get_media_properties_async()
        controls = s.get_playback_info().controls
        tl = s.get_timeline_properties()
        print(f"--- {app}")
        print(f"    {media.artist!r} - {media.title!r}  at {tl.position.total_seconds():.1f}s")
        for name in CONTROLS:
            value = getattr(controls, name, None)
            mark = "yes" if value else "no "
            print(f"    {mark}  {name}")

        if not do_seek:
            continue
        if not getattr(controls, "is_playback_position_enabled", False):
            print("    -> claims no seek support; not attempting")
            continue

        target = max(0.0, tl.position.total_seconds() - 10.0)
        # The API takes 100-nanosecond ticks, not seconds.
        ok = await s.try_change_playback_position_async(int(target * 10_000_000))
        await asyncio.sleep(1.2)
        after = (await SessionManager.request_async())
        moved = None
        for s2 in after.get_sessions():
            if s2.source_app_user_model_id == app:
                moved = s2.get_timeline_properties().position.total_seconds()
        print(f"    -> asked for {target:.1f}s, accepted={ok}, now at {moved}")


if __name__ == "__main__":
    asyncio.run(main("--seek" in sys.argv))
