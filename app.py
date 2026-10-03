"""Az Cut web app — narration-synced video builder.

Run:  python app.py   ->  http://127.0.0.1:5000
"""
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid

from flask import Flask, jsonify, request, send_file
from werkzeug.utils import secure_filename  # noqa: F401  (kept for clarity)

from pipeline import (
    PIPELINE_VERSION, TRANSITIONS, MOTIONS, EFFECTS, CAPTION_STYLES, VIDEO_EXTS,
    SFX_KINDS, CAPTION_PRESETS, TOP_PRESETS, CAPTION_FONTS, CAPTION_GRIDS,
    CAPTION_ANIMS, TRANSCRIBE_MODELS,
    CueError, audio_duration, build_video, burn_captions, draft_cues,
    locate_cues, make_auto_clips, parse_cue_times, parse_cues,
    preview_caption_clip, preview_studio_clip, reframe_916, transcribe,
    words_to_srt, words_to_txt,
)

ASPECTS = {
    "16:9": (1280, 720),
    "9:16": (720, 1280),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}
from updater import check_for_updates

FROZEN = getattr(sys, "frozen", False)
if FROZEN:
    # Running from PyInstaller bundle (installed under Program Files):
    # read-only resources live next to the exe, user data goes to LocalAppData.
    BASE = sys._MEIPASS
    _local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    DATA = os.path.join(_local, "AzCut", "data")
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
    DATA = os.path.join(BASE, "data")
STATIC_DIR = os.path.join(BASE, "static")
os.makedirs(DATA, exist_ok=True)

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # 1 GB

audios = {}  # audio_id -> {path, status, result?, error?}
jobs = {}    # job_id   -> {status, durations?, error?}

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".wma", ".opus"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp",
              ".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}


def _save_upload(f, exts):
    ext = os.path.splitext(f.filename or "")[1].lower()
    if ext not in exts:
        raise CueError(f"File type '{ext or '?'}' is not supported.")
    path = os.path.join(DATA, f"{uuid.uuid4().hex}{ext}")
    f.save(path)
    return path


def _transcribe_bg(audio_id):
    try:
        model = audios[audio_id].get("model")
        result = transcribe(audios[audio_id]["path"], model=model)
        audios[audio_id].update(status="ready", result=result)
    except Exception as e:  # noqa: BLE001
        audios[audio_id].update(status="error", error=str(e))


@app.route("/")
def index():
    return app.send_static_file("home.html")


@app.route("/studio")
def studio():
    return app.send_static_file("studio.html")


@app.route("/transcriber")
def page_transcriber():
    return app.send_static_file("transcriber.html")


@app.route("/captions")
def page_captions():
    return app.send_static_file("captions.html")


@app.route("/clipper")
def page_clipper():
    return app.send_static_file("clipper.html")


@app.route("/image-lab")
def page_image_lab():
    return app.send_static_file("image-lab.html")


@app.get("/api/options")
def api_options():
    return jsonify(
        transitions=TRANSITIONS, motions=MOTIONS, effects=EFFECTS,
        caption_styles=CAPTION_STYLES,
        caption_presets={k: v["label"] for k, v in CAPTION_PRESETS.items()},
        top_presets=TOP_PRESETS,
        preset_details={k: {kk: vv for kk, vv in v.items() if kk != "label"}
                        for k, v in CAPTION_PRESETS.items()},
        caption_fonts=CAPTION_FONTS, caption_grids=CAPTION_GRIDS,
        caption_anims=CAPTION_ANIMS,
        transcribe_models=TRANSCRIBE_MODELS,
        sfx_kinds=SFX_KINDS,
        aspects={k: {"w": w, "h": h} for k, (w, h) in ASPECTS.items()})


@app.get("/api/version")
def api_version():
    return jsonify(app="Az Cut", pipeline=PIPELINE_VERSION)


