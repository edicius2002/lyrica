# Artist Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking. Execution is inline by the primary agent, as requested by the user.

**Goal:** Resolver nombres de canal Topic/VEVO y variantes generales sin rechazar artistas equivalentes ni aceptar artistas por fragmentos de nombre.

**Architecture:** Una política pura de nombres de artista produce lecturas con procedencia y relaciones de comparación. Candidatos compartidos llevan esa información hasta proveedores y portadas; Lyrics separa consulta de nombre confirmado. Los adaptadores existentes y las claves ordinarias de caché siguen disponibles.

**Tech Stack:** Python >=3.11, dataclasses, re, unicodedata, pytest y requests ya presentes.

**Spec:** [2026-09-12-artist-normalization-design.md](../specs/2026-09-12-artist-normalization-design.md).

## Global Constraints

- Python >=3.11; sin dependencias nuevas.
- Conservar `Snapshot.artist`, `Snapshot.title`, `track_key()` y `playback_key()`.
- Conservar los offsets por reproducción y los enlaces de sesión de Windows.
- No cambiar el ranking de precisión, las voces, el backing ni sus tiempos.
- No borrar cachés, tokens ni ajustes del usuario.
- No acceder a servicios reales durante las pruebas automatizadas.
- No activar aceleración de hardware ni iniciar una interfaz para este trabajo.
- Ejecución directa por el agente principal, en tareas secuenciales.

## Preparación y mapa de archivos

Estado al preparar el plan: `research/ARTIST_CHANNEL_SUFFIXES.md` es una nota
nueva todavía sin commit. No descartarla ni confundirla con cambios de terceros.
La selección de 172 pruebas del diagnóstico pasó; ese resultado no sustituye
la línea base al comenzar la implementación.

| Archivo | Responsabilidad del cambio |
| --- | --- |
| Crear `src/lyrica/artist_names.py` | Reglas, procedencia de artista, comparación y modos de caché. |
| Crear `src/lyrica/metadata.py` | Candidato de búsqueda con título original y adaptador de tuplas. |
| `src/lyrica/sessions/base.py` | Producir candidatos, preservar entradas y adaptadores antiguos. |
| `src/lyrica/providers/identity.py` | Transportar la lectura del artista y delegar su validación. |
| `src/lyrica/providers/{lrclib,community,netease,musixmatch}.py` | Aplicar relación compartida y guardar identidad confirmada. |
| `src/lyrica/lyrics.py` | Campo opcional `resolved`. |
| `src/lyrica/providers/__init__.py` | Propagar procedencia, ordenar alternativas y claves por modo. |
| `src/lyrica/providers/cache.py` | Serializar y validar `resolved` sin invalidar v12. |
| `src/lyrica/artwork.py` | Comparación compartida, modo de caché y revalidación de matches. |
| `src/lyrica/app.py` | Consumir candidatos completos y nombre confirmado. |
| `tests/test_artist_names.py`, `tests/test_artist_normalization_flow.py` | Nuevas regresiones puras y del recorrido completo. |
| Pruebas existentes de metadata, identity, providers, caché y portadas | Mantener contratos y extender casos reales. |
| `docs/song-identity-sessions-cache.md` | Actualizar el contrato finalmente implementado. |

Antes de ejecutar, leer el spec y las habilidades de ejecución, TDD y worktrees.
Si se necesita aislamiento, crear el worktree en ese momento; no cambiar de
rama con trabajo ajeno sin preservarlo. Ejecutar cada ciclo rojo/verde y hacer
commits locales por tarea después de verificar; no publicar ni fusionar como
parte de este plan.

## Tarea 1: Política de artistas con procedencia

**Files:** crear `src/lyrica/artist_names.py`, `tests/test_artist_names.py`.

**Interfaces:**

