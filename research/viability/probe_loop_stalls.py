"""Probe: what stalls the event loop, and how often?

Drag latency is not really about how fast a move is. It is about whether the
loop is free to notice the mouse at all. Anything that holds the loop — or the
interpreter lock, from another thread — shows up as a gap here, and a gap is
what a hand feels.

Schedules a wake-up every millisecond and records how late each one actually
is. A quiet loop returns ~1 ms. Anything much larger is a stall, and the
pattern of stalls says where it comes from.
"""
import statistics
import sys
import time
import tkinter as tk

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\Work\research\lyrica\src")

SECONDS = 8.0


def watch(label: str, *, reader: bool, artwork_thread: bool) -> None:
    from lyrica.sessions import create_reader

    root = tk.Tk()
    root.overrideredirect(True)
    root.geometry("500x120+400+400")
    tk.Canvas(root, width=500, height=120, bg="#101010",
              highlightthickness=0).pack()
    root.update()

    session = create_reader(interval=0.5) if reader else None
    if session:
        session.start()

    if artwork_thread:
        import threading

        from lyrica import artwork

        def churn():
            # Stands in for the cover pipeline: network plus image decoding on
            # a worker thread, which is what actually runs during playback.
            for _ in range(6):
                artwork.fetch_cover("Daft Punk", "Get Lucky", "", 600)
                time.sleep(0.4)

        threading.Thread(target=churn, daemon=True).start()

    gaps = []
    state = {"last": time.perf_counter(), "stop": False}

    def beat():
        if state["stop"]:
            return
        now = time.perf_counter()
        gaps.append((now - state["last"]) * 1000)
        state["last"] = now
        root.after(1, beat)

    def finish():
        state["stop"] = True
        if session:
            # Stopped before the window goes: Tk objects the reader's loop may
            # still touch cannot be torn down from another thread.
            session.stop()
        root.after(400, root.quit)

    root.after(10, beat)
    root.after(int(SECONDS * 1000), finish)
    root.mainloop()
    root.update()
    root.destroy()

    if not gaps:
        print(f"  {label}: no samples")
        return
    gaps.sort()
    n = len(gaps)
    over8 = sum(1 for g in gaps if g > 8)
    over16 = sum(1 for g in gaps if g > 16)
    print(f"  {label:34s} median {statistics.median(gaps):5.2f}  "
          f"p99 {gaps[int(n * 0.99)]:6.2f}  worst {gaps[-1]:7.2f} ms   "
          f">8ms: {over8:4d}  >16ms: {over16:4d}  of {n}")


MODES = {
    "bare": ("bare window", False, False),
    "reader": ("with the session reader", True, False),
    "both": ("with cover fetching too", True, True),
}


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in MODES:
        label, reader, art = MODES[sys.argv[1]]
        watch(label, reader=reader, artwork_thread=art)
        return

    # Each configuration in its own process. A reader thread and Tk do not
    # tear down cleanly together, and one run's teardown must not colour the
    # next run's numbers.
    import subprocess
    print("=== how late is a 1 ms wake-up? ===")
    for mode in MODES:
        result = subprocess.run([sys.executable, __file__, mode],  # noqa: S603
                                capture_output=True, text=True, check=False)
        line = (result.stdout or "").strip()
        print(line or f"  {mode}: no output ({result.returncode})")


if __name__ == "__main__":
    main()
