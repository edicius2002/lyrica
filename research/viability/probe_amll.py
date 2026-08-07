# -*- coding: utf-8 -*-
"""Viabilidad amll-ttml-db (letras palabra-por-palabra TTML, gratis en GitHub).

 1. Estructura del repo (carpetas por plataforma: ncm/spotify/qq/am).
 2. Lookup directo por ID de Spotify vía raw.githubusercontent / jsDelivr.
 3. Cobertura aproximada para canciones occidentales vs asiáticas.
"""
import sys, requests

sys.stdout.reconfigure(encoding="utf-8")

GH_API = "https://api.github.com/repos/amll-dev/amll-ttml-db"
RAW = "https://raw.githubusercontent.com/amll-dev/amll-ttml-db/main"
CDN = "https://cdn.jsdelivr.net/gh/amll-dev/amll-ttml-db@main"

# Spotify track IDs conocidos
SPOTIFY_IDS = {
    "The Weeknd - Blinding Lights": "0VjIjW4GlUZAMYd2vXMi3b",
    "Taylor Swift - Anti-Hero": "0V3wPSX9ygBnCm8psDIegu",
    "Bad Bunny - Monaco": "4MjDJD8cW7iVeWInc2Bdyj",
    "Billie Eilish - BIRDS OF A FEATHER": "6dOtVTDdiauQNBQEDOtlAB",
}


def main():
    print("=== 1. Estructura del repo ===")
    r = requests.get(f"{GH_API}/contents/", timeout=15)
    if r.status_code == 200:
        dirs = [x["name"] for x in r.json() if x["type"] == "dir"]
        print(f"Carpetas: {dirs}")
    else:
        print(f"GitHub API: HTTP {r.status_code} (límite 60 req/h sin auth)")
        dirs = []

    print("\n=== 2. Lookup por Spotify ID (raw + jsDelivr) ===")
    for name, sid in SPOTIFY_IDS.items():
        hits = []
        for base, label in [(RAW, "raw.github"), (CDN, "jsdelivr")]:
            rr = requests.get(f"{base}/spotify-lyrics/{sid}.ttml", timeout=15)
            hits.append(f"{label}:{rr.status_code}")
            if rr.status_code == 200:
                print(f"  HIT  {name}: {label} ({len(rr.text)} bytes TTML)")
                break
        else:
            print(f"  MISS {name}: {hits}")

    print("\n=== 3. Tamaño de índices por plataforma ===")
    for d in ("spotify-lyrics", "ncm-lyrics", "qq-lyrics", "am-lyrics"):
        rr = requests.get(f"{CDN}/{d}/index.jsonl", timeout=20)
        if rr.status_code == 200:
            n = rr.text.count("\n")
            print(f"  {d}: index.jsonl con ~{n} entradas")
        else:
            # probar listado vía API de git trees (solo conteo)
            rr2 = requests.get(f"{GH_API}/contents/{d}", timeout=15)
            if rr2.status_code == 200:
                print(f"  {d}: {len(rr2.json())} archivos (listado truncado a 1000)")
            else:
                print(f"  {d}: no accesible (HTTP {rr.status_code}/{rr2.status_code})")


if __name__ == "__main__":
    main()