```python
from dataclasses import dataclass
from typing import Literal

ArtistRule = Literal['original', 'topic', 'vevo', 'decorated']
ArtistRelation = Literal['unknown', 'exact', 'credit', 'channel_alias', 'mismatch']

@dataclass(frozen=True)
class ArtistReading:
    name: str
    original: str
    rule: ArtistRule = 'original'

# Public functions implemented in this task:
# clean_artist(value: str) -> str
# artist_readings(value: str, *, channel_hint: bool) -> tuple[ArtistReading, ...]
# artist_relation(requested: ArtistReading | str, returned: str) -> ArtistRelation
# artist_cache_mode(reading: ArtistReading | None) -> str
```

- [x] Escribir regresiones parametrizadas con Topic, BTS, U2, Queen, espacios
  Unicode, guiones admitidos, mayúsculas y cuerpo vacío. Incluir explícitamente
  `Topic`, `Topic - Topic`, `Hot Topic`, `Artist-Topic` y sufijos duplicados
  ambiguos; comprobar idempotencia de `clean_artist`.

```python
@pytest.mark.parametrize('raw, expected', [
    ('BTS - Topic', 'BTS'), ('U2 - Topic', 'U2'),
    ('Topic - Topic', 'Topic'), ('Topic', 'Topic'),
    ('Hot Topic', 'Hot Topic'), ('Artist-Topic', 'Artist-Topic'),
    ('  Queen  –  TOPIC  ', 'Queen'),
])
def test_clean_artist(raw, expected):
    assert clean_artist(raw) == expected
    assert clean_artist(clean_artist(raw)) == expected

def test_compact_alias_requires_vevo_provenance():
    alias = ArtistReading('BillieEilish', 'BillieEilishVEVO', 'vevo')
    assert artist_relation(alias, 'Billie Eilish') == 'channel_alias'
    assert artist_relation('BillieEilish', 'Billie Eilish') == 'mismatch'
    assert artist_relation('Queen', 'Queensryche') == 'mismatch'
```

- [x] Ejecutar `python -m pytest -q tests/test_artist_names.py` y verificar rojo.
- [x] Implementar regex ancladas al final sobre espacios normalizados. Usar una
  tabla explícita de etiquetas y separadores del spec. No modificar `textmatch.fold`
  global: envolverlo localmente con `' '.join(fold(value).split())`.
- [x] Implementar `artist_relation`: desconocido/colectivo primero; igualdad
  completa; alias VEVO con igualdad compacta completa; créditos completos de
  la política existente; incompatibilidad. Ninguna rama usa `left in right`.
  El modo de caché es `'vevo'` solo para regla VEVO, `''` en los demás casos.
  Aplicar `clean_artist` a ambos nombres antes del plegado para que Topic tenga
  la misma semántica en consultas y registros devueltos; no deducir VEVO del
  nombre compacto cuando la procedencia no está presente.
- [x] Añadir casos de lecturas Official/MusicVEVO, etiquetas separadas y nombres
  que contienen Music/TV/Records. Verificar que `channel_hint=False` no genera
  variantes VEVO/decorativas y que `Various Artists - Topic` queda desconocido.
- [x] Ejecutar la prueba de esta tarea y hacer commit local
  `feat: define provenance-aware artist normalization`.

## Tarea 2: Candidatos compartidos desde la sesión

**Files:** crear `src/lyrica/metadata.py`; modificar `src/lyrica/sessions/base.py`;
extender `tests/test_metadata.py`, `tests/test_artist_names.py`.

**Interfaces:** consume `ArtistReading`, `artist_readings`, `clean_artist`.

```python
@dataclass(frozen=True)
class LookupCandidate:
    artist: ArtistReading
    title: str
    raw_title: str

# metadata.py:
# as_candidate(value: LookupCandidate | tuple) -> LookupCandidate
# accepts legacy (artist, title) and (artist, title, raw_title).
# Snapshot.search_candidates() -> list[LookupCandidate]
# Snapshot.lookup_candidates() -> list[tuple[str, str]] remains compatible.
# Snapshot.lyrics_candidates() -> list[tuple[str, str, str]] remains compatible.
```

- [x] Añadir pruebas de Snapshot con `BTS - Topic / Song`,
  `Queen - Topic / Queen - Song (Official Video)`,
  `BillieEilishVEVO / CHIHIRO` y canal ajeno con `Billie Eilish - CHIHIRO`.

