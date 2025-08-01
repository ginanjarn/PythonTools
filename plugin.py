"""Plugin loader"""

import sublime_plugin
from pathlib import Path

PACKAGE_NAME = Path(__file__).parent.name

# load implementation
sublime_plugin.reload_plugin(f"{PACKAGE_NAME}.language.pyserver_client.main")
sublime_plugin.reload_plugin(f"{PACKAGE_NAME}.environment.commands")
