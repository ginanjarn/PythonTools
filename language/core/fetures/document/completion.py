from collections import defaultdict
from functools import wraps
from typing import List
import sublime
import sublime_plugin

from ...document import is_valid_document
from ...lsp_client import Response
from ...session import Session
from ...uri import path_to_uri


def client_must_ready(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if self.client and self.client.is_ready():
            return func(self, *args, **kwargs)
        return None

    return wrapper


class CompletionEventListener(sublime_plugin.EventListener):
    client = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.prev_completion_point = 0

    @client_must_ready
    def on_query_completions(
        self, view: sublime.View, prefix: str, locations: List[int]
    ) -> sublime.CompletionList:
        if not is_valid_document(view):
            return None

        point = min(locations)
        if (
            document := self.client.completion_target
        ) and document.is_completion_available():

            items = document.pop_completion()
            if (not items) or self._is_context_changed(
                view, self.prev_completion_point, point
            ):
                self.hide_completions(view)
                return None

            return sublime.CompletionList(items, flags=sublime.INHIBIT_WORD_COMPLETIONS)

        self.prev_completion_point = point

        row, col = view.rowcol(point)
        self.client.textdocument_completion(view, row, col)
        self.hide_completions(view)

        return None

    def _is_context_changed(self, view: sublime.View, old: int, new: int) -> bool:
        """check if context moved from old point"""

        # point unchanged
        if old == new:
            return False
        # point changed but still in same word
        word = view.word(old)
        if new in word and view.substr(word).isidentifier():
            return False

        return True

    def hide_completions(self, view: sublime.View):
        view.run_command("hide_auto_complete")


COMPLETION_KIND_MAP = defaultdict(
    lambda: sublime.KIND_AMBIGUOUS,
    {
        1: (sublime.KindId.COLOR_ORANGISH, "t", ""),  # text
        2: (sublime.KindId.FUNCTION, "", ""),  # method
        3: (sublime.KindId.FUNCTION, "", ""),  # function
        4: (sublime.KindId.FUNCTION, "c", ""),  # constructor
        5: (sublime.KindId.VARIABLE, "", ""),  # field
        6: (sublime.KindId.VARIABLE, "", ""),  # variable
        7: (sublime.KindId.TYPE, "", ""),  # class
        8: (sublime.KindId.TYPE, "", ""),  # interface
        9: (sublime.KindId.NAMESPACE, "", ""),  # module
        10: (sublime.KindId.VARIABLE, "", ""),  # property
        11: (sublime.KindId.TYPE, "", ""),  # unit
        12: (sublime.KindId.COLOR_ORANGISH, "v", ""),  # value
        13: (sublime.KindId.TYPE, "", ""),  # enum
        14: (sublime.KindId.KEYWORD, "", ""),  # keyword
        15: (sublime.KindId.SNIPPET, "s", ""),  # snippet
        16: (sublime.KindId.VARIABLE, "v", ""),  # color
        17: (sublime.KindId.VARIABLE, "p", ""),  # file
        18: (sublime.KindId.VARIABLE, "p", ""),  # reference
        19: (sublime.KindId.VARIABLE, "p", ""),  # folder
        20: (sublime.KindId.VARIABLE, "v", ""),  # enum member
        21: (sublime.KindId.VARIABLE, "c", ""),  # constant
        22: (sublime.KindId.TYPE, "", ""),  # struct
        23: (sublime.KindId.TYPE, "e", ""),  # event
        24: (sublime.KindId.KEYWORD, "", ""),  # operator
        25: (sublime.KindId.TYPE, "", ""),  # type parameter
    },
)


def must_initialized(func):
    """exec if initialized"""

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if not self.session.is_initialized():
            return None
        return func(self, *args, **kwargs)

    return wrapper


class DocumentCompletionMixins:

    completion_target = None

    @must_initialized
    def textdocument_completion(self, view, row, col):
        if document := self.session.get_document(view.file_name()):
            self.completion_target = document
            self.message_pool.send_request(
                "textDocument/completion",
                {
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": path_to_uri(document.file_name)},
                },
            )

    def handle_textdocument_completion(self, session: Session, params: Response):
        if err := params.error:
            print(err["message"])

        elif result := params.result:
            items = [self._build_completion(item) for item in result["items"]]
            self.completion_target.show_completion(items)

    @staticmethod
    def _build_completion(completion_item: dict) -> sublime.CompletionItem:
        text = completion_item["label"]
        try:
            insert_text = completion_item["textEdit"]["newText"]
        except KeyError:
            insert_text = text

        signature = completion_item["detail"]
        kind = COMPLETION_KIND_MAP[completion_item["kind"]]

        return sublime.CompletionItem.snippet_completion(
            trigger=text,
            snippet=insert_text,
            annotation=signature,
            kind=kind,
        )
