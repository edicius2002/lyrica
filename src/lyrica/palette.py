"""Colours for each composition mode, derived from the cover where there is one.

In glass mode the surface adds light, so a level behaves like an opacity:
drawing at 0x8a rather than 0xff reads as roughly half strength, with no alpha
channel anywhere. In keyed mode colours replace what is behind them, so the
values are real colours and legibility comes from an outline instead.

**The mistake to avoid first**, because it is the obvious design and it does not
work: you cannot hold both the largest channel and the luminance. Fully
saturated red at 255 carries 21 % of white's luminance, because green supplies
0.7152 of it and red only 0.2126. Tinting every role at a fixed level therefore
dims the text by a different amount for every hue, and a blue song comes out
illegible while a yellow one comes out unchanged.

So a role is specified by two numbers that *are* stable across hues — how much
light it puts out and how much colour it carries — and the level is solved for:

    target luminance = luminance(the grey this role used to be) x RETAIN[role]
    target chroma    = CHROMA[role] x the song's strength
    largest channel <= 138, except for the sung word

138 is the measured clamp ceiling (see `glass`). At or below it a colour reaches
the screen with its chroma intact on every desktop; above it the chroma lost is
`level - 138`, linearly. The sung word is the only role allowed past it, because
it is the one role whose job is light rather than colour.

The third constraint is the one a greyscale palette silently fails. Over a
bright desktop the plate adds ~117 to everything, so the sung word at 255 and
the unsung line at 138 both clamp to 255 and the sweep disappears — measured
dE 3.4, against 34.5 over a dark desktop. Chroma is what survives the clamp, so
a coloured song keeps its sweep for free; a neutral one has nothing to keep it
with and must buy the separation back in level. That is the only place this
design spends brightness, and it spends it only where it must.
"""

import logging
from dataclasses import dataclass, field, replace

from lyrica.chrome import Chrome, ChromeMode
from lyrica.glass import (
    HEADROOM,
    delta_e,
    hex_of,
    luminance,
    plate,
    rgb_of,
    screen,
    tint,
)
from lyrica.songcolour import NEUTRAL, SongColour

logger = logging.getLogger(__name__)

RAMP_STEPS = 64

# The greyscale levels the palette used before colour. Luminance anchors now,
# not outputs — what a role keeps of them is RETAIN's business.
ANCHOR = {"sung": 255, "unsung": 138, "side": 98, "far": 48,
          "title": 138, "artist": 84, "sheen": 44}

# How much of that anchor's luminance each role keeps once coloured. Below 1.0
# is the price of colour, charged where it costs least to pay.
RETAIN = {"sung": 0.98, "unsung": 0.78, "side": 0.72, "far": 0.60,
          "title": 0.78, "artist": 0.66, "sheen": 0.60}

# The chroma each role aims at, at full song strength — screen units, largest
# channel minus smallest, which is exactly what reaches the glass while the
# level stays under the ceiling.
CHROMA = {"sung": 8, "unsung": 46, "side": 58, "far": 34,
          "title": 44, "artist": 56, "sheen": 26}

# A role may not fall below this fraction of its anchor luminance to buy colour.
FLOOR = 0.55

CHROMA_CEILING = HEADROOM        # 138
SUNG_CEILING = 255

# The sweep has to survive the worst desktop there is.
WORST_DESKTOP = (245, 245, 245)
SWEEP_DE = 13.0                  # between the sung word and the unsung tail
MIN_UNSUNG = 92                  # below this the line reads as dim, not as unsung

# How much of the unsung state's compression the rest of the ladder shares. Not
# all of it: the sweep rule is about one pair of colours, and passing the whole
# cost on would dim a neutral track's neighbours by 26 % to fix something only
# the active line has. The square root keeps the step between the active line
# and its neighbours while charging them 14 % instead.
LADDER_COMP_EXP = 0.5

# How much of the cover has to carry colour before the palette trusts the hue,
# and where that trust reaches full. Ramped rather than gated, so a borderline
# cover drifts toward grey instead of flipping between two palettes between
# plays.
CONF_LO, CONF_HI = 0.06, 0.35

ROLES = ("sung", "unsung", "side", "far", "title", "artist", "sheen")


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else min(v, hi)


def _grey(level: float) -> tuple:
    v = _clamp(round(level), 0, 255)
    # A touch warm, so text separates from neutral grey without reading as a
    # colour change.
    return (v, v, max(0, v - 6))


def _blend_ramp(low: str, high: str) -> list[str]:
    lo, hi = rgb_of(low), rgb_of(high)
    return [hex_of(tuple(a + (b - a) * i / (RAMP_STEPS - 1)
                         for a, b in zip(lo, hi, strict=True)))
            for i in range(RAMP_STEPS)]


# ------------------------------------------------------------------ solving

