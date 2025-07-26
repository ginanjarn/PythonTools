import logging

from ....constant import LOGGING_CHANNEL
from ...lsp_client import Response
from ...session import Session

LOGGER = logging.getLogger(LOGGING_CHANNEL)


class WorkspaceExecuteCommandMixins:

    def handle_workspace_executecommand(
        self, session: Session, params: Response
    ) -> dict:
        if error := params.error:
            print(error["message"])
        elif result := params.result:
            LOGGER.info(result)

        return None