```python
def test_topic_snapshot_keeps_playback_identity():
    snap = Snapshot(app='chrome.exe', artist='BTS - Topic', title='Song')
    before = (snap.artist, snap.title, snap.track_key(), snap.playback_key())
    candidate = snap.search_candidates()[0]
    assert (candidate.artist.name, candidate.title) == ('BTS', 'Song')
    assert candidate.artist.original == 'BTS - Topic'
    assert before == (snap.artist, snap.title, snap.track_key(), snap.playback_key())
    assert len(snap.search_candidates()) <= 6
```

- [x] Ejecutar `python -m pytest -q tests/test_metadata.py` y verificar el fallo nuevo.
- [x] Extraer la construcción a `search_candidates`, usando exactamente el
  orden y tope seis del spec. Quitar prefijos con el artista limpio antes de
  limpiar el título. Conservar `raw_title` de cada interpretación.
- [x] Implementar los métodos de pares/triples como proyecciones compatibles;
  `as_candidate` marca tuplas antiguas como originales, sin atribuirles una
  procedencia VEVO que no transportan. Deducir Topic mediante la regla fuerte.
- [x] Verificar igualdad de offsets, sesión y datos originales; deduplicación;
  límite de candidatos; conservación de títulos/versiones; y `ValueError` claro
  en el adaptador para longitudes o tipos inválidos.
- [x] Ejecutar `python -m pytest -q tests/test_metadata.py tests/test_sessions.py tests/test_artist_names.py`.
- [x] Hacer commit local `feat: generate shared artist lookup candidates`.

## Tarea 3: Proveedores e identidad confirmada

**Files:** `src/lyrica/providers/identity.py`, los cuatro proveedores,
`src/lyrica/lyrics.py`; `tests/test_identity.py`, `tests/test_community.py`,
`tests/test_netease.py`, `tests/test_musixmatch.py`.

**Interfaces:**

```python
# Append defaulted fields; preserve existing positional construction.
# SongQuery.artist_reading: ArtistReading | None = None
# Lyrics.resolved: tuple[str, str] | tuple[()] = ()
# validate_identity(..., requested_artist_reading: ArtistReading | None = None)
# Existing artists_compatible(requested: str, returned: str) remains a wrapper.
```

- [x] Escribir pruebas con respuestas HTTP simuladas: BTS/U2 limpios y registros
  correctos aceptados; Queen/Queensryche rechazados aun con título/duración
  idénticos; VEVO compacto aceptado solo con la lectura transportada.

```python
def test_vevo_query_retains_alias_evidence():
    reading = ArtistReading('BillieEilish', 'BillieEilishVEVO', 'vevo')
    decision = validate_identity(
        requested_artist=reading.name, requested_title='CHIHIRO',
        returned_artist='Billie Eilish', returned_title='CHIHIRO',
        requested_artist_reading=reading,
    )
    assert decision.accepted
```

- [x] Ejecutar la selección de pruebas anterior y observar fallos de contrato.
- [x] Delegar artista a `artist_relation`; conservar las comprobaciones de
  título y versión. Propagar el contexto de SongQuery en los cuatro proveedores.
- [x] Reemplazar únicamente el componente artista de `_score` en LRCLIB,
  Community y NetEase con la relación compartida y los pesos existentes. Añadir
  contexto opcional por keyword a esos scorers para no romper pruebas actuales.
- [x] NetEase debe comparar cada elemento de su lista estructurada de artistas
  con la misma política, sin concatenar nombres con espacios como si fueran uno.
  Usar `', '.join(...)` solo para presentación del crédito devuelto.
- [x] Después de validar el registro, asignar `lyrics.resolved = (artist, title)`
  si ambos existen y el artista no es colectivo. Verificar todas las ramas de
  letras/instrumental que producen un hit. No modificar `queried` en proveedores.
- [x] Probar metadatos ausentes, colaboraciones actuales, acentos y versiones;
  comprobar que una incompatibilidad no se compensa con score alto.
