# compmein_nuke.py — CompMeIn Nuke Plugin v3.0
# Three separate nodes: Image, Video, Alpha.
# GCS signed-URL upload to bypass Vercel 4.5MB limit.
# Compatible with Nuke 13+ (Python 3). Pure stdlib — no pip installs.

import nuke
import nukescripts
import os
import json
import tempfile
import threading
import time
import urllib.request
import urllib.parse
import urllib.error

# ── Constants ─────────────────────────────────────────────────────────────────

BASE_URL = "https://www.compmein.com"
BACKEND_URL = "https://veo-backend-484563986683.us-central1.run.app"
SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".compmein_nuke_settings.json")

CLR_RENDERING = 0xf0a000ff   # amber
CLR_READY     = 0x4caf50ff   # green
CLR_ERROR     = 0xc62828ff   # red
CLR_DEFAULT   = 0            # reset

GEN_MODELS = [
    ("quick_0.5k", "Quick 0.5K", 9),
    ("quick_1k",   "Quick 1K",  13),
    ("quick_2k",   "Quick 2K",  21),
    ("quick_4k",   "Quick 4K",  31),
    ("pro_2k",     "Pro 2K",    30),
    ("pro_4k",     "Pro 4K",    50),
]
GEN_MODEL_LABELS = ["{} ({} tok)".format(m[1], m[2]) for m in GEN_MODELS]
GEN_AR = ["16:9", "1:1", "9:16", "4:3", "3:4", "3:2", "2:3"]

KLING_SCENARIOS = [
    "Text to Video",
    "I2V (First Frame)",
    "I2V (First + Last Frame)",
    "I2V + Video Ref",
    "Video Restyle",
]
KLING_RESOLUTIONS = ["720p", "1080p"]
KLING_AR = ["16:9", "1:1", "9:16", "4:3", "3:4", "3:2", "2:3"]


# ── Settings ──────────────────────────────────────────────────────────────────

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_settings(data):
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        nuke.message("CompMeIn: Could not save settings.\n{}".format(e))


def get_api_key():
    return load_settings().get("api_key", "")


def _node_api_key(node):
    key = node["api_key"].value().strip()
    return key if key and key.startswith("cm_nk_") else get_api_key()


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _multipart_body(fields, files):
    boundary = "----CompMeInBoundary7MA4YWxkTrZu0gW"
    body = b""
    for name, value in fields.items():
        body += "--{}\r\n".format(boundary).encode()
        body += "Content-Disposition: form-data; name=\"{}\"\r\n\r\n".format(name).encode()
        body += (value.encode("utf-8") if isinstance(value, str) else value)
        body += b"\r\n"
    for field_name, filename, content_type, data in files:
        body += "--{}\r\n".format(boundary).encode()
        body += "Content-Disposition: form-data; name=\"{}\"; filename=\"{}\"\r\n".format(
            field_name, filename
        ).encode()
        body += "Content-Type: {}\r\n\r\n".format(content_type).encode()
        body += data
        body += b"\r\n"
    body += "--{}--\r\n".format(boundary).encode()
    return boundary, body