@app.post("/api/audio")
def api_audio():
    f = request.files.get("audio")
    if not f or not f.filename:
        return jsonify(error="Please upload an audio file."), 400
    try:
        path = _save_upload(f, AUDIO_EXTS)
    except CueError as e:
        return jsonify(error=str(e)), 400
    model = request.form.get("model") or None
    if model not in TRANSCRIBE_MODELS:
        model = None
    audio_id = uuid.uuid4().hex
    audios[audio_id] = {"path": path, "status": "transcribing", "model": model}
    threading.Thread(target=_transcribe_bg, args=(audio_id,), daemon=True).start()
    return jsonify(audio_id=audio_id)


@app.get("/api/audio/<audio_id>")
def api_audio_status(audio_id):
    a = audios.get(audio_id)
    if not a:
        return jsonify(error="Audio not found."), 404
    out = {"status": a["status"]}
    if a["status"] == "ready":
        out.update(duration=a["result"]["duration"],
                   text=a["result"]["text"],
                   words=a["result"]["words"])
    elif a["status"] == "error":
        out["error"] = a.get("error", "Transcription fail ho gayi.")
    return jsonify(out)


@app.post("/api/cue-draft")
def api_cue_draft():
    data = request.get_json(force=True, silent=True) or {}
    a = audios.get(data.get("audio_id"))
    if not a or a["status"] != "ready":
        return jsonify(error="Please wait for transcription to finish."), 400
    count = max(1, min(50, int(data.get("count", 3))))
    return jsonify(cues=draft_cues(a["result"]["words"], count))


@app.post("/api/validate")
def api_validate():
    data = request.get_json(force=True, silent=True) or {}
    a = audios.get(data.get("audio_id"))
    if not a or a["status"] != "ready":
        return jsonify(error="Please wait for transcription to finish."), 400
    try:
        cues = parse_cues(data.get("cues", ""))
        starts = locate_cues(a["result"]["words"], cues)
    except CueError as e:
        return jsonify(ok=False, error=str(e))
    return jsonify(ok=True, cues=[
        {"img": n, "start": round(s, 2)} for (n, _), s in zip(cues, starts)
    ])


def _caption_opts(form):
    """Validate caption options from a form dict; return None if disabled."""
    if form.get("cap_on") != "1":
        return None

    def _int(key, default, lo, hi):
        try:
            return max(lo, min(hi, int(form.get(key, default))))
        except (ValueError, TypeError):
            return default

    def _color(key, default):
        v = (form.get(key) or "").strip()
        return v if re.match(r"^#[0-9a-fA-F]{6}$", v) else default

    opts = {
        "preset": form.get("cap_preset", "hormozi"),
        "font": form.get("cap_font", "Montserrat"),
        "size": _int("cap_size", 64, 20, 160),
        "bold": form.get("cap_bold", "1") == "1",
        "color": _color("cap_color", "#FFFFFF"),
        "hl": _color("cap_hl", "#FFE600"),
        "outline": _int("cap_outline", 2, 0, 8),
        "outline_color": _color("cap_oline", "#000000"),
        "shadow": _int("cap_shadow", 1, 0, 8),
        "glow": _color("cap_glow", "") or None,
        "grid": form.get("cap_grid", "bottom-center"),
        "dx": _int("cap_dx", 0, -600, 600),
        "dy": _int("cap_dy", 0, -400, 400),
        "wpl": _int("cap_wpl", 4, 1, 20),
        "lines": 2 if form.get("cap_lines") == "2" else 1,
        "karaoke": form.get("cap_karaoke", "word"),
        "anim": form.get("cap_anim", "pop"),
        "uppercase": form.get("cap_upper") == "1",
        "box": form.get("cap_box") == "1",
    }
    if opts["font"] not in CAPTION_FONTS:
        opts["font"] = "Montserrat"
    return opts
