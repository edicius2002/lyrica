# Normalización de artistas y nombres de canales

Estado: implementado y verificado el 2026-09-14; consultar el registro del plan.
Fecha: 2026-09-12.
Investigación: [ARTIST_CHANNEL_SUFFIXES.md](../../../research/ARTIST_CHANNEL_SUFFIXES.md).

## Resultado esperado

Una canción recibida como `BTS - Topic / Song` se busca como `BTS / Song`, con
el mismo criterio que `Queen - Topic`. Un canal VEVO puede resolverse al nombre
musical aunque su identificador no tenga espacios. Letras, portada y tarjeta
utilizan la misma política de comparación de artistas y preservan la procedencia
de las transformaciones. La consulta realizada sigue siendo distinta del nombre
que devuelve y confirma un catálogo.

## Alternativas consideradas

1. Quitar sufijos únicamente en Snapshot: pequeño cambio, pero no resuelve los
   rechazos por espacios, la comparación por fragmentos ni la presentación.
2. Política compartida con candidatos y procedencia: opción elegida. Limita
   las reglas permisivas a candidatos derivados de canales y conserva interfaces
   de compatibilidad para los consumidores actuales.
3. Identificador musical universal y migración completa de la biblioteca:
   requiere fuentes e identificadores ausentes; excede el problema investigado.

## Restricciones globales

- Python >=3.11; sin dependencias nuevas.
- Conservar `Snapshot.artist`, `Snapshot.title`, `track_key()` y `playback_key()`.
- Conservar los offsets por reproducción y los enlaces de sesión de Windows.
- No cambiar el ranking de precisión, las voces, el backing ni sus tiempos.
- No borrar cachés, tokens ni ajustes del usuario.
- No acceder a servicios reales durante las pruebas automatizadas.
- No activar aceleración de hardware ni iniciar una interfaz para este trabajo.
- Ejecución directa por el agente principal, en tareas secuenciales.

## Reglas decididas

### Topic

Retirar una sola etiqueta final completa ` - Topic` de la lectura normalizada,
independientemente del reproductor; es una heurística explícita por formato,
no una afirmación de que el origen esté verificado como YouTube. Reconocer
capitalización indiferente, espacios Unicode y separadores `-`, `‐`, `‑`, `–`,
`—`. Exigir espacios a ambos lados del separador, después de normalizarlos.
Nunca dejar el nombre vacío ni eliminar una palabra interna.

`Topic - Topic` produce `Topic`; `Topic`, `Hot Topic` y `Artist-Topic` se
conservan. `Artist-Topic` se mantiene por no cumplir el formato documentado.
No eliminar sucesivamente etiquetas: la función debe ser idempotente para los
casos admitidos; si al retirar una etiqueta el resultado aún tiene el mismo
formato, conservar esa entrada ambigua y registrarla como caso no resuelto.

La primera entrega activa solo `Topic`. `Tema`, `Thema`, `Sujet` y `トピック`
quedan documentados para incorporación posterior con capturas reales de sesión:
las traducciones de la ayuda no prueban que Windows publique esas cadenas.

### VEVO y añadidos generales

Cuando Snapshot identifica un navegador, generar lecturas de canal; el nombre
del navegador no prueba un dominio. Para `ArtistVEVO`, `Artist VEVO` y
`Artist - VEVO`, retirar una terminación VEVO completa y su separador opcional.
El cuerpo alfanumérico pegado se admite por la convención documentada; exigir
cuerpo no vacío y no tratar `VEVO` aislado como artista desconocido.

Para `ArtistOfficialVEVO` y `ArtistMusicVEVO`, producir primero el cuerpo
`ArtistOfficial`/`ArtistMusic` y una alternativa adicional `Artist`. No seguir
recortando prefijos, números ni palabras arbitrarias. Todas estas lecturas
llevan procedencia `vevo`: solo ellas permiten igualdad del nombre completo
ignorando espacios después del plegado de acentos y puntuación.

Para etiquetas separadas al final `Official`, `Oficial`, `TV`, `Channel`,
`Canal`, `YouTube`, `Productions`, `Producciones`, retirar una sola etiqueta
como alternativa de menor confianza. Admitir palabra final, ` - etiqueta`,
`(etiqueta)` y `[etiqueta]`. No admitir palabras pegadas fuera de las
combinaciones VEVO anteriores. No borrar `Music`, `Records`, `DJ`, `MC`,
`Band`, `Live` o `Remix` de artistas arbitrarios.

La presentación inicial solo aplica espacios y Topic. Las alternativas VEVO o
genéricas no se convierten automáticamente en el nombre visible. Se muestran
cuando un proveedor devuelve un nombre completo compatible.

### Candidatos y presupuesto

Crear una representación de candidato que lleve artista consultado, artista
original, regla aplicada, título consultado y título original de esa lectura.
No confundir la interpretación extraída del título con el nombre del canal.

Orden:

1. Lectura principal con espacios/Topic corregidos y prefijo de artista quitado.
2. Lectura explícita `Artista - Título` del título del navegador, si es distinta.
3. Alternativas VEVO, cuando hay una terminación reconocida.
4. Lecturas originales de compatibilidad que no dupliquen las anteriores.
5. Alternativas de etiquetas genéricas, solo si los grupos anteriores no
   produjeron letras utilizables o una portada/nombre según el consumidor.

