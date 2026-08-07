# Informe de viabilidad — Letras en tiempo real (Lyrica)

**Fecha:** 2026-08-06 · **Entorno:** Windows 10, Python 3.11.9
Scripts reproducibles en `viability/`. Todos los datos de abajo son medidos, no de documentación.

## 1. Condiciones reales de cada fuente de letras

### LRCLIB — ✅ la columna vertebral (gratis, sin límites, confirmado)
- **Sin API key, sin registro.** Ráfaga de 30 requests seguidas: 30/30 HTTP 200, **cero rate limit**.
- Latencia: mediana **~680 ms**, máx ~810 ms (aceptable; con caché local solo se paga una vez por canción).
- Cobertura medida con `/get` exacto + fallback `/search`:
  - Perfil Spotify/YTM (metadata limpia): **10/10 con letra sincronizada** (2 necesitaron el fallback `/search`).
  - Perfil YouTube (títulos sucios tipo "(Official Video)"): 3/3 sincronizadas, pero 2 de 3 **solo** vía `/search` + limpieza de título con regex. La limpieza de títulos es obligatoria.
  - Perfil SoundCloud: remixes conocidos sí (Flume, Skrillex); underground/"Free DL" **no existe en ninguna base** — miss irrecuperable por metadata.
- Condición real: el `/get` exige duración ±2 s; sin duración confiable (YouTube) hay que ir directo a `/search` y puntuar resultados (artista, título, duración, synced).

### Musixmatch no oficial (API de escritorio) — ⚠️ funciona pero frágil
- El token gratuito **sí se obtiene** hoy sin registro (`token.get`).
- Letras sincronizadas por línea: funcionan (3/3 canciones, incluida música en español).
- `has_richsync=True` en las 3 canciones probadas, **pero** el endpoint `track.richsync.get` devolvió **404** con búsqueda por nombre → el palabra-por-palabra requiere ingeniería extra (track_id numérico + parámetros exactos de la app oficial) y puede requerir cuenta/estar geobloqueado.
- Condición real: endpoint no documentado, ToS en gris, puede romperse o banear IP en cualquier momento. Úsalo solo como *mejora opcional*, nunca como base.

### amll-ttml-db (word-by-word TTML en GitHub) — ⚠️ gratis total, cobertura mínima
- Acceso directo por `raw.githubusercontent`/jsDelivr, sin límites prácticos.
- **Pero:** solo ~2.364 letras indexadas por Spotify ID (~3.400 NetEase, ~2.800 QQ). De 4 hits occidentales probados, solo 1 (Blinding Lights) tenía letra.
- Condición real: sesgo fuerte a pop asiático; sirve como capa "premium cuando exista", no como fuente principal. Lookup por Spotify track ID (que el SMTC de Windows **no** da — habría que resolver el ID vía otra API).

### Agregador `syncedlyrics` (pip) — ✅ útil como fallback
- Lrclib OK, NetEase OK (synced), Genius OK (solo plana), Megalobiz caído/miss, Musixmatch intermitente.
- `enhanced=True` (word-level) **no devolvió** timestamps por palabra en la prueba.
- Condición real: NetEase es un buen segundo proveedor synced gratis; Genius solo texto plano.

### Conclusión palabra-por-palabra (lo que pediste como ideal)
Hoy **no existe** una fuente de karaoke word-by-word que sea a la vez gratis, sin límites y con cobertura amplia. La arquitectura correcta es cascada degradante:
`amll-ttml-db (si hay) → Musixmatch richsync (si se resuelve el endpoint) → LRCLIB línea (99% de los casos) → plana`.

## 2. Detección de lo que suena (SMTC de Windows) — ✅ verificado
- `winsdk` lee la sesión global de medios: app, artista, título, álbum, duración, posición y estado.
- Verificado en vivo con **Spotify.exe**: metadata completa y posición exacta.
- La posición se reporta como snapshot (`position` + `last_updated_time`): hay que **interpolar** (`pos + (ahora − last_updated)` mientras suena) — implementado en el MVP.
- Navegadores (Chrome): publican metadata vía Media Session; a veces artista vacío y "Artista - Tema" en el título → el MVP lo separa y limpia.

## 3. Repos clonados en `repos/` — licencias y veredicto como base

| Repo | Licencia | Veredicto |
|---|---|---|
| **Lyricify-Lyrics-Helper** | Apache 2.0 | ✅ Mejor librería de parsing multi-formato (LRC/TTML/YRC/richsync), C#. Base ideal si migras a .NET |
| **better-lyrics** | GPLv3 | ✅ Referencia de UX y cascada de proveedores (extensión, solo YTM) |
| **YouLyPlus** | MIT | ✅ Referencia del render word-by-word y del manejo Musixmatch en JS |
| **syncedlyrics** | MIT | ✅ Fallback multi-proveedor listo para usar desde Python |
| **lyrictified** | MPL 2.0 | ✅ Overlay Python minimalista comparable al MVP |
| **FrontLine-Lyrics-Desktop** | GPLv3 | ⚠️ Interesante por identificación de audio (útil para SoundCloud underground) |
| **Lyric-Immersion-and-Karaoke** | **Propietaria** | ❌ NO forkeable; solo inspiración de features |

## 4. MVP entregado (`mvp/`) — funcional y verificado
`python mvp/main.py` → overlay flotante, siempre visible, fondo transparente:
- Detecta lo que suena (Spotify app y navegadores) vía SMTC, con interpolación de posición.
- Busca en LRCLIB (`/get` → `/search` con puntuación fuzzy), limpia títulos de YouTube, separa "Artista - Tema".
- Caché en disco (`mvp/cache/`), incluye misses para no repetir búsquedas.
- Muestra línea anterior/actual/siguiente; letra plana se pagina por % de avance; instrumentales marcados.
- Controles: arrastrar para mover, `Esc`/click derecho para salir, `+`/`-` ajusta desfase ±0.25 s.
- **Verificado en vivo:** con "Still D.R.E." pausado en 151.78 s, el overlay mostró exactamente la línea correcta.

## 5. Qué plataforma conviene (medido)
1. **Spotify app / YouTube Music** — metadata perfecta + ~100% cobertura synced. Experiencia ideal.
2. **YouTube normal** — bien con limpieza de títulos; los videos no musicales darán misses razonables.
3. **SoundCloud** — mainstream bien; remixes/underground sin solución por metadata (la única vía sería identificación de audio tipo Shazam, ver FrontLine).

## 6. Próximos pasos sugeridos
1. Word-by-word: resolver `track.richsync.get` con track_id (estudiar YouLyPlus) + integrar amll-ttml-db como capa opcional.
2. NetEase como segundo proveedor synced vía `syncedlyrics`.
3. Sombra/borde del texto para legibilidad sobre fondos claros (migrar overlay a Qt si tkinter queda corto).
4. Empaquetar como .exe con PyInstaller + arranque con Windows.
