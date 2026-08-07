# -*- coding: utf-8 -*-
"""Viabilidad del paquete `syncedlyrics` como agregador multi-proveedor.

Prueba cada proveedor por separado para mapear condiciones reales
(qué responde, qué está caído, qué tiene captcha).
"""
import sys, time

sys.stdout.reconfigure(encoding="utf-8")

import syncedlyrics

PROVIDERS = ["Lrclib", "Musixmatch", "NetEase", "Megalobiz", "Genius"]
TRACKS = ["The Weeknd Blinding Lights", "Soda Stereo De Música Ligera"]


def main():
    for track in TRACKS:
        print(f"\n=== {track} ===")
        for p in PROVIDERS:
            t0 = time.perf_counter()
            try:
                lrc = syncedlyrics.search(track, providers=[p])
                ms = (time.perf_counter() - t0) * 1000
                if lrc:
                    synced = "[" in lrc.splitlines()[0]
                    print(f"  {p:12s} OK   {ms:6.0f}ms  {'synced' if synced else 'plain '} "
                          f"({len(lrc.splitlines())} líneas)")
                else:
                    print(f"  {p:12s} MISS {ms:6.0f}ms")
            except Exception as e:
                ms = (time.perf_counter() - t0) * 1000
                print(f"  {p:12s} ERR  {ms:6.0f}ms  {type(e).__name__}: {str(e)[:80]}")

    print("\n=== Word-level (enhanced=True, vía Musixmatch richsync) ===")
    t0 = time.perf_counter()
    try:
        lrc = syncedlyrics.search("The Weeknd Blinding Lights", enhanced=True)
        ms = (time.perf_counter() - t0) * 1000
        if lrc and "<" in lrc:
            print(f"  OK {ms:.0f}ms — formato enhanced LRC con timestamps por palabra")
            print("  Ejemplo:", lrc.splitlines()[2][:100])
        else:
            print(f"  Sin word-level ({ms:.0f}ms): {'línea normal' if lrc else 'MISS'}")
    except Exception as e:
        print(f"  ERR: {type(e).__name__}: {str(e)[:100]}")


if __name__ == "__main__":
    main()