- [x] Ejecutar `python -m pytest -q tests/test_identity.py tests/test_community.py tests/test_netease.py tests/test_musixmatch.py`.
- [x] Hacer commit local `fix: validate artist aliases consistently across providers`.

## Tarea 4: Cascada, persistencia y aislamiento de caché

**Files:** `src/lyrica/providers/__init__.py`, `src/lyrica/providers/cache.py`;
`tests/test_providers.py`, `tests/test_provider_cache.py`,
`tests/test_cascade_precision.py` (incluye los casos de `_merge_community_backing`).

**Interfaces:** consume `LookupCandidate`, `as_candidate`, `artist_cache_mode`.
Añadir `artist_reading: ArtistReading | None = None` como keyword opcional a
`fetch_lyrics`, `_ask_providers` y `_cache_path`; propagar a `_ask_one`/SongQuery.
`fetch_for_candidates` acepta candidatos completos y las tuplas antiguas.

- [x] Añadir pruebas de hit limpio preexistente + miss Topic; un miss Topic
  no debe impedir la búsqueda BTS limpia. Añadir claves VEVO/ordinaria distintas
  para el mismo texto. Las claves ordinarias siguen siendo iguales a la fórmula
  SHA1 anterior, incluida duración y versiones.

```python
def test_alias_cache_is_separate_from_plain_text():
    reading = ArtistReading('BillieEilish', 'BillieEilishVEVO', 'vevo')
    assert _cache_path('BillieEilish', 'CHIHIRO', 300) != _cache_path(
        'BillieEilish', 'CHIHIRO', 300, artist_reading=reading)

def test_resolved_survives_cache(tmp_path):
    lyr = Lyrics(lines=[(1.0, 'line')], synced=True,
                 resolved=('Billie Eilish', 'CHIHIRO'))
    path = tmp_path / 'entry.json'
    cache.write_entry(path, cache.CacheEntry(lyr, {}), IDENTITY)
    assert cache.read_entry(path, IDENTITY).lyrics.resolved == lyr.resolved
```

- [x] Ejecutar pruebas nuevas y verificar rojo antes de implementar.
- [x] Usar `as_candidate` en la cascada; conservar `_better`, timeout e híbrido.
  Procesar alternativas `decorated` solo si no hay letras utilizables previas.
  Las tuplas antiguas no pasan keywords nuevos innecesarios a stubs históricos.
- [x] Añadir `|artist-mode:vevo` a claves VEVO y la entrada opcional
  `artist_mode: 'vevo'` en su identidad JSON. Sin clave nueva para consultas
  ordinarias. El mismo modo debe usarse al leer y escribir.
- [x] Añadir `resolved` a `_LYRIC_FIELDS`; aceptar ausencia o lista/tupla vacía,
  o par de strings no vacíos. Rechazar string escalar, tamaños distintos,
  valores no string y pares incompletos como payload inválido. Convertir a tupla.
- [x] `_stamp` sigue llenando solo `queried`. Verificar que el híbrido conserva
  `resolved` del proveedor principal. No copiar misses ni hacer barrido global.
- [x] Probar v10/v11/v12 sin resolved, errores de escritura y recuperación de
  proveedor. Confirmar que la nueva lectura no cambia precisión ni tiempos.
- [x] Ejecutar `python -m pytest -q tests/test_providers.py tests/test_provider_cache.py tests/test_cascade_precision.py`.
- [x] Hacer commit local `feat: persist resolved artist names without merging unsafe cache entries`.

## Tarea 5: Portadas y tarjeta usan los mismos candidatos

**Files:** `src/lyrica/artwork.py`, `src/lyrica/app.py`;
`tests/test_cover_sources.py`, `tests/test_cover_cache.py`,
`tests/test_metadata.py`, `tests/test_card_cache.py`.

