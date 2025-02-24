"""session object"""

import logging
import threading
from typing import Optional, Dict, List, Any

import sublime

from .constant import LOGGING_CHANNEL
from .document import BufferedDocument


PathStr = str
LOGGER = logging.getLogger(LOGGING_CHANNEL)


class Session:
    """Session"""

    def __init__(self) -> None:

        # Map document by view is easier to track if view is valid.
        # If we map by file name, one document my related to multiple 'View'
        # and some times the 'View' is invalid.
        self.working_documents: Dict[sublime.View, BufferedDocument] = {}
        self._lock = threading.Lock()

    def reset(self):
        """"""
        with self._lock:
            self.working_documents.clear()

    def get_document(
        self, view: sublime.View, /, default: Any = None
    ) -> Optional[BufferedDocument]:
        with self._lock:
            return self.working_documents.get(view, default)

    def add_document(self, document: BufferedDocument) -> None:
        with self._lock:
            self.working_documents[document.view] = document

    def remove_document(self, view: sublime.View) -> None:
        with self._lock:
            try:
                del self.working_documents[view]
            except KeyError as err:
                LOGGER.debug("document not found %s", err)
                pass

    def get_document_by_name(
        self, file_name: PathStr, /, default: Any = None
    ) -> Optional[BufferedDocument]:
        """get document by name"""

        with self._lock:
            for view, document in self.working_documents.items():
                if view.file_name() == file_name:
                    return document
            return default

    def get_documents(
        self, file_name: Optional[PathStr] = None
    ) -> List[BufferedDocument]:
        """get documents.
        If file_name assigned, return documents with file_name filtered.
        """
        with self._lock:
            if not file_name:
                return [doc for _, doc in self.working_documents.items()]
            return [
                doc
                for _, doc in self.working_documents.items()
                if doc.file_name == file_name
            ]
