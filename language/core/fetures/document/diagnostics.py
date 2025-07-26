from ...session import Session
from ...uri import uri_to_path


class DocumentDiagnosticsMixins:

    def handle_textdocument_publishdiagnostics(self, session: Session, params: dict):
        file_name = uri_to_path(params["uri"])
        diagnostics = params["diagnostics"]

        if document := session.get_document(file_name):
            session.diagnostic_manager.add(document.view, diagnostics)