**Interfaces:** `identify` y `best_cover_for_candidates` aceptan
`LookupCandidate | tuple`. Añadir `artist_reading=None` como keyword a la cadena
`best_cover`, `fetch_cover`, `_apple_match`, `_closest`, `cached_cover`,
`store_cover`, `_recorded_miss`, `_cover_path`, `_album_path` cuando se transporta
el modo; mantener firmas posicionales actuales. Las ramas Discogs reciben el
texto del candidato, conservando su contrato de servicio actual.

- [x] Añadir pruebas de veto a portada de otro artista con mismo título/álbum;
  alias VEVO válido; nombre confirmado en tarjeta; JSON de match antiguo con
  `_score` alto pero artista incompatible; caché VEVO separada.

```python
def test_album_cannot_override_artist_mismatch():
    records = [{'artistName': 'Queensryche', 'trackName': 'Song',
                'collectionName': 'Album'}]
    assert artwork._closest(records, 'Queen', 'Song', 'Album') is None

def test_card_uses_provider_name_before_query():
    lyr = Lyrics(queried=('BillieEilish', 'CHIHIRO'),
                 resolved=('Billie Eilish', 'CHIHIRO'))
    assert Overlay._resolved_name(Panel(lyr)) == ('Billie Eilish', 'CHIHIRO')
```

- [x] Ejecutar la selección de pruebas de esta tarea y verificar rojo.
- [x] Aplicar veto y puntuación artista mediante `artist_relation` en `_closest`.
  Si es unknown, no sumar artista. Recalcular coincidencia de un JSON cacheado
  contra la consulta actual; un hit incompatible debe tratarse como inválido,
  no como un no-match definitivo: permitir búsqueda de reemplazo.
- [x] Propagar modo VEVO hasta nombres de archivo de matches e imágenes. No
  reutilizar imagen opaca de otro alias; conservar todos los archivos anteriores.
  Mantener los TTL y el manejo de fallos de red existentes.
- [x] Usar `snap.search_candidates()` en ambos workers de app.py. Los métodos
  de pares/triples continúan para compatibilidad, sin usarlos en producción
  donde harían perder la procedencia del artista.
- [x] Implementar orden de nombre del spec. Para respaldo de `queried` sin
  resolved, comparar su lectura con `snap.search_candidates()` en `_card_for`:
  si solo coincide con VEVO/decorated, usar Snapshot normalizado. Conservar la
  prioridad de catálogo y la invalidación existente de la caché de la tarjeta.
- [x] Extender pruebas de portadas a nombres ausentes, colaboraciones existentes,
  fallos de ambos servicios y fallback de miniatura. Las respuestas Discogs no
  se presentan como identidad musical confirmada en `Release`.
- [x] Ejecutar `python -m pytest -q tests/test_cover_sources.py tests/test_cover_cache.py tests/test_metadata.py tests/test_card_cache.py`.
- [x] Hacer commit local `fix: align cover and display artist identity with lyric lookups`.

## Tarea 6: Recorrido completo y cierre

**Files:** crear `tests/test_artist_normalization_flow.py`; actualizar
`docs/song-identity-sessions-cache.md` y el estado de este plan.

- [x] Construir pruebas del recorrido con red simulada y `tmp_path`: Snapshot
  → candidatos completos → proveedor real con respuestas falsas → persistencia
  → tarjeta. Usar helpers existentes de respuestas y Panel; no ventanas Tk.

```python
@pytest.mark.parametrize('artist', ['Queen', 'BTS', 'U2'])
def test_topic_requests_use_clean_artist(artist, monkeypatch):
    calls = []
    def fake_fetch(name, title, duration=0.0, album='', **kwargs):
        calls.append(name)
        return Lyrics(lines=[(1.0, 'line')], synced=True,
                      resolved=(name, title))
    monkeypatch.setattr(providers, 'fetch_lyrics', fake_fetch)
    snap = Snapshot(app='chrome.exe', artist=artist + ' - Topic', title='Song')
    got = providers.fetch_for_candidates(snap.search_candidates())
    assert calls[0] == artist
    assert got.resolved == (artist, 'Song')
```

Este test de frontera complementa, no sustituye, los casos con proveedores
reales y respuestas simuladas de las tareas 3–5.