def solve(hue: float, target_lum: float, target_chroma: float,
          ceiling: float) -> tuple:
    """The dimmest colour reaching `target_lum` while carrying the chroma.

    Chroma is given up before luminance: a role that cannot be both bright
    enough and colourful enough at this hue comes out paler, never darker. That
    ordering is the whole legibility policy in one line.
    """
    c = float(target_chroma)
    for _ in range(12):
        lo, hi = max(1.0, c), float(ceiling)
        if luminance(tint(hi, hue, min(1.0, c / hi))) < target_lum:
            c *= 0.85          # unreachable at any legal level: less colour
            continue
        for _ in range(20):
            mid = (lo + hi) / 2
            if luminance(tint(mid, hue, min(1.0, c / mid))) >= target_lum:
                hi = mid
            else:
                lo = mid
        return tint(hi, hue, min(1.0, c / hi))
    return tint(float(ceiling), hue, 0.0)


def strength_of(song: SongColour) -> float:
    """How strongly this cover's colour should show, 0..1.

    Two factors, both needed. Saturation says how colourful the winning swatch
    is; weight says how much of the cover agrees with it. A vivid detail on a
    grey sleeve is not the song's colour, and neither is half a cover of beige.
    """
    if song.neutral:
        return 0.0
    conf = _clamp((song.weight - CONF_LO) / (CONF_HI - CONF_LO), 0.0, 1.0)
    return _clamp((0.30 + 0.70 * song.sat) * conf ** 0.5, 0.0, 1.0)


def _sweep_limited(sung: tuple, level_of, sweep_de: float) -> tuple:
    """The brightest colour that still reads as different from `sung`.

    Solved downward from the ceiling rather than up from a luminance target,
    because the latter is *non-monotone*: a brighter target raises the level
    until the sweep rule pulls it back further than it gained. Measured on one
    track, aiming for 86 % of the grey's luminance landed dimmer than aiming
    for 70 %.
    """
    p = plate(WORST_DESKTOP)
    hi = float(CHROMA_CEILING)
    if delta_e(screen(p, sung), screen(p, level_of(hi))) >= sweep_de:
        return level_of(hi)
    lo = float(MIN_UNSUNG)
    for _ in range(20):
        mid = (lo + hi) / 2
        if delta_e(screen(p, sung), screen(p, level_of(mid))) >= sweep_de:
            lo = mid
        else:
            hi = mid
    return level_of(lo)


def _derive_neutral(sweep_de: float) -> dict:
    """The greyscale ladder, compressed by the same sweep rule as the rest."""
    sung = _grey(255)
    unsung = _sweep_limited(sung, _grey, sweep_de)
    comp = luminance(unsung) / (luminance(_grey(CHROMA_CEILING))
                                * RETAIN["unsung"])
    out = {"sung": sung, "unsung": unsung}
    for role in ROLES:
        if role in out:
            continue
        want = luminance((ANCHOR[role],) * 3) * RETAIN[role] * comp ** LADDER_COMP_EXP
        lo, hi = 0.0, 255.0
        for _ in range(20):
            mid = (lo + hi) / 2
            if luminance(_grey(mid)) >= want:
                hi = mid
            else:
                lo = mid
        out[role] = _grey(hi)
    p = plate(WORST_DESKTOP)
    return out | {"_meta": (0.0, 0.0, comp,
                            delta_e(screen(p, sung), screen(p, unsung)))}


def _derive_coloured(hue: float, strength: float, sweep_de: float) -> dict:
    sung = solve(hue, luminance((255,) * 3) * RETAIN["sung"],
                 CHROMA["sung"] * strength, SUNG_CEILING)

    # The unsung state is not derived from a luminance target; it takes as much
    # as the two hard constraints leave. Brightness only helps it, and only the
    # sweep rule limits it, so the answer is the highest level that still reads
    # as a different colour from the sung word over the worst desktop there is.
    got_c = CHROMA["unsung"] * strength
    floor_l = luminance((ANCHOR["unsung"],) * 3) * FLOOR
    unsung = None
    for _ in range(12):
        def level_of(lv, c=got_c):
            return tint(lv, hue, min(1.0, c / lv))
        unsung = _sweep_limited(sung, level_of, sweep_de)
        if luminance(unsung) >= floor_l or got_c < 4:
            break
        got_c *= 0.85       # too dark to be this colourful: give up chroma first

    # Everything else follows the unsung state in luminance, so the ladder keeps
    # the shape the greyscale palette gave it whatever the sweep cost.
    comp = luminance(unsung) / (luminance((ANCHOR["unsung"],) * 3)
                                * RETAIN["unsung"])
    out = {"sung": sung, "unsung": unsung}
    for role in ROLES:
        if role in out:
            continue
        shared = comp ** LADDER_COMP_EXP
        want = luminance((ANCHOR[role],) * 3) * RETAIN[role] * shared
        floor = luminance((ANCHOR[role],) * 3) * FLOOR * shared
        out[role] = solve(hue, max(want, floor), CHROMA[role] * strength,
                          CHROMA_CEILING)
    p = plate(WORST_DESKTOP)
    return out | {"_meta": (hue, strength, comp,
                            delta_e(screen(p, sung), screen(p, unsung)))}


