"""handler"""

import threading
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Optional, List, Dict, Callable, Any, Union

import sublime

from .document import TextChange
from .lsp_client import Client, Handler, Transport, MethodName, Response
from .errors import MethodNotFound


Params = Union[Response, dict]
HandlerFunction = Callable[[str, Params], Any]


COMPLETION_KIND_MAP = defaultdict(
    lambda _: sublime.KIND_AMBIGUOUS,
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


def get_completion_kind(lsp_kind: int) -> int:
    """"""
    return COMPLETION_KIND_MAP[lsp_kind]


class Command(ABC):
    """Command Interface"""

    @abstractmethod
    def initialize(self, view: sublime.View) -> None: ...

    @abstractmethod
    def textdocument_didopen(
        self, view: sublime.View, *, reload: bool = False
    ) -> None: ...
    @abstractmethod
    def textdocument_didsave(self, view: sublime.View) -> None: ...
    @abstractmethod
    def textdocument_didclose(self, view: sublime.View) -> None: ...
    @abstractmethod
    def textdocument_didchange(
        self, view: sublime.View, changes: List[TextChange]
    ) -> None: ...

    def textdocument_hover(self, view: sublime.View, row: int, col: int) -> None: ...
    def textdocument_completion(
        self, view: sublime.View, row: int, col: int
    ) -> None: ...
    def textdocument_signaturehelp(
        self, view: sublime.View, row: int, col: int
    ) -> None: ...
    def textdocument_formatting(self, view: sublime.View) -> None: ...
    def textdocument_definition(
        self, view: sublime.View, row: int, col: int
    ) -> None: ...
    def textdocument_preparerename(
        self, view: sublime.View, row: int, col: int
    ) -> None: ...
    def textdocument_rename(
        self, view: sublime.View, row: int, col: int, new_name: str
    ) -> None: ...


class Session(Command, Handler):
    """Session"""

    def __init__(self, transport: Transport):
        self.client = Client(transport, self)

        # server message handler
        self.handler_map: Dict[MethodName, HandlerFunction] = dict()
        self._run_server_lock = threading.Lock()

    def handle(self, method: MethodName, params: Params) -> Optional[Response]:
        """"""
        try:
            func = self.handler_map[method]
        except KeyError as err:
            raise MethodNotFound(err)

        return func(params)

    def register_handler(self, method: MethodName, function: HandlerFunction) -> None:
        """"""
        self.handler_map[method] = function

    def run_server(self, env: Optional[dict] = None) -> None:
        """"""
        # only one thread can run server
        if self._run_server_lock.locked():
            return

        with self._run_server_lock:
            if not self.client.is_server_running():
                sublime.status_message("running language server...")
                # sometimes the server stop working
                # we must reset the state before run server
                self.reset_state()

                self.client.run_server(env)
                self.client.listen()

    def reset_state(self) -> None:
        """reset session state"""
        self._reset_state()

    def is_ready(self) -> bool:
        """check session is ready"""
        return self._is_ready()

    def terminate(self) -> None:
        """terminate session"""
        self._terminate()
