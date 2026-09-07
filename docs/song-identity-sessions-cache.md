# Song Identity, Playback Sessions, and Lyrics Cache Contract

This document defines the runtime contracts introduced for song identity,
Windows playback-session selection, and the lyrics cache. It deliberately does
not change lyric quality ranking, hybrid backing selection, providers,
endpoints, timing algorithms, or UI controls.

## Song identity

Lyrics candidates retain both the cleaned lookup title and the raw player
title. Provider records are accepted only when their available evidence is
compatible with the requested song:

- A known provider artist that contradicts a known requested artist is a hard
  rejection. Missing artist metadata remains unknown, not contradictory.
- Featured and multi-artist credits are compatible when they share a complete
  credited artist; punctuation and case differences are ignored.
- Explicit recording qualifiers such as live, remix, acoustic, instrumental,
  demo, edit, and remaster are compared from raw titles. Contradictory known
  qualifiers are rejected. Missing qualifier evidence remains unknown.
- Title and duration improve ranking only after the contradiction checks. They
  cannot compensate for a different known artist or recording.

Rejection reasons remain internal provider provenance for future diagnostics;
this change adds no diagnostic UI.

## Provider outcomes

Every production provider returns one of four outcomes:

- `hit`: usable lyrics that passed identity validation.
- `no_match`: a valid response established that this provider has no compatible
  result for the query.
- `retryable`: transport, timeout, server, or malformed-response failure.
- `unavailable`: authentication, rate-limit, or an existing provider cooldown
  prevented a lookup. A provider may supply its own retry time.

The cascade still starts providers concurrently, applies the existing
precision/staging ranking, preserves the Richsync/CommunityTTML hybrid policy,
and stops waiting when outstanding work cannot improve the answer. Only
`no_match` exhausts a provider until its miss TTL. Failures and unavailable
states never become permanent misses.

## Cache version 12

Version 12 stores validated lyric payloads separately from per-provider lookup
state. The initial policy is:

- Confirmed no-match TTL: 7 days.
- Retryable failures receive one immediate recovery attempt; repeated failures
  back off from 30 seconds, doubling to at most 30 minutes.
- Unavailable outcomes: the provider's existing retry time when supplied,
  otherwise the same bounded backoff.
- Legacy miss migration batch: one previously ambiguous provider per play.

A refresh begins with the usable cached lyric as the incumbent. A failed,
unavailable, invalid, or lower-quality refresh cannot replace it. A successful
quality upgrade replaces it under the existing ranking rules. Compatible v10
and v11 hits remain usable; unsafe v10 hybrid/inferred timing entries keep the
existing refresh behavior. Legacy misses have no trustworthy outcome time, so
they are revisited lazily, one provider per play, instead of triggering a
library-wide migration.

Payloads are shape-checked before use. Writes serialize to a unique temporary
file in the target directory, flush it, and atomically replace the destination.
An in-process per-path lock prevents local readers and writers from observing a
partial update. The replacement policy is not distributed conflict resolution:
simultaneous writes from different machines remain last-rename-wins.

## Windows playback-session binding

The Windows adapter enumerates all media sessions and maps them to immutable
records before selection. A pure stateful selector keeps the chosen record
while it is playing, keeps it while paused if nothing else is playing, switches
when it disappears, and switches when it pauses while another session plays.
Initial and replacement ties use a deterministic identity rather than
enumeration order.

The binding includes the app id and track metadata; an app id alone is not a
session identity because browsers publish several sessions under one id.
Snapshots carry this binding without changing the existing persistent
`track_key`, which remains the offset key. A separate playback key lets the app
notice a session switch even when two sessions expose the same display track.

Seek and artwork calls receive the snapshot they are acting for. Each call
requests a fresh WinRT manager on its own event loop, enumerates sessions, and
resolves the exact immutable binding. If that session disappeared or changed
track, the operation fails closed instead of falling through to Windows'
current session. No WinRT object crosses a thread or event-loop boundary.

## Integration boundaries

`app.py` changes are limited to passing the relevant snapshot to lyrics,
artwork, and seek work, and to comparing playback bindings when promoting or
advancing a track. Rendering geometry, resize behavior, fades, backing-lane
layout, and their tests are outside this change.