# ------------------------------------------------------------------ palette

@dataclass(frozen=True)
class Palette:
    sung: str
    unsung: str
    side: str
    far: str
    title: str
    artist: str
    sheen: str
    outline: int
    ramp: list[str]
    glow: bool
    additive: bool
    # What a fading line dissolves into. The backdrop image covers the whole
    # window, so this is the artwork wash behind the lyrics, not black.
    backdrop: tuple = (0, 0, 0)
    hue: float = 0.0
    strength: float = 0.0
    sweep_de: float = 0.0
    compression: float = 1.0
    _fades: dict = field(default_factory=dict, repr=False, compare=False)

    def at(self, fraction: float) -> str:
        """The sweep colour at 0..1 through a character."""
        return self.ramp[int(_clamp(fraction, 0.0, 1.0) * (RAMP_STEPS - 1))]

    def by_distance(self, distance: int) -> str:
        """How bright a line sits, given how far it is from the one being sung.

        The outermost step is what turns entry and exit into a fade: a line
        arrives already dim, brightens as it approaches, and dims again on its
        way out, instead of appearing and vanishing at full strength.
        """
        return self.side if distance <= 1 else self.far

    def faded(self, distance: int, visibility: float) -> str:
        """Brightness for a line, dimmed by how near the edge it has drifted.

        The fade aims at the backdrop, not at black or at any fixed floor.
        Fading toward black is wrong wherever the backdrop is not black — and it
        never is, because the cover wash covers the whole window. A glyph drawn
        at #000000 there does not disappear; it punches a dark hole in the wash
        and reads as a silhouette, which is *more* visible than the line it was
        trying to hide. What actually vanishes is a glyph the colour of what is
        behind it.

        Replacing composition has no equivalent — dimming a colour there just
        makes a darker colour, which over a bright background is more visible
        rather than less — so keyed mode keeps its flat step.
        """
        base = self.by_distance(distance)
        if not self.additive:
            return base
        # Quantised and memoised: this is asked per line per frame, and the eye
        # cannot see a step of 1/32 of a fade anyway.
        step = int(_clamp(visibility, 0.0, 1.0) * 32)
        key = (distance <= 1, step)
        hit = self._fades.get(key)
        if hit is None:
            v = step / 32
            hit = hex_of(tuple(b + (a - b) * v
                               for a, b in zip(rgb_of(base), self.backdrop,
                                               strict=True)))
            self._fades[key] = hit
        return hit


def _assemble(colours: dict, *, backdrop: tuple, additive: bool) -> Palette:
    hue, strength, comp, de = colours["_meta"]
    roles = {r: hex_of(colours[r]) for r in ROLES}
    return Palette(
        **roles,
        outline=0,      # black is invisible here; the tinted plate is the contrast
        ramp=_blend_ramp(roles["unsung"], roles["sung"]),
        glow=True,      # offset copies genuinely add light rather than faking it
        additive=additive,
        backdrop=backdrop,
        hue=hue,
        strength=strength,
        sweep_de=de,
        compression=comp,
    )


GLASS = _assemble(_derive_neutral(SWEEP_DE), backdrop=(0, 0, 0), additive=True)

KEYED = Palette(
    sung="#ffffff",
    unsung="#9aa3b2",
    side="#b6bdc9",
    far="#78808d",
    title="#e8ecf2",
    artist="#c9cfda",
    sheen="#78808d",
    outline=2,          # the only thing keeping text legible over a bright video
    ramp=_blend_ramp("#9aa3b2", "#ffffff"),
    glow=False,         # an offset copy would just smear, with nothing to add to
    additive=False,
)

GLOW_LEVELS = [f"#{g:02x}{g:02x}{g:02x}" for g in range(RAMP_STEPS)]


def for_chrome(chrome: Chrome) -> Palette:
    """The palette to start with, before any cover has been seen."""
    return GLASS if chrome.mode is ChromeMode.GLASS else KEYED


def for_song(chrome: Chrome, song: SongColour | None,
             backdrop: tuple = (0, 0, 0)) -> Palette:
    """The palette for one cover.

    Keyed mode ignores the cover: its colours have to stay legible over whatever
    video is behind the window, which the artwork says nothing about.
    """
    if chrome.mode is not ChromeMode.GLASS:
        return KEYED
    song = song or NEUTRAL
    strength = strength_of(song)
    if strength <= 0:
        colours = _derive_neutral(SWEEP_DE)
    else:
        colours = _derive_coloured(song.hue, strength, SWEEP_DE)
    return _assemble(colours, backdrop=tuple(backdrop), additive=True)


def rebacked(pal: Palette, backdrop: tuple) -> Palette:
    """The same palette, fading into a different backdrop."""
    backdrop = tuple(backdrop)
    if backdrop == pal.backdrop:
        return pal
    return replace(pal, backdrop=backdrop, _fades={})
