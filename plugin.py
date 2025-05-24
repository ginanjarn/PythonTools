"""Plugin loader"""

import sublime_plugin
from .internal.constant import PACKAGE_NAME

# load implementation
sublime_plugin.reload_plugin(f"{PACKAGE_NAME}.internal.plugin_implementation")