def _render_bg(job_id, audio_id, image_paths, cues_text, width, height,
               transition, motion, effect, captions, sfx_on, sfx_kind,
               sfx_vol, sync_mode):
    try:
        jobs[job_id]["status"] = "building"
        a = audios[audio_id]
        if len(image_paths) < 1:
            raise CueError("Please add at least 1 image.")
        if sync_mode == "timestamps":
            # user-supplied times: no transcription needed
            cues = parse_cue_times(cues_text)
            if len(cues) != len(image_paths):
                raise CueError(
                    f"Cues ({len(cues)}) and images ({len(image_paths)}) "
                    "must be equal in number.")
            starts = [t for _, t in cues]
            if a["status"] == "ready":
                total = a["result"]["duration"]
                words = a["result"]["words"]
            else:
                total = audio_duration(a["path"])
                words = []
        else:
            if a["status"] != "ready":
                raise CueError("Please wait for transcription to finish.")
            cues = parse_cues(cues_text)
            if len(cues) != len(image_paths):
                raise CueError(
                    f"Cues ({len(cues)}) and images ({len(image_paths)}) "
                    "must be equal in number.")
            starts = locate_cues(a["result"]["words"], cues)
            total = a["result"]["duration"]
            words = a["result"]["words"]
        if starts[-1] >= total:
            raise CueError("The last cue is beyond the audio length.")
        out = os.path.join(DATA, f"{job_id}.mp4")
        info = build_video(a["path"], image_paths, starts, out,
                           total_duration=total,
                           width=width, height=height,
                           transition=transition, motion=motion,
                           effect=effect, captions=captions,
                           words=words, sfx_on=sfx_on,
                           sfx_kind=sfx_kind, sfx_vol=sfx_vol)
        jobs[job_id].update(status="done", durations=info["durations"])
    except (CueError, RuntimeError) as e:
        jobs[job_id].update(status="error", error=str(e))
    except Exception as e:  # noqa: BLE001
        jobs[job_id].update(status="error", error=f"Unexpected error: {e}")


@app.post("/api/render")
def api_render():
    audio_id = request.form.get("audio_id")
    cues_text = request.form.get("cues", "")
    sync_mode = request.form.get("sync_mode", "audio")
    if sync_mode not in ("audio", "timestamps"):
        sync_mode = "audio"
    a = audios.get(audio_id)
    if not a:
        return jsonify(error="Please upload audio first."), 400
    if sync_mode == "audio" and a["status"] != "ready":
        return jsonify(error="Please wait for transcription to finish."), 400
    files = [f for f in request.files.getlist("images") if f.filename]
    if not files:
        return jsonify(error="Please add at least 1 image."), 400
    try:
        width = int(request.form.get("width", 1280))
        height = int(request.form.get("height", 720))
    except ValueError:
        width, height = 1280, 720
    if (width, height) not in [(w, h) for w, h in ASPECTS.values()]:
        width, height = 1280, 720
    try:
        image_paths = [_save_upload(f, IMAGE_EXTS) for f in files]
    except CueError as e:
        return jsonify(error=str(e)), 400
    transition = request.form.get("transition", "none")
    motion = request.form.get("motion", "none")
    effect = request.form.get("effect", "none")
    if transition not in TRANSITIONS:
        transition = "none"
    if motion not in MOTIONS:
        motion = "none"
    if effect not in EFFECTS:
        effect = "none"
    captions = _caption_opts(request.form)
    sfx_on = request.form.get("sfx_on", "1") == "1"
    sfx_kind = request.form.get("sfx_kind") or None
    if sfx_kind not in SFX_KINDS:
        sfx_kind = None
    try:
        sfx_vol = max(0.0, min(1.0, float(request.form.get("sfx_vol", 0.5))))
    except (ValueError, TypeError):
        sfx_vol = 0.5
    job_id = uuid.uuid4().hex
    jobs[job_id] = {"status": "queued"}
    threading.Thread(target=_render_bg,
                     args=(job_id, audio_id, image_paths, cues_text, width,
                           height, transition, motion, effect, captions,
                           sfx_on, sfx_kind, sfx_vol, sync_mode),
                     daemon=True).start()
    return jsonify(job_id=job_id)


@app.get("/api/render/<job_id>")
def api_job(job_id):
    j = jobs.get(job_id)
    if not j:
        return jsonify(error="Job not found."), 404
    return jsonify(j)


@app.get("/api/render/<job_id>/preview")
def api_preview(job_id):
    j = jobs.get(job_id)
    if not j or j["status"] != "done":
        return jsonify(error="Video is not ready yet."), 404
    return send_file(os.path.join(DATA, f"{job_id}.mp4"), mimetype="video/mp4")


