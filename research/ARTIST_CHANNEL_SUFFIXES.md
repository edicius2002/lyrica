# Sufijos de canales musicales y normalización de artistas

Fecha: 2026-09-12. Investigación previa; no implementa cambios de código.

## Alcance y evidencia

Se contrastaron fuentes oficiales de YouTube y Vevo con el código local. Las
fuentes documentan convenciones y ejemplos, pero no proporcionan porcentajes de
frecuencia. No se capturaron nuevas sesiones de Windows ni se midió el historial
del usuario. Los ejemplos de comportamiento deseado son casos propuestos, no
capturas reales del reproductor.

## Convenciones confirmadas

| Forma | Evidencia | Tratamiento propuesto |
| --- | --- | --- |
| `Artista - Topic` | YouTube documenta este formato para canales autogenerados. | Quitar el sufijo final en la lectura normalizada de un nombre de canal; conservar el original. |
| `Artista - Tema`, `Artista – Thema`, `Artista – Sujet` | La ayuda localizada describe esos nombres en español, alemán y francés. | Considerar una lista explícita de variantes; confirmar su aparición en metadatos de reproducción antes de asumir alcance universal. |
| `ArtistaVEVO` | Vevo documenta nombres de canal con este sufijo. | Derivar un candidato sin `VEVO`; validar el artista restante antes de fijar su nombre visible. |
| `ArtistaOfficialVEVO`, `ArtistaMusicVEVO` | Vevo recomienda estos ejemplos cuando el nombre solicitado ya está ocupado. | Ofrecer alternativas acotadas; quitar solo `VEVO` puede ser insuficiente. |
| `Official`, `Oficial`, `TV`, `Channel`, `Canal`, `YouTube`, `Productions`, `Producciones` | YouTube los enumera como añadidos que recomienda evitar. | Candidatos condicionales cuando son etiquetas separadas; no borrado universal. |

