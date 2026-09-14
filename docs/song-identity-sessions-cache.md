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

Recognized version suffixes separated by a spaced dash are normalized like
bracketed labels: `Song - 2011 Remaster` and `Song (Remastered 2011)` do not
contradict each other. Version words inside the main title remain significant.

Rejection reasons remain internal provider provenance for future diagnostics;
this change adds no diagnostic UI.

## Artist channel names

`artist_names.py` supplies one comparison policy for lyrics providers and
Apple catalogue matches. A final spaced `- Topic` label is removed from the
lookup/display reading regardless of artist length; the name `Topic` itself
survives. Whitespace and common Unicode dash variants are handled. Repeated
ambiguous Topic suffixes remain untouched. Localized labels such as `Tema`
are deferred until verified in captured playback metadata.

Browser snapshots offer additional, bounded channel readings for VEVO and its
documented Official/Music combinations. Those readings retain provenance;
only VEVO readings may compare complete names ignoring spaces. Arbitrary
compact artist strings do not inherit that exception. Separate trailing
Official/Oficial/TV/Channel/Canal/YouTube/Productions/Producciones labels are
last-resort alternatives. They do not replace an already usable lyric reading.
Snapshots produce at most six candidates, retaining the raw title for each.
Browser detection is not proof that the source site is YouTube.

Artist comparison accepts complete names and shared complete credited artists,
not name substrings: Queen and Queensryche are incompatible. Various Artists
and Varios Artistas are unknown collective credits, not identified performers.
The existing ambiguity between collaborations and group names containing
commas or joining words remains. NetEase retains the weaker scoring weight
for matching only one member of a collaboration.

`Snapshot.search_candidates()` carries the original artist, transformation,
lookup title and raw title through both workers. Pair/triple projections remain
available for older callers. Raw Snapshot data, session bindings, playback keys
and saved offsets do not change. A repeated channel prefix is removed before
its artist suffix is normalized, so `BTS - Topic - Song` retains `Song`.

Validated providers put their complete returned credit in `Lyrics.resolved`;
`queried` still records the successful query. Card naming prioritizes the
Apple catalogue name, then the provider name, then a safe successful query,
then normalized player metadata. Unconfirmed speculative aliases are not
presented as official names. Hybrid lyrics retain the main provider's name.

Cache v12 adds `resolved` as an optional validated pair; older entries remain
readable. VEVO matching uses a separate `artist-mode:vevo` key namespace and
identity marker, so an alias hit cannot satisfy an ordinary compact-name
query. Ordinary keys are unchanged. Topic queries reuse existing clean-name
entries, while old channel misses cannot block the new query. No old entries
are deleted or globally migrated.

Cached Apple match JSON is revalidated rather than trusting an old score.
Title and album cannot compensate for a known incompatible artist. Discogs
retains its own search contract, and old opaque cover-image bytes cannot be
revalidated from their contents or migrated as proof of artist identity.

Title noise rules, remaster years and concert/version identity are separate
work; this change does not claim to resolve those earlier findings. See the
[suffix research](../research/ARTIST_CHANNEL_SUFFIXES.md) and
[implementation plan](superpowers/plans/2026-09-12-artist-normalization.md).

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

Providers still pending at the overall cascade deadline receive a retryable
timeout state even if their background request has not returned. The shorter
hybrid grace and successful early exits do not imply provider failures.

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
notice distinguishable session switches. These bindings are metadata evidence,
not globally unique physical tab identifiers. For display continuity, unchanged
bindings are matched first; a single removed and added binding within the same
app is treated as a track update. Multiple simultaneous changes cannot prove
continuity and fall back to deterministic selection. No COM object is retained
for this matching.

Duplicate bindings prefer a playing reading for display, but mark the snapshot
ambiguous. Such a snapshot cannot authorize seek or artwork, even if a duplicate
later disappears. An initially unique snapshot also fails closed if its binding
has multiple matches when resolving an action. Indistinguishable tabs cannot
be reliably tracked individually from these metadata fields.

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