@app.get("/api/render/<job_id>/download")
def api_download(job_id):
    j = jobs.get(job_id)
    if not j or j["status"] != "done":
        return jsonify(error="Video is not ready yet."), 404
    return send_file(os.path.join(DATA, f"{job_id}.mp4"),
                     as_attachment=True, download_name="azcut-video.mp4")


# ---------------- Phase 1 tools: transcriber export, captions, clipper -------

@app.get("/api/audio/<audio_id>/export")
def api_export(audio_id):
    a = audios.get(audio_id)
    if not a or a["status"] != "ready":
        return jsonify(error="Please wait for transcription to finish."), 400
    fmt = request.args.get("format", "srt").lower()
    words = a["result"]["words"]
    if fmt == "txt":
        data, name = words_to_txt(words), "transcript.txt"
    else:
        data, name = words_to_srt(words), "transcript.srt"
    path = os.path.join(DATA, f"{audio_id}.{fmt}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(data)
    return send_file(path, as_attachment=True, download_name=name)


def _caption_bg(job_id, video_path, opts, model):
    try:
        jobs[job_id]["status"] = "building"
        out = os.path.join(DATA, f"{job_id}.mp4")
        info = burn_captions(video_path, out, opts, model=model)
        jobs[job_id].update(status="done", words=info["words"])
    except (CueError, RuntimeError) as e:
        jobs[job_id].update(status="error", error=str(e))
    except Exception as e:  # noqa: BLE001
        jobs[job_id].update(status="error", error=f"Unexpected error: {e}")


@app.post("/api/caption")
def api_caption():
    f = request.files.get("video")
    if not f or not f.filename:
        return jsonify(error="Please upload a video file."), 400
    try:
        video_path = _save_upload(f, VIDEO_EXTS)
    except CueError as e:
        return jsonify(error=str(e)), 400
    form = dict(request.form)
    form["cap_on"] = "1"  # captions always on for this tool
    opts = _caption_opts(form)
    model = form.get("model") or None
    if model not in TRANSCRIBE_MODELS:
        model = None
    job_id = uuid.uuid4().hex
    jobs[job_id] = {"status": "queued", "kind": "caption"}
    threading.Thread(target=_caption_bg, args=(job_id, video_path, opts, model),
                     daemon=True).start()
    return jsonify(job_id=job_id)


def _job_error(job_id, e):
    if isinstance(e, (CueError, RuntimeError)):
        jobs[job_id].update(status="error", error=str(e))
    else:
        jobs[job_id].update(status="error", error=f"Unexpected error: {e}")


def _caption_preview_bg(job_id, video_path, opts, model):
    try:
        jobs[job_id]["status"] = "building"
        out = os.path.join(DATA, f"{job_id}.mp4")
        preview_caption_clip(video_path, out, opts, model=model)
        jobs[job_id].update(status="done")
    except Exception as e:  # noqa: BLE001
        _job_error(job_id, e)


@app.post("/api/caption-preview")
def api_caption_preview():
    """Burn captions on a 5s sample of the uploaded video (live preview)."""
    f = request.files.get("video")
    if not f or not f.filename:
        return jsonify(error="Please upload a video file."), 400
    try:
        video_path = _save_upload(f, VIDEO_EXTS)
    except CueError as e:
        return jsonify(error=str(e)), 400
    form = dict(request.form)
    form["cap_on"] = "1"
    opts = _caption_opts(form)
    model = form.get("model") or None
    if model not in TRANSCRIBE_MODELS:
        model = None
    job_id = uuid.uuid4().hex
    jobs[job_id] = {"status": "queued", "kind": "caption-preview"}
    threading.Thread(target=_caption_preview_bg,
                     args=(job_id, video_path, opts, model), daemon=True).start()
    return jsonify(job_id=job_id)


def _studio_preview_bg(job_id, audio_id, image_path, opts):
    try:
        jobs[job_id]["status"] = "building"
        a = audios.get(audio_id)
        if not a or a["status"] != "ready":
            raise CueError("Please wait for transcription to finish first.")
        out = os.path.join(DATA, f"{job_id}.mp4")
        preview_studio_clip(a["path"], image_path, out, opts,
                            a["result"]["words"])
        jobs[job_id].update(status="done")
    except Exception as e:  # noqa: BLE001
        _job_error(job_id, e)


@app.post("/api/studio-preview")
def api_studio_preview():
    """Render a 5s studio sample (first image + start of audio) with captions."""
    audio_id = request.form.get("audio_id")
    a = audios.get(audio_id)
    if not a:
        return jsonify(error="Please upload audio first."), 400
    f = request.files.get("image")
    if not f or not f.filename:
        return jsonify(error="Please pick one image for the preview."), 400
    try:
        image_path = _save_upload(f, IMAGE_EXTS)
    except CueError as e:
        return jsonify(error=str(e)), 400
    form = dict(request.form)
    form["cap_on"] = "1"
    opts = _caption_opts(form)
    job_id = uuid.uuid4().hex
    jobs[job_id] = {"status": "queued", "kind": "studio-preview"}
    threading.Thread(target=_studio_preview_bg,
                     args=(job_id, audio_id, image_path, opts), daemon=True).start()
    return jsonify(job_id=job_id)


def _autoclip_bg(job_id, video_path, n_clips, clip_dur, cam, cap_opts, model):
    try:
        jobs[job_id]["status"] = "building"
        out_dir = os.path.join(DATA, job_id)
        os.makedirs(out_dir, exist_ok=True)
        paths = make_auto_clips(video_path, out_dir, n_clips=n_clips,
                                clip_dur=clip_dur, cam=cam,
                                captions=cap_opts, model=model)
        jobs[job_id].update(status="done", clips=paths)
    except Exception as e:  # noqa: BLE001
        _job_error(job_id, e)


@app.post("/api/autoclip")
def api_autoclip():
    """Find the most engaging clips (any language) and cut them to 9:16."""
    f = request.files.get("video")
    if not f or not f.filename:
        return jsonify(error="Please upload a video file."), 400
    try:
        video_path = _save_upload(f, VIDEO_EXTS)
    except CueError as e:
        return jsonify(error=str(e)), 400
    try:
        n_clips = max(1, min(10, int(request.form.get("n_clips", 3))))
        clip_dur = max(10, min(120, int(request.form.get("clip_dur", 30))))
    except ValueError:
        return jsonify(error="Invalid clip settings."), 400
    cam = request.form.get("cam") or "face"
    if cam not in ("face", "center"):
        cam = "face"
    cap_opts = None
    if request.form.get("cap_on") == "1":
        form = dict(request.form)
        cap_opts = _caption_opts(form)
    model = request.form.get("model") or None
    if model not in TRANSCRIBE_MODELS:
        model = None
    job_id = uuid.uuid4().hex
    jobs[job_id] = {"status": "queued", "kind": "autoclip"}
    threading.Thread(target=_autoclip_bg,
                     args=(job_id, video_path, n_clips, clip_dur, cam,
                           cap_opts, model), daemon=True).start()
    return jsonify(job_id=job_id)


@app.get("/api/clips/<job_id>")
def api_clips_status(job_id):
    j = jobs.get(job_id)
    if not j:
        return jsonify(error="Unknown job."), 404
    out = {"status": j["status"], "kind": j.get("kind")}
    if j["status"] == "done":
        out["clips"] = list(range(1, len(j.get("clips", [])) + 1))
    if j["status"] == "error":
        out["error"] = j.get("error")
    return jsonify(out)


@app.get("/api/clips/<job_id>/<int:n>/preview")
def api_clip_preview(job_id, n):
    j = jobs.get(job_id)
    clips = (j or {}).get("clips") or []
    if not (1 <= n <= len(clips)) or not os.path.exists(clips[n - 1]):
        return jsonify(error="Clip not found."), 404
    return send_file(clips[n - 1], mimetype="video/mp4")


@app.get("/api/clips/<job_id>/download")
def api_clips_download(job_id):
    j = jobs.get(job_id)
    clips = (j or {}).get("clips") or []
    if j["status"] != "done" or not clips:
        return jsonify(error="Clips are not ready yet."), 400
    import zipfile
    zpath = os.path.join(DATA, f"clips_{job_id}.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for i, p in enumerate(clips, 1):
            z.write(p, f"clip{i}.mp4")
    return send_file(zpath, as_attachment=True,
                     download_name="azcut_clips.zip")


def _clipper_bg(job_id, video_path):
    try:
        jobs[job_id]["status"] = "building"
        out = os.path.join(DATA, f"{job_id}.mp4")
        info = reframe_916(video_path, out)
        jobs[job_id].update(status="done", faces=info["faces"])
    except (CueError, RuntimeError) as e:
        jobs[job_id].update(status="error", error=str(e))
    except Exception as e:  # noqa: BLE001
        jobs[job_id].update(status="error", error=f"Unexpected error: {e}")


@app.post("/api/clipper")
def api_clipper():
    f = request.files.get("video")
    if not f or not f.filename:
        return jsonify(error="Please upload a video file."), 400
    try:
        video_path = _save_upload(f, VIDEO_EXTS)
    except CueError as e:
        return jsonify(error=str(e)), 400
    job_id = uuid.uuid4().hex
    jobs[job_id] = {"status": "queued", "kind": "clipper"}
    threading.Thread(target=_clipper_bg, args=(job_id, video_path),
                     daemon=True).start()
    return jsonify(job_id=job_id)


# ---------------- one-click auto-update ----------------
_update_state = {"state": "idle", "percent": 0, "message": "", "latest_version": ""}
_last_update_check = None


@app.get("/api/check-update")
def api_check_update():
    global _last_update_check
    _last_update_check = check_for_updates(PIPELINE_VERSION)
    return jsonify(_last_update_check)


@app.get("/api/update-status")
def api_update_status():
    return jsonify(_update_state)


def _do_update(url, asset_name):
    try:
        _update_state.update(state="downloading", percent=0,
                             message="Update download ho raha hai...")
        tmp = os.environ.get("TEMP") or tempfile.gettempdir()
        dest = os.path.join(tmp, asset_name)
        req = urllib.request.Request(url, headers={"User-Agent": "AzCut-Updater"})
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = r.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    _update_state["percent"] = int(done * 100 / total)
        _update_state.update(state="downloaded", percent=100,
                             message="Download mukammal!")
        if not FROZEN:
            _update_state["message"] = f"File tayyar: {dest} - ise khud chalayein."
            return
        # frozen (installed) mode: silent-install via helper bat, then restart
        exe_dir = os.path.dirname(sys.executable)
        bat = os.path.join(tmp, "azcut_update.bat")
        with open(bat, "w") as f:
            f.write("@echo off\n"
                    "timeout /t 4 /nobreak >nul\n"
                    "taskkill /F /IM AzCut.exe >nul 2>nul\n"
                    f'"{dest}" /VERYSILENT /SUPPRESSMSGBOXES\n'
                    f'start "" "{exe_dir}\\AzCut.exe"\n')
        _update_state.update(state="installing",
                             message="Installer chal raha hai...")
        popen_kw = {}
        if os.name == "nt":
            popen_kw["creationflags"] = (subprocess.DETACHED_PROCESS
                                         | subprocess.CREATE_NO_WINDOW)
        subprocess.Popen(["cmd", "/c", bat], **popen_kw)
        time.sleep(2)
        os._exit(0)
    except Exception as e:
        _update_state.update(state="error", message=str(e)[:200])


@app.post("/api/start-update")
def api_start_update():
    if _update_state["state"] == "downloading":
        return jsonify(started=True)
    info = _last_update_check or check_for_updates(PIPELINE_VERSION)
    if not info.get("ok") or not info.get("update_available"):
        return jsonify(error="No new update found."), 404
    _update_state.update(state="downloading", percent=0, message="",
                         latest_version=info["latest_version"])
    threading.Thread(target=_do_update,
                     args=(info["download_url"], info["asset_name"]),
                     daemon=True).start()
    return jsonify(started=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  Az Cut chal raha hai: http://127.0.0.1:{port}\n"
          "  Band karne ke liye Ctrl+C dabayein.\n")
    if os.environ.get("NARRASYNC_NO_BROWSER") != "1":
        import webbrowser
        from threading import Timer
        Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.run(host="127.0.0.1", port=port, threaded=True)
