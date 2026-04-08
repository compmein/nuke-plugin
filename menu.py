# menu.py — CompMeIn Nuke Plugin v3.0
# Nuke runs this after init.py on startup.

import nuke
import compmein_nuke

# ── Top menu bar ──────────────────────────────────────────────────────────────
toolbar = nuke.menu("Nuke")
m = toolbar.addMenu("&CompMeIn")
m.addCommand("Create Image Node", compmein_nuke.create_image_node)
m.addCommand("Create Video Node", compmein_nuke.create_video_node)
m.addCommand("Create Alpha Node", compmein_nuke.create_alpha_node)
m.addSeparator()
m.addCommand("Settings / API Key", compmein_nuke.show_settings_panel)

# ── Nodes toolbar (left panel — more visible) ────────────────────────────────
nodes_toolbar = nuke.toolbar("Nodes")
cm = nodes_toolbar.addMenu("CompMeIn", icon="")
cm.addCommand("Image",  "compmein_nuke.create_image_node()", icon="")
cm.addCommand("Video",  "compmein_nuke.create_video_node()", icon="")
cm.addCommand("Alpha",  "compmein_nuke.create_alpha_node()", icon="")
