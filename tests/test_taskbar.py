"""Windows must see the lyric panel as a minimizable application window."""

import sys
import tkinter as tk

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows taskbar")
def test_borderless_window_keeps_its_taskbar_button_when_minimized(tk_root):
    import ctypes
    from ctypes import wintypes

    from lyrica import chrome

    window = tk.Toplevel(tk_root)
    try:
        window.overrideredirect(True)
        window.update_idletasks()
        chrome.enable_taskbar(window)

        hwnd = wintypes.HWND(chrome.window_handle(window))
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, -16)
        ex_style = user32.GetWindowLongW(hwnd, -20)
        assert ex_style & 0x00040000  # WS_EX_APPWINDOW
        assert not ex_style & 0x00000080  # WS_EX_TOOLWINDOW
        assert style & 0x00020000  # WS_MINIMIZEBOX
        assert style & 0x00080000  # WS_SYSMENU, supplies the icon

        chrome.minimize(window)
        window.update_idletasks()
        assert chrome.is_minimized(window)
        chrome.restore(window)
        window.update_idletasks()
        assert not chrome.is_minimized(window)
    finally:
        window.destroy()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows taskbar")
def test_overlay_uses_taskbar_without_notification_icon(overlay):
    from lyrica import chrome

    hwnd = chrome.window_handle(overlay.root)
    assert hwnd
    assert not overlay.tray.show_icon