Fuentes de las dos primeras filas: [YouTube en inglés](https://support.google.com/youtube/answer/7636475?hl=en),
[español](https://support.google.com/youtube/answer/7636475?hl=es),
[alemán](https://support.google.com/youtube/answer/7636475?hl=de) y
[francés](https://support.google.com/youtube/answer/7636475?hl=fr).
La ayuda localizada puede contener traducciones automáticas; su texto no prueba
por sí solo qué publica una sesión de Windows.

Fuentes de Vevo: [identificadores y nombres](https://support.vevo.com/hc/en-us/articles/10959580142107-Youtube-Channel-Handles)
y [alternativas para nombres ocupados](https://support.vevo.com/hc/en-us/articles/360033365754-What-if-the-channel-name-I-am-requesting-is-not-available).
Fuentes de añadidos genéricos: [gestión del canal de artista](https://support.google.com/youtube/answer/9048215?hl=en)
y [versión española](https://support.google.com/youtube/answer/9048215?hl=es).

## Por qué VEVO necesita algo más que recortar caracteres

Vevo exige nombres de canal de hasta 20 caracteres, incluido el sufijo, usando
letras A–Z y números. Su documentación también distingue el nombre de canal del
identificador `@handle`, que puede recibir caracteres adicionales. Fuentes:
[creación de canales](https://support.vevo.com/hc/en-us/articles/17860063197723-How-to-Request-Vevo-Channel-in-Backstage)
y [handles](https://support.vevo.com/hc/en-us/articles/10959580142107-Youtube-Channel-Handles).

Consecuencia inferida: eliminar `VEVO` no reconstruye necesariamente espacios,
acentos, puntuación ni un nombre abreviado. `BillieEilishVEVO` produce
`BillieEilish`, no una prueba de que la presentación canónica sea `Billie Eilish`.
Una comparación compacta puede ayudar a verificar un alias de canal concreto;
no debería convertir todos los nombres musicales en cadenas sin espacios.

## Qué significa «eliminar - Topic siempre»

Propuesta: reconocer un sufijo final completo formado por separador y `Topic`,
sin distinguir mayúsculas, y retirarlo de la lectura normalizada del artista.
Normalizar espacios y contemplar una lista explícita de guiones Unicode. Dejar
un nombre no vacío y conservar intactos los metadatos originales.

No es una sustitución de la palabra `Topic` en cualquier posición. Existe un
artista llamado Topic, documentado por [Universal Music](https://www.universal-music.de/topic).
Los casos propuestos deben preservar `Topic` y transformar `Topic - Topic` en
`Topic`. Tampoco se debe eliminar cualquier fragmento posterior a un guion.

`Various Artists - Topic` merece una distinción adicional: recortar el sufijo
no descubre quién interpreta la canción. YouTube exige al menos un artista
principal por pista y reserva `Various Artists` para ciertos lanzamientos de
varios artistas. [Metadatos de artistas y colaboradores](https://support.google.com/youtube/answer/12029076?hl=en-GB).
Por tanto, no tratar ese nombre colectivo como identidad individual confirmada.

## Restricciones observadas en Lyrica

- `src/lyrica/sessions/base.py`, `Snapshot`: conserva aplicación y metadatos,
  pero no URL, dominio ni identificador de canal. `is_browser` detecta el
  navegador, no YouTube. Una política limitada a YouTube no puede verificarse
  solamente con ese indicador.
- `norm_artist_title` y `lyrics_candidates`: recortan espacios del artista;
  no tienen reglas para `Topic` o `VEVO`. La alternativa útil depende a menudo
  de que el título contenga `Artista - Canción`.
- `src/lyrica/providers/identity.py`, `artists_compatible`: permite fragmentos
  coincidentes de cuatro caracteres o más. Es una regla distinta de reconocer
  sufijos y explica diferencias entre nombres largos y cortos.
- `src/lyrica/providers/__init__.py`, `_stamp` y `_cache_path`: conservan la
  consulta exitosa y generan claves a partir del texto de consulta. Añadir una
  limpieza debe coordinarse con presentación y compatibilidad de caché.

Estas observaciones proceden del código local, no de las fuentes web. La
investigación previa de sesiones está en `research/BROWSER_SESSIONS.md`; sus
ejemplos no constituyen una medición de frecuencia de estos sufijos.

## Política general recomendada para una futura implementación

1. Normalizar espacios exteriores e interiores; reconocer sufijos antes de
   perder los separadores por eliminación de puntuación.
2. Aplicar una regla precisa al sufijo final `- Topic`; mantener el nombre
   original y evitar usar el navegador como prueba de origen YouTube.
3. Generar pocos candidatos para `VEVO` y sus combinaciones documentadas;
   confirmar la identidad con título, crédito y evidencia del catálogo.
4. Tratar `Official`/`Oficial` y demás palabras genéricas como alternativas,
   sin quitarlas dentro de nombres ni encadenar eliminaciones sin límite.
5. No añadir una lista universal para `Music`, `Records`, `TV`, `DJ`, `MC`,
   `Band`, `Live` o `Remix`: pueden tener significado musical o identificar un
   publicador distinto del intérprete. `Music` aparece documentado como añadido
   en combinaciones VEVO, pero eso no justifica borrarlo en todos los artistas.
6. Unificar el criterio de equivalencia de letras y portadas; conservar por
   separado identidad original de reproducción, consulta y nombre confirmado.

## Casos de aceptación propuestos

| Entrada | Expectativa |
| --- | --- |
| `Queen - Topic`, `BTS - Topic`, `U2 - Topic` | Mismo tratamiento del sufijo, independiente de la longitud del nombre. |
| `Topic - Topic` / `Topic` | Ambos preservan el artista `Topic`. |
| `Artist – Topic`, espacios repetidos, distinta capitalización | Variantes de formato cubiertas explícitamente. |
| `BillieEilishVEVO` | Candidato sin sufijo; resolver el nombre con evidencia, sin adivinar espacios. |
| `ArtistOfficialVEVO`, `ArtistMusicVEVO` | Candidatos acotados que cubran los añadidos documentados. |
| `Artist - Tema` | Variante localizada, diferenciando prueba sintética de captura real. |
| `Queen` / `Queensryche` | No equivalentes por compartir un fragmento. |
| `Various Artists - Topic` | Limpieza del canal no equivale a identificar al intérprete. |
| Nombres originales con `Music`, `TV` o guiones internos | Conservarlos si no existe evidencia de que sean añadidos del canal. |

No se implementaron reglas ni se modificaron pruebas o cachés en esta investigación.
