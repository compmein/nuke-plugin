# init.py — CompMeIn Nuke Plugin
# Place this file (and the whole nuke-plugin folder) in your Nuke plugin path.
# Nuke automatically executes init.py on startup.

import nuke
import os
import sys

# Add the plugin directory to Python path so compmein_nuke.py can be imported
_plugin_dir = os.path.dirname(__file__)
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)
