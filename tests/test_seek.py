"""Click-to-seek: telling a click from a drag, and asking rather than assuming."""
from lyrica.sessions import NullSessionReader
from lyrica.sessions.base import SessionReader


class Recorder(SessionReader):
    """A reader that accepts or refuses a jump, and remembers being asked."""

    def __init__(self, accepts: bool = True):
        super().__init__()
        self.accepts = accepts
        self.asked: list[float] = []

    def _run(self):
        pass

    def seek(self, seconds: float) -> bool:
        self.asked.append(seconds)
        return self.accepts


def moved(press: tuple, now: tuple, slack: int = 4) -> bool:
    """The rule the overlay applies to tell a drag from a click."""
    return abs(now[0] - press[0]) > slack or abs(now[1] - press[1]) > slack


# --- click versus drag ------------------------------------------------------

def test_a_still_hand_is_a_click():
    assert not moved((100, 100), (100, 100))


def test_a_slightly_shaky_hand_is_still_a_click():
    # A hand never holds perfectly still. Without the slack, seeking would
    # almost never fire.
    assert not moved((100, 100), (103, 98))


def test_real_travel_is_a_drag():
    assert moved((100, 100), (140, 100))
    assert moved((100, 100), (100, 140))


def test_the_slack_is_symmetric():
    assert moved((100, 100), (94, 100))
    assert moved((100, 100), (100, 94))


# --- asking rather than assuming -------------------------------------------

def test_a_reader_reports_whether_the_jump_was_taken():
    accepting, refusing = Recorder(accepts=True), Recorder(accepts=False)
    assert accepting.seek(30.0) is True
    assert refusing.seek(30.0) is False


def test_a_reader_that_cannot_seek_says_so_rather_than_pretending():
    # Watching and controlling are separate permissions: an overlay outside the
    # player can only ask.
    assert SessionReader.seek(NullSessionReader(), 30.0) is False


def test_the_base_reader_refuses_by_default():
    # A new platform reader that forgets to implement this must fail closed,
    # not silently claim success.
    assert NullSessionReader("no player").seek(10.0) is False
