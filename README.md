# CompMeIn Nuke Plugin

Nano Banana 2 and Nano Banana Pro image generation, background removal, and Kling V3 video generation — directly inside Nuke.

---

## Features

### Nano Banana 2 / Nano Banana Pro
Connect up to 14 input nodes as reference images. Outputs a Read node with the generated image. Nano Banana 2 at 1K / 2K / 4K and Nano Banana Pro at 2K / 4K. Aspect ratio defaults to **Auto** — reads input 0 dimensions and snaps to the closest supported ratio; falls back to 16:9 for pure text-to-image.

### Remove Background (Alpha)
Outputs a Read node (PNG with alpha) + Premult node, ready to comp.

### Kling V3 Video Generation
6 scenarios in one node: Text to Video, Image to Video, First + Last Frame, I2V + Video Ref, Video Restyle, and Motion Control. Std mode = 720p, Pro mode = 1080p. Aspect ratio defaults to **Auto** (reads input 0).

---

## Installation

### 1. Copy files to your Nuke plugin path

| OS | Path |
|---|---|
| macOS | `~/.nuke/` |
| Windows | `%USERPROFILE%\.nuke\` |
| Linux | `~/.nuke/` |

Final structure:

```
~/.nuke/
  compmein_nuke.py
  init.py          ← add one line (see below)
  menu.py          ← add contents, or use as-is
```

**`init.py`** — add this line:
```python
nuke.pluginAddPath("~/.nuke")
```

> If you already have `init.py` / `menu.py`, just append the plugin's contents to yours.

### 2. Get your Nuke API Key

1. Go to [compmein.com/developer](https://www.compmein.com/developer)
2. Click the **Nuke API** tab
3. Click **+ Create Nuke Key** — generates a `cm_nk_...` key
4. Copy it

### 3. Enter the key in Nuke

The key can be set in two ways:

**Option A — Global (recommended):**
In the menu bar: **CompMeIn → Settings / API Key** → paste your key → OK.
Saved to `~/.compmein_nuke_settings.json`, loaded automatically on every launch.

**Option B — Per-node:**
Each node has an **API Key** field at the top of the User tab. Paste the key there to override the global key for that node.

---

## Usage

### Image Node (CMImage)

1. **CompMeIn → Create Image Node** (or from the Nodes toolbar)
2. Connect up to 14 nodes as reference images (inputs 0–13)
3. Enter a prompt, pick a model and aspect ratio
4. Click **Submit Job** — result loads into a Read node when ready

### Alpha Node (CMAlpha)

1. **CompMeIn → Create Alpha Node**
2. Connect a source node to **input 0**
3. Click **Submit Job** — a Read node (PNG) + Premult appear automatically

### Video Node (CMVideo)

1. **CompMeIn → Create Video Node**
2. Connect inputs as needed:
   - **Input 0** — first frame / character image
   - **Input 1** — last frame
   - **Inputs 2–8** — reference images
3. Select a tab for your scenario
4. Set the Video File path (for I2V+VideoRef, Video Restyle, Motion Control)
5. Enter a prompt, set duration and quality
6. Click **Submit Job** — polls until done, then click **Load Result**

> The "User" tab at the top of every node contains your API key, notification toggle, and token balance.

---

## Node Input Map (Video)

| Input | Purpose |
|---|---|
| 0 | First frame / character image |
| 1 | Last frame (First+Last scenario) |
| 2–8 | Reference images |

---

## Requirements

- Nuke 13+ (Python 3)
- No additional Python packages — pure stdlib
- Internet access to `compmein.com` and `veo-backend-484563986683.us-central1.run.app`

---

## Billing

All usage is billed to the CompMeIn account linked to your `cm_nk_` key.
Monitor usage and top up at [compmein.com/developer](https://www.compmein.com/developer).
