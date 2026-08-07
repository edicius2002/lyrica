# -*- coding: utf-8 -*-
"""Viabilidad Musixmatch NO OFICIAL (API de la app de escritorio).

Prueba las condiciones reales:
 1. ¿Se puede obtener un usertoken gratis? (token.get)
 2. ¿Devuelve letras sincronizadas (subtitles) y palabra-por-palabra (richsync)?
 3. ¿Aparecen captchas / límites?
Esto es un endpoint no documentado: puede romperse o bloquearse en cualquier momento.
"""
import sys, time, json
import requests

sys.stdout.reconfigure(encoding="utf-8")

BASE = "https://apic-desktop.musixmatch.com/ws/1.1"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Cookie": "AWSELB=0; AWSELBCORS=0",
}


def get_token():
    r = requests.get(f"{BASE}/token.get", params={"app_id": "web-desktop-app-v1.0"},
                     headers=HEADERS, timeout=15)
    d = r.json()
    hdr = d.get("message", {}).get("header", {})
    status = hdr.get("status_code")
    hint = hdr.get("hint")
    if status == 200:
        return d["message"]["body"]["user_token"], None
    return None, f"status={status} hint={hint}"


def macro_call(token, artist, track):
    params = {
        "format": "json", "namespace": "lyrics_richsynched",
        "subtitle_format": "mxm", "app_id": "web-desktop-app-v1.0",
        "q_artist": artist, "q_track": track, "usertoken": token,
    }
    r = requests.get(f"{BASE}/macro.subtitles.get", params=params, headers=HEADERS, timeout=15)
    d = r.json()
    hdr = d["message"]["header"]
    if hdr["status_code"] != 200:
        return {"error": f"status={hdr['status_code']} hint={hdr.get('hint')}"}
    calls = d["message"]["body"]["macro_calls"]
    out = {}
    tr = calls.get("matcher.track.get", {}).get("message", {}).get("body", {}).get("track", {})
    out["match"] = f"{tr.get('artist_name')} - {tr.get('track_name')}" if tr else None
    out["has_subtitles"] = bool(tr.get("has_subtitles"))
    out["has_richsync"] = bool(tr.get("has_richsync"))
    sub = calls.get("track.subtitles.get", {}).get("message", {}).get("body", {})
    if isinstance(sub, dict) and sub.get("subtitle_list"):
        out["subtitle_len"] = len(sub["subtitle_list"][0]["subtitle"]["subtitle_body"])
    return out


def richsync_call(token, artist, track):
    params = {"format": "json", "app_id": "web-desktop-app-v1.0",
              "q_artist": artist, "q_track": track, "usertoken": token}
    r = requests.get(f"{BASE}/track.richsync.get", params=params, headers=HEADERS, timeout=15)
    d = r.json()
    hdr = d["message"]["header"]
    if hdr["status_code"] != 200:
        return f"status={hdr['status_code']} hint={hdr.get('hint')}"
    body = d["message"]["body"].get("richsync", {})
    rb = body.get("richsync_body", "")
    if rb:
        lines = json.loads(rb)
        return f"OK: {len(lines)} líneas word-synced (ej: {[w['c'] for w in lines[0]['l'][:5]]})"
    return "sin richsync_body"


def main():
    print("=== 1. Obtener usertoken gratuito ===")
    token, err = get_token()
    if not token:
        print(f"FALLO token.get: {err}")
        print("Condición real: Musixmatch está bloqueando la obtención de tokens (captcha/IP).")
        return
    print(f"Token obtenido: {token[:16]}... (gratis, sin registro)")

    print("\n=== 2. Subtítulos + flags richsync (macro.subtitles.get) ===")
    for artist, track in [("The Weeknd", "Blinding Lights"), ("Bad Bunny", "Monaco"),
                          ("Soda Stereo", "De Música Ligera")]:
        time.sleep(1.5)
        res = macro_call(token, artist, track)
        print(f"  {artist} - {track}: {res}")

    print("\n=== 3. Richsync palabra por palabra (track.richsync.get) ===")
    for artist, track in [("The Weeknd", "Blinding Lights"), ("Taylor Swift", "Anti-Hero")]:
        time.sleep(1.5)
        print(f"  {artist} - {track}: {richsync_call(token, artist, track)}")


if __name__ == "__main__":
    main()