Mantener como máximo las tres lecturas estructurales actuales y tres lecturas
adicionales de sufijos: seis candidatos por Snapshot. Deduplicar por artista,
título y modo de comparación; conservar evidencia de versión del título.
El nombre específico extraído del título prevalece sobre un añadido genérico.

## Comparación compartida

Crear un módulo puro `artist_names.py`. Devolver una relación explícita:
`unknown`, `exact`, `credit`, `channel_alias` o `mismatch`.

- Plegar acentos, puntuación, mayúsculas y espacios repetidos para comparar.
- Igualdad completa de nombres o de un crédito completo según la separación
  de colaboraciones actualmente soportada; retirar la coincidencia por
  subcadenas. `Queen` y `Queensryche` deben ser incompatibles.
- Solo procedencia VEVO autoriza igualdad compacta del nombre completo.
  `ArtistReading('BillieEilish', ..., 'vevo')` puede coincidir con
  `Billie Eilish`; el texto arbitrario `BillieEilish` no gana esa regla.
- Metadatos de artista ausentes siguen siendo desconocidos, sin bonificación
  de artista ni capacidad por sí solos para confirmar un nombre visible.
- `Various Artists` y `Varios Artistas`, después de retirar Topic, se tratan
  como crédito colectivo desconocido. El nombre específico del título o del
  catálogo es necesario para presentar un intérprete resuelto.

Esta entrega conserva la política existente de colaboradores compartidos.
Resolver completamente grupos cuyos nombres contienen `&`, comas o palabras
de enlace necesita créditos estructurados y es un trabajo separado; no se
afirmará que esta entrega resuelve esa ambigüedad.

La puntuación de cada proveedor conserva sus pesos de título/duración y sus
umbrales. El componente artista usa la relación compartida: igualdad completa
mantiene su peso de igualdad; crédito/alias usa el peso previo de compatibilidad;
desconocido no suma; incompatibilidad impide aceptar el resultado.

## Identidad confirmada y presentación

Añadir a Lyrics un par opcional `resolved`, con el artista y título devueltos
por el proveedor después de validar identidad. `queried` conserva la consulta.
El campo `source` existente identifica la procedencia de las letras y de su
nombre confirmado. Los proveedores solo llenan `resolved` con ambos campos
presentes y un artista no colectivo; no inventar valores ausentes.

La tarjeta prioriza: catálogo de portada compatible, `Lyrics.resolved`, consulta
exitosa segura, Snapshot normalizado. Una consulta compacta VEVO o un añadido
genérico sin confirmación no debe convertirse en el nombre oficial visible.
El híbrido conserva `resolved` de las letras principales, no del backing.

Apple aplica el mismo veto de artista antes de puntuar; título y álbum no pueden
compensar un artista conocido distinto. Volver a validar los JSON de matches
almacenados al leerlos, en lugar de confiar en un `_score` antiguo.
Discogs conserva sus controles propios: su respuesta de búsqueda no ofrece
el mismo campo de artista estructurado; no se le atribuirá una validación que
su respuesta no permite. La alternativa de miniatura del reproductor permanece.

## Caché y compatibilidad

Mantener el formato v12 y añadir `resolved` como campo opcional validado. Los
hits v10/v11/v12 sin ese campo continúan legibles, con presentación de respaldo.
No modificar firmas posicionales existentes: añadir contexto opcional por
keyword y adaptadores para los pares/triples de candidatos anteriores.

Las consultas Topic nuevas se hacen directamente con el nombre limpio, por lo
que reutilizan la entrada limpia existente y un miss bajo `- Topic` no bloquea
esa consulta. No copiar estados negativos entre variantes. Conservar archivos
antiguos: esta entrega no intenta demostrar identidad de hits históricos que
solo almacenan `queried` y carecen de metadatos del registro devuelto.

Los candidatos VEVO requieren un discriminador `artist-mode:vevo` en clave e
identidad de caché: el resultado aceptado por igualdad compacta no debe aparecer
como hit de una consulta ordinaria con el mismo texto. Las consultas ordinarias
conservan sus claves actuales. Aplicar el discriminador a letras, matches y
portadas de candidatos VEVO. Un miss previo sin ese modo no bloquea el nuevo.

No migrar imágenes opacas de un nombre de canal a otro artista ni afirmar que
se han revalidado: los bytes antiguos no contienen crédito verificable. La
revalidación de matches afecta a los JSON con metadatos; no sanea por sí sola
todas las imágenes históricas ya descargadas.

## Fuera de esta entrega

Correcciones de `Birthday Mix`, reglas de `Letra`, separación general de
guiones de títulos, años de remaster, sedes de conciertos e identidad universal
de grabación. Se preservan como hallazgos del diagnóstico y no se eliminan de
la lista de trabajo por completar este plan.

## Aceptación

Las pruebas deben cubrir entrada de Snapshot, solicitudes simuladas a los
cuatro proveedores, selección de portadas, caché y nombre visible. Se exige
que nombres cortos y largos reciban el mismo tratamiento de Topic; VEVO solo
habilite su comparación con procedencia; Queen/Queensryche se rechace; la
consulta limpia no herede misses del canal; los offsets y las claves de sesión
permanezcan byte por byte iguales; y se respeten seis candidatos como máximo.