def _post_json(endpoint, api_key, fields, files=None, base=None):
    url = (base or BASE_URL) + endpoint
    boundary, body = _multipart_body(fields, files or [])
    headers = {
        "Authorization": "Bearer {}".format(api_key),
        "Content-Type": "multipart/form-data; boundary={}".format(boundary),
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8")), resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw), e.code
        except Exception:
            raise RuntimeError("HTTP {}: {}".format(e.code, raw))


def _post_json_body(endpoint, api_key, body_dict, base=None):
    """POST with JSON body (not multipart)."""
    url = (base or BASE_URL) + endpoint
    data = json.dumps(body_dict).encode("utf-8")
    headers = {
        "Authorization": "Bearer {}".format(api_key),
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8")), resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw), e.code
        except Exception:
            raise RuntimeError("HTTP {}: {}".format(e.code, raw))


def _post_raw(endpoint, api_key, fields, files=None):
    url = BASE_URL + endpoint
    boundary, body = _multipart_body(fields, files or [])
    headers = {
        "Authorization": "Bearer {}".format(api_key),
        "Content-Type": "multipart/form-data; boundary={}".format(boundary),
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
        balance = resp.headers.get("X-Token-Balance")
        return data, balance


def _get_json(endpoint, api_key, params=None):
    url = BASE_URL + endpoint
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Authorization": "Bearer {}".format(api_key)}
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _download_to_file(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "CompMeIn-Nuke/3.0"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)


def _upload_to_gcs(signed_url, file_path, content_type):
    """PUT file to GCS signed URL."""
    with open(file_path, "rb") as f:
        data = f.read()
    req = urllib.request.Request(
        signed_url, data=data, method="PUT",
        headers={"Content-Type": content_type}
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return resp.status


def _get_upload_url(api_key, content_type="image/jpeg", ext="jpg"):
    """Get a signed GCS upload URL from our API."""
    resp, code = _post_json_body("/api/nuke/upload-url", api_key, {
        "contentType": content_type,
        "ext": ext,
    })
    if code != 200 or not resp.get("uploadUrl"):
        raise RuntimeError("Failed to get upload URL: {}".format(resp))
    return resp["uploadUrl"], resp["path"]


# ── Nuke helpers ──────────────────────────────────────────────────────────────

def _render_input_to_tmp(node, input_idx, fmt="jpeg", max_edge=2048):
    """Render a node's input at input_idx to a temp file, downscaled so the
    longest edge does not exceed *max_edge* pixels.  Returns path or None."""
    src = node.input(input_idx)
    if not src:
        return None
    ext = "jpg" if fmt == "jpeg" else "png"
    tmp = tempfile.NamedTemporaryFile(suffix=".{}".format(ext), delete=False)
    tmp.close()

    # Downscale if the source exceeds max_edge on either side
    reformat = None
    sw = src.width()
    sh = src.height()
    if sw > max_edge or sh > max_edge:
        scale = float(max_edge) / max(sw, sh)
        reformat = nuke.nodes.Reformat(inputs=[src])
        reformat["type"].setValue("to scale")
        reformat["scale"].setValue(scale)
        reformat["filter"].setValue("Lanczos6")
        write_input = reformat
    else:
        write_input = src

    w = nuke.nodes.Write(
        file=tmp.name.replace("\\", "/"),
        file_type=fmt,
        inputs=[write_input],
    )
    if fmt == "jpeg":
        w["_jpeg_quality"].setValue(0.85)
    try:
        nuke.execute(w, nuke.frame(), nuke.frame())
    finally:
        nuke.delete(w)
        if reformat:
            nuke.delete(reformat)
    return tmp.name


def _render_inputs_to_tmp(node, start_idx, count, fmt="jpeg"):
    """Render multiple inputs. Returns list of (input_idx, path) tuples."""
    results = []
    for i in range(start_idx, start_idx + count):
        path = _render_input_to_tmp(node, i, fmt)
        if path:
            results.append((i, path))
    return results


def _create_group(name, label, num_inputs):
    """Create a Group node with the specified number of Input nodes inside."""
    node = nuke.createNode("Group", inpanel=True)
    node.setName(name)
    node["label"].setValue("CompMeIn\n{}".format(label))
    node["note_font_size"].setValue(14)

    # Build internal graph: Input(s) → Output
    node.begin()
    inputs = []
    for i in range(num_inputs):
        inp = nuke.nodes.Input()
        inp.setName("Input{}".format(i))
        inputs.append(inp)
    out = nuke.nodes.Output()
    if inputs:
        out.setInput(0, inputs[0])  # pass first input through
    node.end()

    return node


def _set_state(node_name, color, label, st_knob, st_html,
               sub_knob=None, ld_knob=None, sub_on=True, ld_on=False):
    n = nuke.toNode(node_name)
    if not n:
        return
    n["tile_color"].setValue(color)
    n["label"].setValue("CompMeIn\n{}".format(label))
    n[st_knob].setValue(st_html)
    if sub_knob:
        n[sub_knob].setEnabled(sub_on)
    if ld_knob:
        n[ld_knob].setEnabled(ld_on)


# ── Job results store ────────────────────────────────────────────────────────

_results = {}


# ── Cost helpers ─────────────────────────────────────────────────────────────

def _kling_cost(dur, mode, has_video_ref=False):
    d = int(dur)
    if has_video_ref:
        return d * (34 if mode == "pro" else 25)
    return d * (23 if mode == "pro" else 17)


def _add_settings_to_user_tab(node):
    """Add settings knobs BEFORE any Tab_Knob so they land in the default 'User' tab."""
    k = nuke.String_Knob("api_key", "API Key")
    k.setValue(get_api_key())
    node.addKnob(k)

    node.addKnob(nuke.Text_Knob("api_key_info", "",
        "<small>Enter your <b>cm_nk_...</b> key from "
        "compmein.com/developer</small>"))

    k = nuke.Boolean_Knob("notify",
                           "Notify on job completion")
    k.setValue(True)
    k.setFlag(nuke.STARTLINE)
    node.addKnob(k)

    node.addKnob(nuke.Text_Knob("balance_display", "", ""))


# ==============================================================================
# NODE 1: CompMeIn Image
# ==============================================================================

def create_image_node():
    node = _create_group("CMImage1", "Image", 14)

    mk = nuke.Boolean_Knob("is_compmein_image", "")
    mk.setVisible(False)
    mk.setValue(True)
    node.addKnob(mk)

    # Settings go into default "User" tab (before any Tab_Knob)
    _add_settings_to_user_tab(node)

    # ── Generate Image tab ───────────────────────────────
    node.addKnob(nuke.Tab_Knob("tab_gen", "Generate Image"))

    node.addKnob(nuke.Multiline_Eval_String_Knob("gen_prompt", "Prompt"))

    k = nuke.Enumeration_Knob("gen_model", "Model", GEN_MODEL_LABELS)
    k.setValue(GEN_MODEL_LABELS[1])
    node.addKnob(k)

    k = nuke.Enumeration_Knob("gen_ar", "Aspect Ratio", GEN_AR)
    k.setValue("16:9")
    node.addKnob(k)

    node.addKnob(nuke.Text_Knob("gen_input_info", "",
        "<small>Connect nodes as inputs for reference images. "
        "Quick: max 14, Pro: max 14.</small>"))

    node.addKnob(nuke.Text_Knob("gen_cost", "",
        "<b>Cost: {} tokens</b>".format(GEN_MODELS[1][2])))

    node.addKnob(nuke.Text_Knob("gen_div", "", ""))

    k = nuke.PyScript_Knob("gen_submit", "Submit Job",
                            "compmein_nuke._submit_genimage()")
    k.setFlag(nuke.STARTLINE)
    node.addKnob(k)

    k = nuke.PyScript_Knob("gen_load", "Load Result",
                            "compmein_nuke._load_genimage()")
    k.setEnabled(False)
    node.addKnob(k)

    node.addKnob(nuke.Text_Knob("gen_status", "", ""))

    node["knobChanged"].setValue("compmein_nuke._on_image_knob_changed()")
    return node


def _on_image_knob_changed():
    node = nuke.thisNode()
    name = nuke.thisKnob().name()
    if name == "gen_model":
        idx = int(node["gen_model"].getValue())
        node["gen_cost"].setValue(
            "<b>Cost: {} tokens</b>".format(GEN_MODELS[idx][2]))
    elif name == "api_key":
        key = node["api_key"].value().strip()
        if key and key.startswith("cm_nk_"):
            s = load_settings()
            s["api_key"] = key
            save_settings(s)


def _submit_genimage():
    node = nuke.thisNode()
    api_key = _node_api_key(node)
    if not api_key or not api_key.startswith("cm_nk_"):
        nuke.message("CompMeIn: Set your API key in the Settings tab.")
        return

    prompt = node["gen_prompt"].value().strip()
    if not prompt:
        nuke.message("CompMeIn: Enter a prompt.")
        return

    model_idx = int(node["gen_model"].getValue())
    model_key = GEN_MODELS[model_idx][0]
    ar = GEN_AR[int(node["gen_ar"].getValue())]

    # Render connected inputs as JPEG, upload via GCS signed URL
    ref_inputs = _render_inputs_to_tmp(node, 0, node.maxInputs(), "jpeg")
    if len(ref_inputs) > 14:
        nuke.message("CompMeIn: Max 14 references. {} connected.".format(len(ref_inputs)))
        for _, p in ref_inputs:
            os.unlink(p)
        return

    nn = node.name()
    _set_state(nn, CLR_RENDERING, "Rendering...", "gen_status",
               "<b style='color:#f0a000'>Generating...</b>",
               "gen_submit", "gen_load", False, False)

    def _work():
        try:
            # Send ref images as multipart (JPEG quality 90 keeps 4K under 4MB)
            files = []
            for idx, path in ref_inputs:
                try:
                    with open(path, "rb") as f:
                        files.append(("ref_images", "ref_{}.jpg".format(idx),
                                      "image/jpeg", f.read()))
                finally:
                    os.unlink(path)

            fields = {"prompt": prompt, "modelType": model_key, "aspectRatio": ar}
            resp, _ = _post_json("/api/nanobanana/generate-image", api_key,
                                 fields, files)
            b64 = resp.get("imageBase64")
            bal = resp.get("balance")

            if not b64:
                def _f():
                    _set_state(nn, CLR_ERROR, "Failed", "gen_status",
                               "<b style='color:#c62828'>Failed: {}</b>".format(
                                   resp.get("error", "No image")),
                               "gen_submit", "gen_load", True, False)
                nuke.executeInMainThread(_f)
                return

            ext = "jpg" if "jpeg" in resp.get("mimeType", "") else "png"
            tmp = tempfile.NamedTemporaryFile(suffix="." + ext, delete=False)
            from base64 import b64decode as _b64d
            tmp.write(_b64d(b64))
            tmp.close()
            _results[nn + "_gen"] = {"path": tmp.name, "prompt": prompt}

            def _ok():
                _set_state(nn, CLR_READY, "Image Ready", "gen_status",
                           "<b style='color:#4caf50'>Done</b>",
                           "gen_submit", "gen_load", True, True)
                n = nuke.toNode(nn)
                if n and bal is not None:
                    n["balance_display"].setValue(
                        "Balance: {} tokens".format(bal))
                if n and n["notify"].value():
                    if nuke.ask("GenImage ready.\nLoad result now?"):
                        _load_result_gen(nn)
            nuke.executeInMainThread(_ok)

        except Exception as e:
            for _, p in ref_inputs:
                try:
                    os.unlink(p)
                except Exception:
                    pass
            def _e(ex=e):
                _set_state(nn, CLR_ERROR, "Error", "gen_status",
                           "<b style='color:#c62828'>Error</b>",
                           "gen_submit", "gen_load", True, False)
                nuke.message("CompMeIn Error:\n{}".format(ex))
            nuke.executeInMainThread(_e)

    threading.Thread(target=_work, daemon=True).start()


def _load_genimage():
    _load_result_gen(nuke.thisNode().name())


def _load_result_gen(nn):
    data = _results.pop(nn + "_gen", None)
    if not data:
        nuke.message("CompMeIn: No result to load.")
        return
    r = nuke.createNode("Read", inpanel=False)
    r["file"].setValue(data["path"].replace("\\", "/"))
    r["label"].setValue("CompMeIn: {}".format(data["prompt"][:40]))
    _set_state(nn, CLR_DEFAULT, "Image", "gen_status", "",
               "gen_submit", "gen_load", True, False)


# ==============================================================================
# NODE 2: CompMeIn Alpha (Remove BG)
# ==============================================================================

def create_alpha_node():
    node = _create_group("CMAlpha1", "Alpha", 1)

    mk = nuke.Boolean_Knob("is_compmein_alpha", "")
    mk.setVisible(False)
    mk.setValue(True)
    node.addKnob(mk)

    # Settings go into default "User" tab
    _add_settings_to_user_tab(node)

    # ── Remove BG tab ────────────────────────────────────
    node.addKnob(nuke.Tab_Knob("tab_bg", "Remove BG"))

    node.addKnob(nuke.Text_Knob("bg_info", "",
        "<b>Remove Background</b> — 1 token"
        "<br><small>Connect a node to input 0, then click Submit."
        " Result auto-loads with Premult.</small>"))

    node.addKnob(nuke.Text_Knob("bg_div", "", ""))

    k = nuke.PyScript_Knob("bg_submit", "Submit Job",
                            "compmein_nuke._submit_removebg()")
    k.setFlag(nuke.STARTLINE)
    node.addKnob(k)

    k = nuke.PyScript_Knob("bg_load", "Load Result",
                            "compmein_nuke._load_removebg()")
    k.setEnabled(False)
    node.addKnob(k)

    node.addKnob(nuke.Text_Knob("bg_status", "", ""))

    node["knobChanged"].setValue("compmein_nuke._on_alpha_knob_changed()")
    return node


def _on_alpha_knob_changed():
    node = nuke.thisNode()
    name = nuke.thisKnob().name()
    if name == "api_key":
        key = node["api_key"].value().strip()
        if key and key.startswith("cm_nk_"):
            s = load_settings()
            s["api_key"] = key
            save_settings(s)


def _submit_removebg():
    node = nuke.thisNode()
    api_key = _node_api_key(node)
    if not api_key or not api_key.startswith("cm_nk_"):
        nuke.message("CompMeIn: Set your API key in the Settings tab.")
        return

    src = node.input(0)
    if not src:
        nuke.message("CompMeIn: Connect a node to input 0.")
        return

    # Render input as PNG (need alpha channel support for output)
    path = _render_input_to_tmp(node, 0, "png")
    if not path:
        nuke.message("CompMeIn: Failed to render input.")
        return

    nn = node.name()
    src_name = src.name()
    _set_state(nn, CLR_RENDERING, "Removing BG...", "bg_status",
               "<b style='color:#f0a000'>Uploading & Processing...</b>",
               "bg_submit", "bg_load", False, False)

    def _work():
        try:
            with open(path, "rb") as f:
                img_bytes = f.read()
            os.unlink(path)
            resp_data, balance = _post_raw("/api/remove-bg", api_key, {},
                [("image", "frame.png", "image/png", img_bytes)])

            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp.write(resp_data)
            tmp.close()
            _results[nn + "_bg"] = {"path": tmp.name, "source": src_name,
                                    "balance": balance}

            def _ok():
                _set_state(nn, CLR_READY, "BG Ready", "bg_status",
                           "<b style='color:#4caf50'>Done</b>",
                           "bg_submit", "bg_load", True, True)
                n = nuke.toNode(nn)
                if n and balance:
                    n["balance_display"].setValue(
                        "Balance: {} tokens".format(balance))
                if n and n["notify"].value():
                    if nuke.ask("Remove BG ready.\nLoad result now?"):
                        _load_result_bg(nn)
            nuke.executeInMainThread(_ok)

        except Exception as e:
            try:
                os.unlink(path)
            except Exception:
                pass
            def _err(ex=e):
                _set_state(nn, CLR_ERROR, "Error", "bg_status",
                           "<b style='color:#c62828'>Error</b>",
                           "bg_submit", "bg_load", True, False)
                nuke.message("CompMeIn Error:\n{}".format(ex))
            nuke.executeInMainThread(_err)

    threading.Thread(target=_work, daemon=True).start()


def _load_removebg():
    _load_result_bg(nuke.thisNode().name())


def _load_result_bg(nn):
    data = _results.pop(nn + "_bg", None)
    if not data:
        nuke.message("CompMeIn: No result to load.")
        return
    r = nuke.createNode("Read", inpanel=False)
    r["file"].setValue(data["path"].replace("\\", "/"))
    r["label"].setValue("BG Removed - {}".format(data["source"]))
    pm = nuke.createNode("Premult", inpanel=False)
    pm.setInput(0, r)
    _set_state(nn, CLR_DEFAULT, "Alpha", "bg_status", "",
               "bg_submit", "bg_load", True, False)


# ==============================================================================
# NODE 3: CompMeIn Video (Kling V3) — Tab-per-scenario
# ==============================================================================

# Scenario configs: (idx, prefix, tab_label, has_first, has_last, max_refs, ref_start,
#                     needs_video, dur_min, dur_max, refer_type)
_SCENARIOS = [
    # 0: Text to Video
    {"prefix": "t2v", "tab": "Text to Video",
     "first": False, "last": False, "refs": 0, "ref_start": 2,
     "video": False, "dur_min": 3, "dur_max": 15, "refer": None,
     "info": "<small>Prompt only. No images or video. 3-15s.</small>"},
    # 1: Image to Video (first frame)
    {"prefix": "i2v", "tab": "Image to Video",
     "first": True, "last": False, "refs": 6, "ref_start": 2,
     "video": False, "dur_min": 3, "dur_max": 15, "refer": None,
     "info": "<small>Connect first frame to input 0. Up to 6 refs on inputs 2-7.</small>"},
    # 2: First + Last Frame
    {"prefix": "fl", "tab": "First + Last Frame",
     "first": True, "last": True, "refs": 0, "ref_start": 2,
     "video": False, "dur_min": 3, "dur_max": 15, "refer": None,
     "info": "<small>First frame: input 0. Last frame: input 1. No refs or video.</small>"},
    # 3: I2V + Video Ref
    {"prefix": "ivr", "tab": "I2V + Video Ref",
     "first": True, "last": False, "refs": 3, "ref_start": 2,
     "video": True, "dur_min": 3, "dur_max": 10, "refer": "feature",
     "info": "<small>Optional first frame (input 0). Up to 3 refs (inputs 2-4). "
             "Set Video File. 3-10s.</small>"},
    # 4: Video Restyle
    {"prefix": "rst", "tab": "Video Restyle",
     "first": False, "last": False, "refs": 4, "ref_start": 2,
     "video": True, "dur_min": 3, "dur_max": 10, "refer": "base",
     "info": "<small>Set Video File (required). Up to 4 refs (inputs 2-5). "
             "3-10s. Auto-trimmed.</small>"},
    # 5: Motion Control
    {"prefix": "mc", "tab": "Motion Control",
     "first": True, "last": False, "refs": 0, "ref_start": 2,
     "video": True, "dur_min": 3, "dur_max": 10, "refer": "motion",
     "info": "<small>Character image: input 0. Motion video: set Video File. "
             "Std 25 tk/s, Pro 34 tk/s.</small>"},
]


def _add_scenario_tab(node, sc):
    """Add one scenario tab with its own duration, cost, submit, load, status."""
    p = sc["prefix"]
    node.addKnob(nuke.Tab_Knob("tab_{}".format(p), sc["tab"]))

    node.addKnob(nuke.Text_Knob("{}_info".format(p), "", sc["info"]))

    # Prompt
    node.addKnob(nuke.Multiline_Eval_String_Knob("{}_prompt".format(p), "Prompt"))

    # Video file (only for video-requiring scenarios)
    if sc["video"]:
        k = nuke.File_Knob("{}_video".format(p), "Video File")
        node.addKnob(k)

    # Motion control specific knobs
    if sc["refer"] == "motion":
        k = nuke.Enumeration_Knob("{}_orient".format(p), "Character Orientation",
                                   ["image", "video"])
        k.setValue("image")
        node.addKnob(k)

        k = nuke.Boolean_Knob("{}_keep_sound".format(p), "Keep Original Sound")
        k.setValue(True)
        k.setFlag(nuke.STARTLINE)
        node.addKnob(k)

    # Duration slider
    k = nuke.Double_Knob("{}_dur".format(p), "Duration (sec)")
    k.setValue(5.0)
    k.setRange(float(sc["dur_min"]), float(sc["dur_max"]))
    node.addKnob(k)

    # Resolution + Aspect Ratio (not for motion control)
    if sc["refer"] != "motion":
        k = nuke.Enumeration_Knob("{}_res".format(p), "Resolution", KLING_RESOLUTIONS)
        k.setValue(KLING_RESOLUTIONS[0])
        node.addKnob(k)

        k = nuke.Enumeration_Knob("{}_ar".format(p), "Aspect Ratio", KLING_AR)
        k.setValue("16:9")
        node.addKnob(k)
    else:
        # Motion control only has mode
        k = nuke.Enumeration_Knob("{}_res".format(p), "Mode", KLING_RESOLUTIONS)
        k.setValue(KLING_RESOLUTIONS[0])
        node.addKnob(k)

    # Cost display
    node.addKnob(nuke.Text_Knob("{}_cost".format(p), "",
        "<b>Cost: {} tokens</b>".format(_kling_cost(5, "std", sc["video"]))))

    node.addKnob(nuke.Text_Knob("{}_div".format(p), "", ""))

    # Submit / Load / Status
    k = nuke.PyScript_Knob("{}_submit".format(p), "Submit Job",
        "compmein_nuke._submit_kling_scenario({})".format(
            _SCENARIOS.index(sc)))
    k.setFlag(nuke.STARTLINE)
    node.addKnob(k)

    k = nuke.PyScript_Knob("{}_load".format(p), "Load Result",
        "compmein_nuke._load_kling()")
    k.setEnabled(False)
    node.addKnob(k)

    node.addKnob(nuke.Text_Knob("{}_status".format(p), "", ""))


def create_video_node():
    # Input layout: 0=first_frame, 1=last_frame, 2-8=ref images (up to 7)
    node = _create_group("CMVideo1", "Video", 9)

    mk = nuke.Boolean_Knob("is_compmein_video", "")
    mk.setVisible(False)
    mk.setValue(True)
    node.addKnob(mk)

    # Settings in default "User" tab
    _add_settings_to_user_tab(node)

    node.addKnob(nuke.Text_Knob("v_input_map", "",
        "<small><b>Inputs:</b> 0=First Frame, 1=Last Frame, 2-8=Ref Images</small>"))

    # Add one tab per scenario
    for sc in _SCENARIOS:
        _add_scenario_tab(node, sc)

    node["knobChanged"].setValue("compmein_nuke._on_video_knob_changed()")
    return node


def _on_video_knob_changed():
    node = nuke.thisNode()
    name = nuke.thisKnob().name()

    # Update cost when duration or resolution changes
    for sc in _SCENARIOS:
        p = sc["prefix"]
        dur_name = "{}_dur".format(p)
        res_name = "{}_res".format(p)
        if name in (dur_name, res_name):
            if sc["refer"] == "motion":
                dur = int(node[dur_name].value())
                res_idx = int(node[res_name].getValue())
                mode = "pro" if res_idx == 1 else "std"
                cost = dur * (34 if mode == "pro" else 25)
                node["{}_cost".format(p)].setValue(
                    "<b>Cost: {} tokens</b>".format(cost))
            else:
                dur = node[dur_name].value()
                res_idx = int(node[res_name].getValue())
                mode = "pro" if res_idx == 1 else "std"
                cost = _kling_cost(dur, mode, sc["video"])
                node["{}_cost".format(p)].setValue(
                    "<b>Cost: {} tokens</b>".format(int(cost)))
            return

    if name == "api_key":
        key = node["api_key"].value().strip()
        if key and key.startswith("cm_nk_"):
            s = load_settings()
            s["api_key"] = key
            save_settings(s)


def _submit_kling_scenario(scenario_idx):
    """Dispatch submit for a given scenario index."""
    node = nuke.thisNode()
    sc = _SCENARIOS[scenario_idx]
    p = sc["prefix"]
    api_key = _node_api_key(node)
    if not api_key or not api_key.startswith("cm_nk_"):
        nuke.message("CompMeIn: Set your API key in the Settings tab (User tab).")
        return

    is_motion = sc["refer"] == "motion"

    prompt = node["{}_prompt".format(p)].value().strip()
    if not prompt and not is_motion:
        nuke.message("CompMeIn: Enter a prompt.")
        return

    # Read per-tab knobs
    video_path = ""
    if sc["video"]:
        video_path = node["{}_video".format(p)].value().strip()
        if not video_path:
            nuke.message("CompMeIn: Set the Video File path.")
            return

    if is_motion:
        dur = int(node["{}_dur".format(p)].value())
        res_idx = int(node["{}_res".format(p)].getValue())
        mode = "pro" if res_idx == 1 else "std"
        ar = "16:9"  # not used for motion control
    else:
        dur = int(node["{}_dur".format(p)].value())
        res_idx = int(node["{}_res".format(p)].getValue())
        mode = "pro" if res_idx == 1 else "std"
        ar = KLING_AR[int(node["{}_ar".format(p)].getValue())]

    # Validate inputs per scenario
    if sc["first"] and not is_motion and not node.input(0):
        if scenario_idx in (1, 2):  # required first frame
            nuke.message("CompMeIn: Connect first frame to input 0.")
            return
    if sc["first"] and is_motion and not node.input(0):
        nuke.message("CompMeIn: Connect character image to input 0.")
        return
    if sc["last"] and not node.input(1):
        nuke.message("CompMeIn: Connect last frame to input 1.")
        return

    # Render images
    first_frame_path = None
    last_frame_path = None
    ref_paths = []
    try:
        if sc["first"]:
            first_frame_path = _render_input_to_tmp(node, 0, "jpeg")
        if sc["last"]:
            last_frame_path = _render_input_to_tmp(node, 1, "jpeg")
        if sc["refs"] > 0:
            ref_paths = _render_inputs_to_tmp(node, sc["ref_start"],
                                               sc["refs"], "jpeg")
    except Exception as e:
        nuke.message("CompMeIn: Render failed.\n{}".format(e))
        return

    nn = node.name()
    st_knob = "{}_status".format(p)
    sub_knob = "{}_submit".format(p)
    ld_knob = "{}_load".format(p)
    _set_state(nn, CLR_RENDERING, "Submitting...", st_knob,
               "<b style='color:#f0a000'>Uploading...</b>",
               sub_knob, ld_knob, False, False)

    def _work():
        try:
            files = []

            # Pack images as multipart
            if first_frame_path:
                try:
                    with open(first_frame_path, "rb") as f:
                        if is_motion:
                            files.append(("image", "char.jpg",
                                          "image/jpeg", f.read()))
                        else:
                            files.append(("first_frame", "first.jpg",
                                          "image/jpeg", f.read()))
                finally:
                    os.unlink(first_frame_path)

            if last_frame_path:
                try:
                    with open(last_frame_path, "rb") as f:
                        files.append(("last_frame", "last.jpg",
                                      "image/jpeg", f.read()))
                finally:
                    os.unlink(last_frame_path)

            for idx, path in ref_paths:
                try:
                    with open(path, "rb") as f:
                        files.append(("ref_image_{}".format(idx - sc["ref_start"]),
                                      "ref_{}.jpg".format(idx - sc["ref_start"]),
                                      "image/jpeg", f.read()))
                finally:
                    os.unlink(path)

            # Handle video: upload to GCS first, then trim via backend
            video_signed_url = ""
            if sc["video"] and video_path:
                def _ut():
                    n = nuke.toNode(nn)
                    if n:
                        n[st_knob].setValue(
                            "<b style='color:#f0a000'>Uploading video...</b>")
                nuke.executeInMainThread(_ut)

                # Step 1: Upload raw video to GCS via signed URL
                vid_upload_url, vid_gcs_path = _get_upload_url(
                    api_key, content_type="video/mp4", ext="mp4")
                _upload_to_gcs(vid_upload_url, video_path, "video/mp4")

                def _ut2():
                    n = nuke.toNode(nn)
                    if n:
                        n[st_knob].setValue(
                            "<b style='color:#f0a000'>Trimming video...</b>")
                nuke.executeInMainThread(_ut2)

                # Step 2: Ask backend to trim from GCS path
                trim_resp, trim_code = _post_json_body(
                    "/trim_from_gcs", api_key,
                    {
                        "gcs_path": vid_gcs_path,
                        "bucket_name": "compmein-assets",
                        "duration": dur,
                        "user_id": "nuke_upload",
                        "purpose": "temp",
                        "prefix": "nk",
                    },
                    base=BACKEND_URL)
                if trim_code != 200:
                    raise RuntimeError("Video trim failed: {}".format(trim_resp))

                video_signed_url = trim_resp.get("signedUrl") or trim_resp.get("url")
                if not video_signed_url:
                    raise RuntimeError("Video trim failed: {}".format(trim_resp))

            def _us():
                n = nuke.toNode(nn)
                if n:
                    n[st_knob].setValue(
                        "<b style='color:#f0a000'>Submitting...</b>")
            nuke.executeInMainThread(_us)

            # Build request fields and choose endpoint
            if is_motion:
                # Motion Control uses a different API route
                fields = {
                    "prompt": prompt,
                    "mode": mode,
                    "duration": str(dur),
                    "video_url": video_signed_url,
                }
                if sc["first"]:
                    # character image sent as file above
                    pass
                orient = node["{}_orient".format(p)].value() if "{}_orient".format(p) in [k.name() for k in node.allKnobs()] else "image"
                fields["character_orientation"] = orient
                keep_snd = "yes" if node["{}_keep_sound".format(p)].value() else "no"
                fields["keep_original_sound"] = keep_snd

                endpoint = "/api/video/kling/v3-0"
                status_endpoint = "/api/video/kling/v3-0/status"
            else:
                fields = {
                    "prompt": prompt,
                    "duration": str(dur),
                    "mode": mode,
                    "aspect_ratio": ar,
                    "sound": "false",
                }
                if video_signed_url:
                    fields["video_ref_url"] = video_signed_url
                    if sc["refer"]:
                        fields["refer_type"] = sc["refer"]

                endpoint = "/api/video/kling/v3-omni"
                status_endpoint = "/api/video/kling/v3-omni/status"

            resp, code = _post_json(endpoint, api_key, fields, files)

            if code != 202 or not resp.get("taskId"):
                def _f():
                    _set_state(nn, CLR_ERROR, "Failed", st_knob,
                               "<b style='color:#c62828'>{}</b>".format(
                                   resp.get("error", "Submit failed")),
                               sub_knob, ld_knob, True, False)
                nuke.executeInMainThread(_f)
                return

            task_id = resp["taskId"]

            def _poll_msg():
                n = nuke.toNode(nn)
                if n:
                    n[st_knob].setValue(
                        "<b style='color:#f0a000'>Rendering video...</b>")
            nuke.executeInMainThread(_poll_msg)

            # Poll — up to 10 min
            for _ in range(120):
                time.sleep(5)
                try:
                    poll = _get_json(status_endpoint, api_key,
                                     {"taskId": task_id})
                except Exception:
                    continue

                st = poll.get("status", "")
                if st == "SUCCEEDED":
                    urls = poll.get("videos", [])
                    if not urls:
                        def _nv():
                            _set_state(nn, CLR_ERROR, "Failed", st_knob,
                                       "<b style='color:#c62828'>No video</b>",
                                       sub_knob, ld_knob, True, False)
                        nuke.executeInMainThread(_nv)
                        return

                    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
                    tmp.close()
                    _download_to_file(urls[0], tmp.name)
                    _results[nn + "_kling"] = {"path": tmp.name, "prompt": prompt}

                    def _ok():
                        _set_state(nn, CLR_READY, "Video Ready", st_knob,
                                   "<b style='color:#4caf50'>Done</b>",
                                   sub_knob, ld_knob, True, True)
                        n = nuke.toNode(nn)
                        if n and n["notify"].value():
                            if nuke.ask("Video ready.\nLoad result now?"):
                                _load_result_kling(nn)
                    nuke.executeInMainThread(_ok)
                    return

                elif st == "FAILED":
                    err = poll.get("error", "Unknown")
                    def _fl(e=err):
                        _set_state(nn, CLR_ERROR, "Failed", st_knob,
                                   "<b style='color:#c62828'>{}</b>".format(e),
                                   sub_knob, ld_knob, True, False)
                    nuke.executeInMainThread(_fl)
                    return

            def _to():
                _set_state(nn, CLR_ERROR, "Timeout", st_knob,
                           "<b style='color:#c62828'>Timed out (10 min)</b>",
                           sub_knob, ld_knob, True, False)
            nuke.executeInMainThread(_to)

        except Exception as e:
            for _, fp in ref_paths:
                try: os.unlink(fp)
                except Exception: pass
            if first_frame_path:
                try: os.unlink(first_frame_path)
                except Exception: pass
            if last_frame_path:
                try: os.unlink(last_frame_path)
                except Exception: pass
            def _err(ex=e):
                _set_state(nn, CLR_ERROR, "Error", st_knob,
                           "<b style='color:#c62828'>Error</b>",
                           sub_knob, ld_knob, True, False)
                nuke.message("CompMeIn Error:\n{}".format(ex))
            nuke.executeInMainThread(_err)

    threading.Thread(target=_work, daemon=True).start()


def _load_kling():
    _load_result_kling(nuke.thisNode().name())


def _load_result_kling(nn):
    data = _results.pop(nn + "_kling", None)
    if not data:
        nuke.message("CompMeIn: No result to load.")
        return
    r = nuke.createNode("Read", inpanel=False)
    r["file"].setValue(data["path"].replace("\\", "/"))
    r["label"].setValue("Kling: {}".format(data["prompt"][:40]))
    # Find the active status knob to reset
    n = nuke.toNode(nn)
    if n:
        n["tile_color"].setValue(CLR_DEFAULT)
        n["label"].setValue("CompMeIn\nVideo")


# ==============================================================================
# Legacy: create_compmein_node (backwards compat — creates Image node)
# ==============================================================================

def create_compmein_node():
    return create_image_node()


# ==============================================================================
# Standalone Settings Panel (menu access)
# ==============================================================================

class SettingsPanel(nukescripts.PythonPanel):
    def __init__(self):
        nukescripts.PythonPanel.__init__(self, "CompMeIn - Settings",
                                         "com.compmein.settings")
        self._api_key = nuke.String_Knob("api_key",
                                          "Nuke API Key (cm_nk_...)")
        self._api_key.setValue(get_api_key())
        self._info = nuke.Text_Knob("info", "",
            "<small>Generate your key at <b>compmein.com/developer</b>"
            " > Nuke API tab.</small>")
        self.addKnob(self._api_key)
        self.addKnob(self._info)

    def knobChanged(self, knob):
        pass

    def showModalDialog(self):
        result = nukescripts.PythonPanel.showModalDialog(self)
        if result:
            s = load_settings()
            s["api_key"] = self._api_key.value().strip()
            save_settings(s)
            nuke.message("CompMeIn: API key saved.")
        return result


def show_settings_panel():
    SettingsPanel().showModalDialog()
