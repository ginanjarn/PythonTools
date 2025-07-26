from dataclasses import dataclass
from typing import List
import sublime
import sublime_plugin

from ..document import TextChange


@dataclass
class _BufferedTextChange:
    region: sublime.Region
    old_text: str
    new_text: str

    __slots__ = ["region", "old_text", "new_text"]

    def offset_move(self) -> int:
        return len(self.new_text) - len(self.old_text)

    def get_moved_region(self, move: int) -> sublime.Region:
        a, b = self.region.to_tuple()
        return sublime.Region(a + move, b + move)


class _ApplyTextChangesCommand(sublime_plugin.TextCommand):
    """changes item must serialized from 'TextChange'"""

    def run(self, edit: sublime.Edit, changes: List[dict]):
        text_changes = [self.to_text_change(c) for c in changes]
        self.apply(edit, text_changes)

    def apply(self, edit: sublime.Edit, text_changes: List[_BufferedTextChange]):
        move = 0
        for change in text_changes:
            replaced_region = change.get_moved_region(move)
            self.view.replace(edit, replaced_region, change.new_text)
            move += change.offset_move()

    def to_text_change(self, change: dict) -> _BufferedTextChange:
        text_point = self.view.text_point

        change = TextChange(**change)
        region = sublime.Region(text_point(*change.start), text_point(*change.end))
        old_text = self.view.substr(region)

        return _BufferedTextChange(region, old_text, change.text)
