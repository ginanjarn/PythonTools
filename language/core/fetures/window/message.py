import sublime

from ...session import Session


class WindowMessageMixins:

    def handle_window_logmessage(self, session: Session, params: dict):
        print(params["message"])

    def handle_window_showmessage(self, session: Session, params: dict):
        sublime.status_message(params["message"])
