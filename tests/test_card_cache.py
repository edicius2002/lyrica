"""The card's short-circuit, and the rule that broke it once (offline).

The card is only re-derived when its inputs change, because measuring text is a
round trip into Tk and this runs on every tick. That optimisation has one
hazard, and it bit: the key and the cached value must be invalidated *together*.
Clearing the value alone leaves the key matching, so the short-circuit hands
back the cleared value — which blanked the title and artist the moment a cover
arrived.
"""


class Card:
    """The caching rule, isolated from Tk."""

    def __init__(self):
        self.key = None
        self.value = None
        self.derivations = 0

    def derive(self, raw: tuple) -> tuple[str, str]:
        if raw == self.key:
            return self.value or ("", "")
        self.key = raw
        self.derivations += 1
        return (f"title:{raw[0]}", f"artist:{raw[1]}")

    def tick(self, raw: tuple) -> tuple[str, str]:
        result = self.derive(raw)
        self.value = result
        return result

    def invalidate_inputs(self):
        self.key = None

    def invalidate_value(self):
        self.value = None


def test_unchanged_inputs_are_not_re_derived():
    card = Card()
    for _ in range(5):
        card.tick(("A", "B"))
    assert card.derivations == 1, "measuring text on every tick is the cost being avoided"


def test_changed_inputs_are_re_derived():
    card = Card()
    card.tick(("A", "B"))
    card.tick(("C", "D"))
    assert card.derivations == 2
    assert card.tick(("C", "D")) == ("title:C", "artist:D")


def test_invalidating_the_inputs_recovers_the_same_text():
    # How a cover arriving should force a relayout: drop the key, keep nothing
    # stale, get the same answer back.
    card = Card()
    first = card.tick(("A", "B"))
    card.invalidate_inputs()
    assert card.tick(("A", "B")) == first
    assert card.derivations == 2


def test_invalidating_only_the_value_would_blank_the_card():
    # The bug, pinned. With the key still matching, the short-circuit returns
    # the cleared value — and empty strings are what reached the screen.
    card = Card()
    card.tick(("A", "B"))
    card.invalidate_value()
    assert card.derive(("A", "B")) == ("", ""), "this is why the value is never cleared alone"


def test_the_two_are_only_ever_invalidated_together():
    # The rule the fix encodes: invalidate the inputs, and the value follows.
    card = Card()
    card.tick(("A", "B"))
    card.invalidate_inputs()
    assert card.tick(("A", "B")) != ("", "")
