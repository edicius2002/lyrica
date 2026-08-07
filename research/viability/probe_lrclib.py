"""Viabilidad LRCLIB: cobertura, latencia y comportamiento sin API key.

Prueba tres perfiles de metadata reales:
  - spotify: metadata limpia (artista/título/duración exactos)
  - youtube: títulos sucios de video, sin duración confiable
  - soundcloud: remixes/underground con títulos no estándar
"""
import statistics
import sys
import time

import requests

sys.stdout.reconfigure(encoding="utf-8")

API = "https://lrclib.net/api"
HEADERS = {"User-Agent": "lyrica-viability-test/0.1 (research)"}

# (artist, track, duration_s | None, perfil)
TESTS = [
    # Perfil Spotify/YTM: metadata limpia, duración conocida
    ("The Weeknd", "Blinding Lights", 200, "spotify"),
    ("Bad Bunny", "Monaco", 267, "spotify"),
    ("Radiohead", "Karma Police", 264, "spotify"),
    ("Peso Pluma", "Ella Baila Sola", 165, "spotify"),
    ("Daft Punk", "Get Lucky", 369, "spotify"),
    ("Aitana", "Formentera", 184, "spotify"),
    ("Kendrick Lamar", "HUMBLE.", 177, "spotify"),
    ("Taylor Swift", "Anti-Hero", 200, "spotify"),
    ("Soda Stereo", "De Música Ligera", 213, "spotify"),
    ("Billie Eilish", "BIRDS OF A FEATHER", 210, "spotify"),
    # Perfil YouTube: título de video sucio, sin duración
    ("Queen", "Bohemian Rhapsody (Official Video Remastered)", None, "youtube"),
    ("Dua Lipa", "Levitating (Official Music Video)", None, "youtube"),
    ("Shakira", "Shakira: Bzrp Music Sessions, Vol. 53", None, "youtube"),
    # Perfil SoundCloud: remixes / underground
    ("Flume", "Say It (feat. Tove Lo) [Clean Bandit Remix]", None, "soundcloud"),
    ("Unknown Artist", "Midnight Vibes Vol. 3 (Free DL)", None, "soundcloud"),
    ("skrillex", "rumble", 203, "soundcloud"),
]


def clean_title(t: str) -> str:
    import re
    t = re.sub(r"[\(\[][^\)\]]*(official|video|remaster|audio|lyric|hd|4k|mv)[^\)\]]*[\)\]]", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip(" -|")


def query(artist, track, duration):
    params = {"artist_name": artist, "track_name": track}
    if duration:
        params["duration"] = duration
    t0 = time.perf_counter()
    r = requests.get(f"{API}/get", params=params, headers=HEADERS, timeout=10)
    ms = (time.perf_counter() - t0) * 1000
    if r.status_code == 200:
        d = r.json()
        kind = "SYNCED" if d.get("syncedLyrics") else ("PLAIN" if d.get("plainLyrics") else "EMPTY")
        return kind, ms, r.status_code
    return "MISS", ms, r.status_code


def search_fallback(artist, track):
    """Fallback: /api/search cuando /get falla (títulos sucios)."""
    t0 = time.perf_counter()
    r = requests.get(f"{API}/search", params={"q": f"{artist} {clean_title(track)}"}, headers=HEADERS, timeout=10)
    ms = (time.perf_counter() - t0) * 1000
    if r.status_code == 200 and r.json():
        best = r.json()[0]
        kind = "SYNCED" if best.get("syncedLyrics") else ("PLAIN" if best.get("plainLyrics") else "EMPTY")
        return kind, ms, best.get("trackName"), best.get("artistName")
    return "MISS", ms, None, None


def main():
    print("=== LRCLIB /api/get (lookup exacto) ===")
    latencies, results = [], {}
    for artist, track, dur, profile in TESTS:
        kind, ms, code = query(artist, track, dur)
        latencies.append(ms)
        results.setdefault(profile, []).append(kind)
        extra = ""
        if kind == "MISS":
            k2, _, tn, an = search_fallback(artist, track)
            extra = f"  -> /search fallback: {k2}" + (f" ('{tn}' - {an})" if tn else "")
            results[profile][-1] = f"MISS->{k2}"
        print(f"[{profile:10s}] {kind:6s} {ms:6.0f}ms HTTP{code}  {artist} - {track}{extra}")

    print(f"\nLatencia /get: mediana {statistics.median(latencies):.0f}ms, max {max(latencies):.0f}ms")
    print("\nResumen por perfil:")
    for p, kinds in results.items():
        print(f"  {p:10s}: {kinds}")

    # Test de ráfaga: ¿hay rate limit real?
    print("\n=== Ráfaga: 30 requests seguidas (detectar 429/bloqueo) ===")
    codes, burst_lat = [], []
    t0 = time.perf_counter()
    for _ in range(30):
        t1 = time.perf_counter()
        r = requests.get(f"{API}/get", params={"artist_name": "Coldplay", "track_name": "Yellow", "duration": 269},
                         headers=HEADERS, timeout=10)
        burst_lat.append((time.perf_counter() - t1) * 1000)
        codes.append(r.status_code)
    total = time.perf_counter() - t0
    from collections import Counter
    print(f"30 requests en {total:.1f}s -> códigos: {dict(Counter(codes))}")
    print(f"Latencia ráfaga: mediana {statistics.median(burst_lat):.0f}ms, max {max(burst_lat):.0f}ms")
    print("VEREDICTO: sin rate limit" if set(codes) == {200} else "OJO: hubo códigos != 200")


if __name__ == "__main__":
    main()
