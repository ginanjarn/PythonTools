"""sublime settings helper"""

from contextlib import contextmanager
import sublime

SETTINGS_BASENAME = "Pythontools.sublime-settings"


@contextmanager
def Settings(save=False) -> sublime.Settings:
    """settings manager"""

    yield sublime.load_settings(SETTINGS_BASENAME)
    if save:
        sublime.save_settings(SETTINGS_BASENAME)
