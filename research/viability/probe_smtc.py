"""Viabilidad SMTC (Windows Global System Media Transport Controls).

Lista todas las sesiones de medios activas (Spotify app, Chrome, etc.),
muestra metadata y monitorea la posición durante unos segundos para medir
la precisión de actualización por fuente.
"""
import asyncio
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

from winsdk.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as SessionManager,
)


async def dump_sessions(seconds: float = 12.0, interval: float = 1.0):
    mgr = await SessionManager.request_async()
    sessions = mgr.get_sessions()
    if not sessions:
        print("No hay sesiones de medios activas. Abre Spotify/Chrome y reproduce algo.")
        return

    print(f"Sesiones activas: {len(sessions)}")
    for s in sessions:
        print(f"  - app: {s.source_app_user_model_id}")

    print(f"\nMonitoreando {seconds:.0f}s (intervalo {interval}s)...\n")
    t_end = time.time() + seconds
    prev_pos = {}
    while time.time() < t_end:
        mgr = await SessionManager.request_async()
        for s in mgr.get_sessions():
            app = s.source_app_user_model_id
            try:
                media = await s.try_get_media_properties_async()
                tl = s.get_timeline_properties()
                pb = s.get_playback_info()
                pos_s = tl.position.total_seconds()
                dur_s = tl.end_time.total_seconds()
                status = pb.playback_status.name if pb and pb.playback_status else "?"
                delta = ""
                if app in prev_pos:
                    delta = f" (Δ{pos_s - prev_pos[app]:+.2f}s)"
                prev_pos[app] = pos_s
                print(f"[{app}] {status:8s} {media.artist!r} - {media.title!r} "
                      f"pos={pos_s:7.2f}/{dur_s:6.1f}s{delta} album={media.album_title!r}")
            except Exception as e:
                print(f"[{app}] ERROR: {e}")
        print("-")
        await asyncio.sleep(interval)


if __name__ == "__main__":
    asyncio.run(dump_sessions())
