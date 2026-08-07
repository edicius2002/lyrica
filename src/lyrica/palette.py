"""Colours for each composition mode.

In glass mode the surface adds light, so a level *is* an opacity: drawing a line
at 0x8a rather than 0xff is exactly 54 % against the plate, with no alpha
channel anywhere. In keyed mode colours replace what is behind them, so the
values are real colours and legibility comes from an outline instead.

Levels are precomputed into a ramp at import: the sweep asks for a colour per
character per frame, and a table lookup keeps that off the hot path.
"""
from dataclasses import dataclass

from lyrica.chrome import Chrome, ChromeMode

RAMP_STEPS = 64


def _grey(level: int) -> str:
    v = max(0, min(255, level))
    # A touch warm, so sung text separates from neutral grey without reading as
    # a colour change.
    return f"#{v:02x}{v:02x}{max(0, v - 6):02x}"


def _ramp(low: int, high: int) -> list[str]:
    return [_grey(round(low + (high - low) * i / (RAMP_STEPS - 1)))
            for i in range(RAMP_STEPS)]


def _blend_ramp(low: str, high: str) -> list[str]:
    lo = [int(low[i:i + 2], 16) for i in (1, 3, 5)]
    hi = [int(high[i:i + 2], 16) for i in (1, 3, 5)]
    out = []
    for i in range(RAMP_STEPS):
        f = i / (RAMP_STEPS - 1)
        out.append("#" + "".join(f"{round(a + (b - a) * f):02x}"
                                 for a, b in zip(lo, hi, strict=True)))
    return out


@dataclass(frozen=True)
class Palette:
    header: str
    side: str
    far: str
    unsung: str
    sung: str
    outline: int
    ramp: list[str]
    glow: bool
    additive: bool

    def at(self, fraction: float) -> str:
        """The sweep colour at 0..1 through a character."""
        i = int(max(0.0, min(1.0, fraction)) * (RAMP_STEPS - 1))
        return self.ramp[i]

    def by_distance(self, distance: int) -> str:
        """How bright a line sits, given how far it is from the one being sung.

        The outermost step is what turns entry and exit into a fade: a line
        arrives already dim, brightens as it approaches, and dims again on its
        way out, instead of appearing and vanishing at full strength.
        """
        return self.side if distance <= 1 else self.far

    def faded(self, distance: int, visibility: float) -> str:
        """Brightness for a line, dimmed by how near the edge it has drifted.

        Additive composition makes this a true fade rather than a tint:
        brightness *is* opacity, so scaling a level toward zero scales the line
        toward invisible. That is what stops a line ever being seen cut off by
        the edge of the window — by the time it reaches one, there is nothing
        left of it to clip.

        Replacing composition has no equivalent: dimming a colour there just
        makes it a darker colour, which over a bright background is more
        visible rather than less. So keyed mode keeps its flat step.
        """
        base = self.by_distance(distance)
        if not self.additive:
            return base
        level = int(base[1:3], 16)
        return _grey(round(level * max(0.0, min(1.0, visibility))))


# Glass: additive, so these are brightness levels and double as opacity.
# Unsung sits high because the overlay is glanced at rather than read — a line
# you are about to sing should already be legible.
GLASS = Palette(
    header=_grey(0x50),
    side=_grey(0x62),
    far=_grey(0x30),
    unsung=_grey(0x8A),
    sung=_grey(0xFF),
    outline=0,          # black is invisible here; the tinted plate is the contrast
    ramp=_ramp(0x8A, 0xFF),
    glow=True,   # offset copies genuinely add light rather than faking it
    additive=True,
)

KEYED = Palette(
    header="#c9cfda",
    side="#b6bdc9",
    far="#78808d",
    unsung="#9aa3b2",
    sung="#ffffff",
    outline=2,          # the only thing keeping text legible over a bright video
    ramp=_blend_ramp("#9aa3b2", "#ffffff"),
    glow=False,  # an offset copy would just smear, with nothing to add to
    additive=False,
)

GLOW_LEVELS = [f"#{g:02x}{g:02x}{g:02x}" for g in range(RAMP_STEPS)]


def for_chrome(chrome: Chrome) -> Palette:
    return GLASS if chrome.mode is ChromeMode.GLASS else KEYED