- [x] Cubrir VEVO sin artista en título, Official/MusicVEVO, etiquetas genéricas
  que solo se consultan tras fallos, artista explícito en título, colaboración,
  Various Artists, Unicode, negativos Queen/Queensryche y nombres legítimos
  con palabras que no deben borrarse. Verificar tope de seis y deduplicación.
- [x] Ejecutar primero la prueba nueva para ver cualquier rojo y completar
  las conexiones de procedencia, nombre resuelto y modo de caché que falle la
  prueba. Cada corrección debe corresponder a una aserción de las tareas 1–5;
  no ampliar alcance.
- [x] Actualizar el contrato: modos de comparación, procedencia, resolved,
  compatibilidad v12 y limitaciones históricas de caché. Enlazar investigación
  y conservar pendientes los hallazgos de títulos/versiones.
- [x] Ejecutar línea base y comprobaciones finales desde la raíz usando el
  intérprete del proyecto. Ejemplo PowerShell si se usa Python disponible:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$env:PYTHONDONTWRITEBYTECODE = '1'
python -B -m pytest -q -p no:cacheprovider
git diff --check
git status --short
```

Si pytest no puede escribir en su directorio temporal por aislamiento, usar
el mecanismo de permisos del entorno; no atribuir ese fallo al código. Las
pruebas aíslan cachés con `tmp_path`. Ejecutar ruff solo sobre archivos tocados
si la herramienta está instalada; corregir errores introducidos y distinguir
deuda previa del repositorio.

- [x] Revisar diff final, imports/ciclos, todas las rutas de caché con contexto,
  preservación de raw_title y las firmas de stubs. No prometer pruebas de
  sesiones reales si no se capturaron: la validación automatizada es offline.
- [x] Hacer commit local `test: verify artist normalization across playback and caches`.
- [x] Informar archivos modificados, pruebas y conteo real, limitaciones y
  pendientes. La implementación termina con estos criterios, sin push/merge.

## Criterios de terminado

- [x] Todos los casos del spec tienen una prueba al nivel del flujo que ejercitan.
- [x] Topic se elimina igual para nombres cortos y largos; Topic artista se conserva.
- [x] Las lecturas VEVO conservan procedencia hasta red, validación y caché.
- [x] Las alternativas genéricas no desplazan una lectura principal ya resuelta.
- [x] Consulta e identidad devuelta permanecen separadas y sobreviven la caché.
- [x] Letra y match Apple rechazan artistas conocidos incompatibles.
- [x] Los misses del canal no bloquean el nombre limpio y no se borran datos viejos.
- [x] Sesiones, offsets, duración, precisión y backing mantienen sus contratos.
- [x] Suite completa y diff revisados; ningún cambio de títulos/versiones fuera del alcance.


## Ejecución completada — 2026-09-14

- Rama local: `fix/artist-normalization`; implementación secuencial por el agente
  principal. La revisión independiente fue de solo lectura.
- Línea base fuera del aislamiento: 1011 pruebas aprobadas, 1 omitida.
- Verificación final: 1113 pruebas aprobadas, 1 omitida (46.49 s).
- Ruff: sin errores en los archivos de código y pruebas modificados.
- Regresiones rojo/verde: Topic corto, procedencia VEVO, nombres parciales,
  resolución de identidad, persistencia, portadas y presentación.
- La revisión detectó y se corrigieron el prefijo Topic repetido dentro del
  título y el peso de coincidencia parcial de colaboradores en NetEase.
- También se preservó el orden posicional de los campos antiguos de Lyrics y
  se actualizó el doble de Snapshot del test de cambio de canción.
- No se cambió la política de versiones/remasters ni se migraron imágenes
  históricas sin metadatos. No se hicieron consultas a catálogos reales.
- Se trabajó en una rama nueva del checkout actual. Los temporales propios de
  las pruebas se retiraron tras verificar sus rutas; los datos del usuario no
  se borraron. El trabajo se conserva en commits locales, sin push ni merge.
