"""NarrAsync core: narration transcription + script-cued timeline assembly."""

PIPELINE_VERSION = "1.1"

import os
import re
import shutil
import subprocess

import numpy as np

MODEL_NAME = os.environ.get("NARRASYNC_MODEL", "base")

TRANSCRIBE_MODELS = {
    "tiny": "Fast (tiny)",
    "base": "Balanced (base)",
    "small": "Accurate (small)",
}

_models = {}


def get_model(name=None):
    name = name or MODEL_NAME
    # name can be a model id ('tiny'/'base'/'small') or a local folder path
    # (the frozen installer bundles the model and points NARRASYNC_MODEL at it)
    if name not in TRANSCRIBE_MODELS and not os.path.isdir(name):
        name = "base"
    if name not in _models:
        from faster_whisper import WhisperModel
        _models[name] = WhisperModel(name, device="cpu", compute_type="int8")
    return _models[name]


def decode_audio(path):
    """Decode any audio file to mono 16kHz float32 PCM via ffmpeg."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path,
         "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


def audio_duration(path):
    """Duration in seconds via ffmpeg decode (no ffprobe needed)."""
    return len(decode_audio(path)) / 16000


def transcribe(path, model=None):
    """Return {'words': [{word, start, end}], 'duration': seconds, 'text': str}.

    model: 'tiny' (fast), 'base' (balanced), 'small' (accurate).
    """
    audio = decode_audio(path)
    m = get_model(model)
    segments, _info = m.transcribe(audio, word_timestamps=True)
    # NOTE: keep full float precision here (no rounding) so adjacent cues
    # never collapse to the same timestamp; rounding is for display only.
    words = [
        {"word": w.word, "start": w.start, "end": w.end}
        for seg in segments for w in (seg.words or [])
    ]
    return {
        "words": words,
        # full precision: cue math needs exact values; rounding is for display only
        "duration": len(audio) / 16000,
        "text": "".join(w["word"] for w in words).strip(),
    }


def norm(w):
    return re.sub(r"[^a-z0-9]", "", w.lower())


class CueError(Exception):
    pass


def parse_cues(text):
    """Parse '[imgN] sentence...' lines; require consecutive numbering from 1."""
    cues = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"\[img(\d+)\]\s*(.*\S)", line)
        if not m:
            raise CueError(f"Line {lineno}: must be in '[imgN] sentence...' format.")
        cues.append((int(m.group(1)), m.group(2)))
    if not cues:
        raise CueError("No cues found. Write your script in '[img1] ...' format.")
    cues.sort(key=lambda c: c[0])
    if [n for n, _ in cues] != list(range(1, len(cues) + 1)):
        raise CueError("Cues must be numbered [img1], [img2], ... in order (no missing numbers).")
    return cues


def _word_edit(a, b):
    """Levenshtein distance between two word lists."""
    prev = list(range(len(b) + 1))
    for x in a:
        cur = [prev[0] + 1]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def _find_anchor(toks, anchor, prev_pos):
    """Find the anchor phrase in the transcript after prev_pos.

    Uses word-level edit distance over tight windows so the match must be
    (near-)contiguous: scattered words across the transcript don't count.
    Tolerates small transcription differences ('reports' vs 'report').
    """
    max_dist = 0 if len(anchor) <= 3 else (1 if len(anchor) <= 4 else 2)
    best, best_i = None, None
    for i in range(prev_pos + 1, len(toks)):
        for L in range(len(anchor), len(anchor) + 3):
            window = toks[i:i + L]
            if len(window) < len(anchor):
                continue
            d = _word_edit(anchor, window)
            if best is None or d < best:
                best, best_i = d, i
                if d == 0:
                    break
        if best == 0:
            break
    if best is not None and best <= max_dist:
        return best_i
    return None


def locate_cues(words, cues):
    """Map each cue to a start time via fuzzy anchor matching.

    The anchor (first ~6 words after the marker) is located in the
    transcript with LCS scoring, so small transcription differences
    (e.g. 'reports' heard as 'report') still match.
    """
    flat = [(i, norm(w["word"])) for i, w in enumerate(words)]
    flat = [(i, t) for i, t in flat if t]
    idx_of = [i for i, _ in flat]
    toks = [t for _, t in flat]
    starts = []
    prev_pos = -1  # each cue must be found AFTER the previous one
    for n, text in cues:
        anchor = [t for t in (norm(w) for w in text.split()) if t][:6]
        if not anchor:
            raise CueError(f"[img{n}] has no words after it.")
        pos = _find_anchor(toks, anchor, prev_pos)
        if pos is None:
            raise CueError(
                f"[img{n}] was not found in the transcript after the previous cue. "
                f"Make the cue words clearer: '{' '.join(anchor[:5])}...'")
        prev_pos = pos
        starts.append(words[idx_of[pos]]["start"])
    return starts


def parse_cue_times(text):
    """Parse '[imgN] mm:ss[.s] ...' lines for timestamp sync mode.

    Returns [(img_number, seconds)]. Numbers must be 1..N consecutive and
    times strictly increasing.
    """
    cues = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"\[img(\d+)\]\s*(\d+):(\d+(?:\.\d+)?)\b", line)
        if not m:
            raise CueError(
                f"Line {lineno}: must be in '[imgN] m:ss ...' format. "
                f"Example: [img1] 0:05 intro")
        secs = int(m.group(2)) * 60 + float(m.group(3))
        cues.append((int(m.group(1)), secs))
    if not cues:
        raise CueError("No cues found. Write in '[img1] 0:05 ...' format.")
    cues.sort(key=lambda c: c[0])
    if [n for n, _ in cues] != list(range(1, len(cues) + 1)):
        raise CueError("Cues must be numbered [img1], [img2], ... in order.")
    times = [t for _, t in cues]
    if any(b - a <= 0 for a, b in zip(times, times[1:])):
        raise CueError("Timestamps must keep increasing (each one later than the previous).")
    return cues


def draft_cues(words, count):
    """Split the transcript into `count` parts and draft '[imgN] ...' cues."""
    sents, cur = [], []
    for w in words:
        cur.append(w)
        if re.search(r"[.!?\u2026:;]$", w["word"].strip()):
            sents.append(cur)
            cur = []
    if cur:
        sents.append(cur)
    if not sents:
        sents = [words]
    total = sum(len(s) for s in sents)
    target = total / max(count, 1)
    groups, g = [], []
    for s in sents:
        g.append(s)
        acc = sum(len(x) for x in g)
        if len(groups) < count - 1 and acc >= target * (len(groups) + 1):
            groups.append(g)
            g = []
    if g:
        groups.append(g)
    while len(groups) < count:
        i = max(range(len(groups)), key=lambda k: sum(len(s) for s in groups[k]))
        flat_g = [w for s in groups[i] for w in s]
        half = len(flat_g) // 2
        if half == 0:
            break
        groups[i:i + 1] = [[flat_g[:half]], [flat_g[half:]]]
    lines = []
    for i, grp in enumerate(groups[:count], 1):
        head = "".join(w["word"] for s in grp for w in s)[:90].strip()
        lines.append(f"[img{i}] {head} ...")
    return "\n".join(lines)


VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}


# ----------------------------------------------------------------------------
# Style engine: transitions, keyframe motions, effects, captions
# ----------------------------------------------------------------------------

TRANSITIONS = {
    "dissolve": "Dissolve (Fade)",
    "fadeblack": "Fade to Black",
    "wipeleft": "Wipe Left",
    "slideleft": "Slide Left",
    "distance": "Zoom (Distance)",
    "circleopen": "Circle Open (Iris)",
    "hblur": "Blur",
    "pixelize": "Glitch (Pixelize)",
    "fadewhite": "Flash White",
}

MOTIONS = {
    "zoomin": "Zoom In",
    "zoomout": "Zoom Out",
    "panleft": "Pan Left",
    "panright": "Pan Right",
    "panup": "Pan Up",
    "pandown": "Pan Down",
}

EFFECTS = {
    "grain": "Film Grain",
    "vhs": "VHS Retro",
    "rgbsplit": "RGB Split",
    "shake": "Camera Shake",
    "blurfocus": "Blur Focus-in",
}

CAPTION_STYLES = {
    "karaoke": "Karaoke (word highlight)",
    "standard": "Standard",
    "pop": "Pop (word by word)",
}

# ----------------------------------------------------------------------------
# Caption preset engine (v1.0): 30 ready templates, CapCut-style.
# Each preset overrides _CAP_DEFAULTS. resolve_caption_opts() merges a preset
# with the user's own tweaks into the final dict build_ass_file() renders.
# ----------------------------------------------------------------------------

CAPTION_FONTS = [
    "Arial", "Arial Black", "Montserrat", "Poppins", "Inter", "Oswald",
    "Bebas Neue", "Anton", "Roboto", "Impact", "Verdana", "Tahoma",
    "Trebuchet MS", "Consolas", "Merriweather",
    # Urdu / Arabic script fonts (used if installed; falls back to Arial)
    "Noto Nastaliq Urdu", "Noto Naskh Arabic", "Noto Sans Arabic", "Amiri",
    "Aref Ruqaa", "Gulzar", "Jameel Noori Nastaleeq", "Scheherazade New",
]

CAPTION_GRIDS = {
    "top-left": "Top Left", "top-center": "Top Center", "top-right": "Top Right",
    "middle-left": "Middle Left", "middle-center": "Middle Center",
    "middle-right": "Middle Right",
    "bottom-left": "Bottom Left", "bottom-center": "Bottom Center",
    "bottom-right": "Bottom Right",
}

CAPTION_ANIMS = {
    "none": "Instant Cut",
    "fade": "Smooth Fade (In & Out)",
    "pop": "Pop Zoom (Punch In)",
    "slide_up": "Slide Up & Rise",
    "slide_down": "Slide Down",
}

_CAP_DEFAULTS = dict(
    font="Montserrat", bold=True, size=64,
    color="#FFFFFF", hl="#FFE600",
    outline=2, outline_color="#000000", shadow=1, glow=None,
    grid="bottom-center", dx=0, dy=0,
    wpl=4, lines=1, karaoke="word", anim="pop", uppercase=False, box=False,
)


def _P(**kw):
    d = dict(_CAP_DEFAULTS)
    d.update(kw)
    return d


CAPTION_PRESETS = {
    # ---- Top 10 viral templates ----
    "hormozi": _P(label="Hormozi Pop",
                  font="Montserrat", size=68, color="#FFFFFF", hl="#FFE600",
                  outline=2, wpl=3, anim="pop", grid="bottom-center"),
    "mrbeast": _P(label="Beast Bold",
                  font="Arial Black", size=72, color="#FFFFFF", hl="#FF3B30",
                  outline=3, wpl=4, anim="pop"),
    "karaoke_pop": _P(label="Moving Karaoke Pop",
                      font="Poppins", size=64, color="#FFFFFF", hl="#00E676",
                      outline=2, wpl=3, anim="pop", karaoke="word"),
    "minimal": _P(label="Minimal Clean",
                  font="Inter", bold=False, size=54, color="#FFFFFF", hl="#FFFFFF",
                  outline=0, shadow=1, wpl=6, anim="fade", karaoke="none"),
    "neon": _P(label="Neon Glow",
               font="Montserrat", size=64, color="#00F0FF", hl="#FFFFFF",
               outline=0, glow="#00F0FF", shadow=0, wpl=4, anim="fade"),
    "outline_punch": _P(label="Outline Punch",
                        font="Oswald", size=76, color="#FFFFFF", hl="#FFFFFF",
                        outline=4, outline_color="#111111", wpl=3, anim="pop"),
    "typewriter": _P(label="Typewriter",
                     font="Consolas", size=56, color="#E8E8E8", hl="#E8E8E8",
                     outline=1, wpl=8, anim="fade", karaoke="reveal"),
    "ticker": _P(label="News Ticker",
                 font="Arial", size=52, color="#FFFFFF", hl="#FFD60A", box=True,
                 outline=0, shadow=0, wpl=8, anim="none", karaoke="none"),
    "cinema": _P(label="Cinematic",
                 font="Oswald", size=58, color="#FFF8E7", hl="#FFC93C",
                 outline=1, shadow=2, wpl=5, anim="fade", dy=-30),
    "glitch": _P(label="Glitch Pop",
                 font="Arial Black", size=66, color="#FFFFFF", hl="#FF2FB3",
                 outline=2, outline_color="#00E5FF", wpl=3, anim="pop"),
    # ---- More styles ----
    "luxury": _P(label="Luxury Gold",
                 font="Merriweather", size=60, color="#F5E6C8", hl="#D4AF37",
                 outline=1, shadow=2, wpl=5, anim="fade"),
    "sport": _P(label="Sport Energy",
                font="Oswald", size=70, color="#FFFFFF", hl="#FF6B00",
                outline=3, wpl=3, anim="slide_up"),
    "comic": _P(label="Comic Punch",
                font="Arial Black", size=66, color="#FFE93C", hl="#FFFFFF",
                outline=3, outline_color="#D32F2F", wpl=3, anim="pop"),
    "retro": _P(label="Retro Wave",
                font="Impact", size=68, color="#FF9E00", hl="#00E5FF",
                outline=2, wpl=4, anim="fade"),
    "fire": _P(label="Fire Words",
               font="Montserrat", size=68, color="#FFFFFF", hl="#FF5722",
               outline=2, glow="#FF5722", wpl=3, anim="pop"),
    "ocean": _P(label="Ocean Calm",
                font="Poppins", bold=False, size=58, color="#E0F7FA", hl="#4DD0E1",
                outline=1, wpl=6, anim="fade"),
    "mono": _P(label="Mono Code",
               font="Consolas", size=54, color="#C8FFC8", hl="#FFFF00",
               outline=0, shadow=0, wpl=8, anim="none", karaoke="word"),
    "soft_shadow": _P(label="Soft Shadow",
                      font="Inter", size=60, color="#FFFFFF", hl="#FFFFFF",
                      outline=0, shadow=4, wpl=5, anim="fade", karaoke="none"),
    "upper_punch": _P(label="Uppercase Punch",
                      font="Oswald", size=64, color="#FFFFFF", hl="#FFE600",
                      outline=2, wpl=4, anim="pop", uppercase=True),
    "gentle": _P(label="Gentle Fade",
                 font="Poppins", bold=False, size=56, color="#FFFFFF", hl="#FFE0B2",
                 outline=1, wpl=7, anim="fade", karaoke="none"),
    # ---- Urdu / Arabic ----
    "urdu_nastaliq": _P(label="Urdu Nastaliq",
                        font="Noto Nastaliq Urdu", size=60, color="#FFFFFF", hl="#FFE600",
                        outline=2, wpl=5, anim="fade"),
    "urdu_naskh": _P(label="Urdu Naskh",
                     font="Noto Naskh Arabic", size=62, color="#FFFFFF", hl="#7CFC00",
                     outline=2, wpl=5, anim="fade"),
    "urdu_modern": _P(label="Urdu Modern",
                      font="Noto Sans Arabic", size=60, color="#FFFFFF", hl="#00E5FF",
                      outline=2, wpl=5, anim="pop"),
    "urdu_ruqaa": _P(label="Ruqaa Style",
                     font="Aref Ruqaa", size=64, color="#FFF3D6", hl="#FFB300",
                     outline=1, shadow=2, wpl=5, anim="fade"),
    "urdu_gulzar": _P(label="Gulzar Display",
                      font="Gulzar", size=66, color="#FFFFFF", hl="#FF4081",
                      outline=2, wpl=4, anim="pop"),
    # ---- Fun & utility ----
    "bubble": _P(label="Bubble Box",
                 font="Poppins", size=58, color="#1A1A2E", hl="#1A1A2E", box=True,
                 outline=0, shadow=0, wpl=5, anim="pop", karaoke="none"),
    "karaoke_classic": _P(label="Classic Karaoke",
                          font="Arial", size=60, color="#FFFFFF", hl="#0000FF",
                          outline=2, wpl=6, anim="none", karaoke="word"),
    "marker": _P(label="Marker Highlight",
                 font="Montserrat", size=62, color="#111111", hl="#111111",
                 outline=0, shadow=0, box=True, wpl=4, anim="pop", karaoke="none"),
    "tiny_top": _P(label="Tiny Top",
                   font="Inter", size=40, color="#FFFFFF", hl="#FFFFFF",
                   outline=1, wpl=8, anim="fade", karaoke="none", grid="top-center"),
    "big_center": _P(label="Big Center",
                     font="Montserrat", size=84, color="#FFFFFF", hl="#FFE600",
                     outline=3, wpl=3, anim="pop", grid="middle-center"),
    # ---- Professional guide presets (v1.1) ----
    "cine_pro": _P(label="Cinematic Pro",
                   font="Montserrat", size=62, color="#FFFFFF", hl="#FFE600",
                   outline=2, shadow=2, wpl=6, lines=2, anim="fade",
                   grid="bottom-center", dy=-40, karaoke="word"),
    "action_impact": _P(label="Action Impact",
                        font="Anton", size=78, color="#FFFFFF", hl="#FF3B30",
                        outline=3, wpl=3, anim="pop", uppercase=True,
                        grid="middle-center"),
    "story_fade": _P(label="Story Fade",
                     font="Poppins", bold=False, size=58, color="#FFFFFF", hl="#FFE0B2",
                     outline=1, shadow=1, wpl=7, lines=2, anim="fade",
                     karaoke="none", dy=-40),
    "danger_red": _P(label="Danger Red",
                     font="Montserrat", size=68, color="#FFFFFF", hl="#FF3B30",
                     outline=2, wpl=3, anim="pop", uppercase=True),
    "center_moment": _P(label="Center Moment",
                        font="Montserrat", size=76, color="#FFFFFF", hl="#FFE600",
                        outline=3, wpl=4, anim="fade", grid="middle-center"),
    "lower_third": _P(label="Lower Third Safe",
                      font="Inter", size=56, color="#FFFFFF", hl="#FFE600",
                      outline=2, shadow=2, wpl=6, lines=2, anim="fade",
                      grid="bottom-center", dy=-90,
                      karaoke="word"),
    "docu": _P(label="Documentary",
               font="Inter", bold=False, size=56, color="#F5F5F5", hl="#FFFFFF",
               outline=1, shadow=2, wpl=8, lines=2, anim="fade",
               karaoke="none", dy=-40),
    "gold_title": _P(label="Gold Title",
                     font="Merriweather", size=72, color="#FFFFFF", hl="#D4AF37",
                     outline=2, shadow=3, wpl=4, anim="fade",
                     grid="middle-center", uppercase=True),
}

TOP_PRESETS = ["hormozi", "mrbeast", "karaoke_pop", "minimal", "neon",
               "outline_punch", "typewriter", "ticker", "cinema", "glitch"]


def resolve_caption_opts(opts):
    """Merge a preset id + user tweaks into the final caption dict."""
    opts = dict(opts or {})
    preset_id = opts.pop("preset", "hormozi")
    base = dict(_CAP_DEFAULTS)
    if preset_id in CAPTION_PRESETS:
        p = CAPTION_PRESETS[preset_id]
        base.update({k: v for k, v in p.items() if k != "label"})
    # legacy keys from older UI versions
    if "font_size" in opts:
        opts["size"] = opts.pop("font_size")
    if "hl_color" in opts:
        opts["hl"] = opts.pop("hl_color")
    if "style" in opts and "karaoke" not in opts:
        opts["karaoke"] = {"karaoke": "word", "pop": "reveal"}.get(opts.pop("style"), "none")
    if "layout" in opts:
        lay = opts.pop("layout")
        if lay == "two" and "lines" not in opts:
            opts["lines"] = 2
        if "wpl" not in opts:
            opts["wpl"] = 4
    if "position" in opts and "grid" not in opts:
        pos = opts.pop("position")
        opts["grid"] = {"top": "top-center", "middle": "middle-center"}.get(pos, "bottom-center")
    base.update(opts)
    # sanitize
    base["size"] = max(20, min(160, int(base.get("size", 64))))
    base["wpl"] = max(1, min(20, int(base.get("wpl", 4))))
    base["outline"] = max(0, min(8, int(base.get("outline", 2))))
    base["shadow"] = max(0, min(8, int(base.get("shadow", 1))))
    base["dx"] = max(-600, min(600, int(base.get("dx", 0))))
    base["dy"] = max(-400, min(400, int(base.get("dy", 0))))
    base["lines"] = 2 if int(base.get("lines", 1)) >= 2 else 1
    if base.get("grid") not in CAPTION_GRIDS:
        base["grid"] = "bottom-center"
    if base.get("anim") not in CAPTION_ANIMS:
        base["anim"] = "pop"
    if base.get("karaoke") not in ("word", "reveal", "none"):
        base["karaoke"] = "word"
    return base

# ----------------------------------------------------------------------------
# Procedural sound effects (synthesized with numpy, no audio files needed).
# Each transition type gets a matching SFX, auto-placed at cut points.
# ----------------------------------------------------------------------------
SFX_SR = 44100

TRANSITION_SFX = {
    "dissolve": "soft_whoosh",    # gentle airy sweep
    "fadeblack": "drop",          # deep falling sweep
    "wipeleft": "whoosh",         # classic whoosh
    "slideleft": "whoosh",
    "distance": "riser",          # rising sweep for zoom
    "circleopen": "pop",          # iris pop
    "hblur": "soft_whoosh",
    "pixelize": "glitch",         # digital glitch blips
    "fadewhite": "flash",         # camera flash snap
}


def _sfx_env(n, attack=0.12):
    """Raised-cosine attack/decay envelope."""
    a = max(1, int(n * attack))
    env = np.empty(n, dtype=np.float32)
    env[:a] = 0.5 - 0.5 * np.cos(np.pi * np.arange(a) / a)
    tail = np.arange(n - a)
    env[a:] = 0.5 + 0.5 * np.cos(np.pi * tail / max(1, n - a))
    return env


def _sfx_chirp(dur, f0, f1, gain=0.6):
    """Sine sweep from f0 to f1 Hz."""
    n = int(dur * SFX_SR)
    t = np.arange(n) / SFX_SR
    f = f0 + (f1 - f0) * (t / dur)
    phase = 2 * np.pi * np.cumsum(f) / SFX_SR
    return (gain * np.sin(phase) * _sfx_env(n)).astype(np.float32)


def _sfx_noise(dur, seed=7, gain=0.5, attack=0.15):
    """Shaped white-noise burst (whoosh body)."""
    n = int(dur * SFX_SR)
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n).astype(np.float32)
    # swept one-pole lowpass -> rising brightness
    fc = np.linspace(500, 6000, n)
    a = 1 - np.exp(-2 * np.pi * fc / SFX_SR)
    y = np.empty(n, dtype=np.float32)
    acc = 0.0
    for i in range(n):
        acc += a[i] * (x[i] - acc)
        y[i] = acc
    y /= max(1e-6, np.max(np.abs(y)))
    return (gain * y * _sfx_env(n, attack)).astype(np.float32)


def _sfx_tone(dur, freq, decay, gain=0.6):
    """Decaying sine (pop / thump)."""
    n = int(dur * SFX_SR)
    t = np.arange(n) / SFX_SR
    return (gain * np.sin(2 * np.pi * freq * t) * np.exp(-t * decay)
            * _sfx_env(n, attack=0.02)).astype(np.float32)


def _sfx_glitch(dur=0.55, seed=21):
    """Random digital blips."""
    n = int(dur * SFX_SR)
    y = np.zeros(n, dtype=np.float32)
    rng = np.random.default_rng(seed)
    pos = 0
    while pos < n - 100:
        blen = int(rng.integers(int(0.02 * SFX_SR), int(0.07 * SFX_SR)))
        freq = float(rng.uniform(300, 3200))
        t = np.arange(blen) / SFX_SR
        blip = np.sin(2 * np.pi * freq * t) * _sfx_env(blen, attack=0.3)
        end = min(n, pos + blen)
        y[pos:end] += blip[:end - pos] * 0.55
        pos += blen + int(rng.integers(int(0.01 * SFX_SR), int(0.06 * SFX_SR)))
    return y


def _sfx_ting(dur=0.9, seed=31):
    """Bright bell-like 'ting' (sine + harmonic, long decay)."""
    n = int(dur * SFX_SR)
    t = np.arange(n) / SFX_SR
    y = (np.sin(2 * np.pi * 1568 * t) * 0.6
         + np.sin(2 * np.pi * 2093 * t) * 0.3
         + np.sin(2 * np.pi * 2637 * t) * 0.15)
    return (0.5 * y * np.exp(-t * 5.5) * _sfx_env(n, attack=0.01)).astype(np.float32)


def _sfx_shutter():
    """Camera shutter: two mechanical clicks."""
    n = int(0.35 * SFX_SR)
    y = np.zeros(n, dtype=np.float32)
    for start, seed in ((0, 41), (int(0.09 * SFX_SR), 42)):
        click = _sfx_noise(0.035, seed=seed, gain=0.8, attack=0.02)
        end = min(n, start + len(click))
        y[start:end] += click[:end - start]
    tone = _sfx_tone(0.05, 2400, 60, gain=0.25)
    y[:len(tone)] += tone
    return y


def _sfx_splash(dur=0.9, seed=51):
    """Water splash: noise burst with falling brightness."""
    n = int(dur * SFX_SR)
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n).astype(np.float32)
    fc = np.linspace(6500, 500, n)
    a = 1 - np.exp(-2 * np.pi * fc / SFX_SR)
    y = np.empty(n, dtype=np.float32)
    acc = 0.0
    for i in range(n):
        acc += a[i] * (x[i] - acc)
        y[i] = acc
    y /= max(1e-6, np.max(np.abs(y)))
    return (0.6 * y * _sfx_env(n, attack=0.04)).astype(np.float32)


def _sfx_cap_open():
    """Bottle cap pop: metallic ping + pressure release hiss."""
    n = int(0.4 * SFX_SR)
    y = np.zeros(n, dtype=np.float32)
    ping = _sfx_tone(0.12, 880, 28, gain=0.55)
    y[:len(ping)] += ping
    hiss = _sfx_noise(0.22, seed=61, gain=0.35, attack=0.05)
    y[:len(hiss)] += hiss
    return y


def _sfx_click():
    """Short UI click."""
    n = int(0.07 * SFX_SR)
    y = _sfx_noise(0.07, seed=71, gain=0.5, attack=0.02)
    tone = _sfx_tone(0.05, 1250, 70, gain=0.4)
    y[:len(tone)] += tone
    return y


SFX_KINDS = {
    "soft_whoosh": "Soft Whoosh",
    "whoosh": "Whoosh",
    "riser": "Riser",
    "drop": "Drop",
    "pop": "Pop",
    "ting": "Ting",
    "click": "Click",
    "shutter": "Camera Shutter",
    "splash": "Splash",
    "cap_open": "Bottle Cap Open",
    "glitch": "Glitch",
    "flash": "Flash",
    "thump": "Thump",
    "shimmer": "Shimmer",
}


def synthesize_sfx(kind):
    """Return a float32 mono 44.1kHz SFX buffer for the given kind."""
    if kind == "whoosh":
        return _sfx_noise(0.7, seed=11, gain=0.55)
    if kind == "soft_whoosh":
        return _sfx_noise(0.8, seed=12, gain=0.38, attack=0.25)
    if kind == "riser":
        return _sfx_chirp(0.8, 180, 2600, gain=0.5)
    if kind == "drop":
        return _sfx_chirp(0.7, 1600, 110, gain=0.5)
    if kind == "pop":
        tone = _sfx_tone(0.28, 660, 16, gain=0.6)
        click = _sfx_noise(0.06, seed=13, gain=0.35, attack=0.02)
        tone[:len(click)] += click
        return tone
    if kind == "thump":
        return _sfx_tone(0.5, 58, 9, gain=0.7)
    if kind == "shimmer":
        n = int(0.7 * SFX_SR)
        t = np.arange(n) / SFX_SR
        y = (np.sin(2 * np.pi * 2800 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 26 * t))
             * np.exp(-t * 6) * _sfx_env(n, attack=0.03))
        return (0.4 * y).astype(np.float32)
    if kind == "glitch":
        return _sfx_glitch()
    if kind == "ting":
        return _sfx_ting()
    if kind == "shutter":
        return _sfx_shutter()
    if kind == "splash":
        return _sfx_splash()
    if kind == "cap_open":
        return _sfx_cap_open()
    if kind == "click":
        return _sfx_click()
    if kind == "flash":
        snap = _sfx_noise(0.14, seed=14, gain=0.6, attack=0.02)
        pop = _sfx_tone(0.3, 880, 14, gain=0.45)
        out = np.zeros(max(len(snap), len(pop)), dtype=np.float32)
        out[:len(pop)] += pop
        out[:len(snap)] += snap
        return out
    return _sfx_noise(0.6, seed=99, gain=0.4)


def _write_wav_mono(path, data, sr=SFX_SR):
    """Write float32 mono [-1,1] to 16-bit PCM WAV."""
    import wave
    pcm = (np.clip(data, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def build_sfx_track(transition, bounds, t_trans, out_path, gain=0.5, kind=None):
    """Mix transition SFX at every cut point; return wav path or None.

    kind: explicit SFX_KINDS key (user's choice); otherwise the transition's
    default from TRANSITION_SFX is used.
    """
    kind = kind or TRANSITION_SFX.get(transition)
    if not kind or kind not in SFX_KINDS or len(bounds) < 3:
        return None
    sfx = synthesize_sfx(kind)
    total = bounds[-1]
    mix = np.zeros(int(total * SFX_SR) + len(sfx), dtype=np.float32)
    for i in range(1, len(bounds) - 1):
        # cut i in the final (xfade-overlapped) timeline sits at bounds[i] - t_trans*i
        center = bounds[i] - t_trans * i
        start = int(max(0.0, center - (len(sfx) / SFX_SR) * 0.5) * SFX_SR)
        end = start + len(sfx)
        mix[start:end] += sfx * gain
    _write_wav_mono(out_path, mix)
    return out_path


def _motion_filter(motion, tf, W, H, fps):
    """Ken Burns style motion via zoompan. Input is pre-scaled to 2560x1440."""
    pre = "scale=2560:1440:force_original_aspect_ratio=increase,crop=2560:1440"
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if motion == "zoomin":
        z, x, y = f"1+0.18*on/{tf}", cx, cy
    elif motion == "zoomout":
        z, x, y = f"1.18-0.18*on/{tf}", cx, cy
    elif motion == "panleft":
        z, x, y = "1.3", f"(iw-iw/zoom)*(1-on/{tf})", cy
    elif motion == "panright":
        z, x, y = "1.3", f"(iw-iw/zoom)*on/{tf}", cy
    elif motion == "panup":
        z, x, y = "1.3", cx, f"(ih-ih/zoom)*(1-on/{tf})"
    elif motion == "pandown":
        z, x, y = "1.3", cx, f"(ih-ih/zoom)*on/{tf}"
    else:
        return None
    zp = (f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={fps}")
    return f"{pre},{zp},setsar=1"


def _effect_filter(effect, W, H):
    if effect == "grain":
        return "noise=alls=7:allf=t"
    if effect == "vhs":
        return "noise=alls=5:allf=t,eq=contrast=1.05:saturation=1.35,vignette=angle=PI/5"
    if effect == "rgbsplit":
        return "handled-inline"  # special-cased in build_video (multi-stream)
    if effect == "shake":
        return (f"crop=iw-24:ih-24:'12+7*sin(2*PI*t*3)':"
                f"'12+7*cos(2*PI*t*2.2)',scale={W}:{H}")
    if effect == "blurfocus":
        # NOTE: modern ffmpeg evaluates gblur sigma once (no per-frame 't'),
        # so animate via timeline editing: strong blur -> medium -> light.
        return ("gblur=sigma=8:enable='lt(t,0.5)',"
                "gblur=sigma=4:enable='between(t,0.5,1)',"
                "gblur=sigma=1.5:enable='between(t,1,1.5)'")
    return None


def _ass_time(s):
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    return f"{h}:{m:02d}:{s % 60:05.2f}"


def _hex_to_ass(h):
    h = h.lstrip("#")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def words_to_txt(words):
    """Plain transcript text from word timestamps."""
    return "".join(w["word"] for w in words).strip()


def _srt_time(s):
    ms = int(round(max(0.0, s) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def words_to_srt(words):
    """Group word timestamps into SRT subtitle cues (max ~2 lines each)."""
    cues, cur = [], []
    for w in words:
        cur.append(w)
        dur = w["end"] - cur[0]["start"]
        if len(cur) >= 10 or dur > 4.0 or w["word"].strip().endswith((".", "!", "?", "…")):
            cues.append(cur)
            cur = []
    if cur:
        cues.append(cur)
    out = []
    for i, c in enumerate(cues, 1):
        text = "".join(x["word"] for x in c).strip()
        if len(text) > 42:  # wrap long cues to 2 lines
            sp = text.rfind(" ", 0, 45)
            if sp > 10:
                text = text[:sp] + "\n" + text[sp + 1:]
        out.append(f"{i}\n{_srt_time(c[0]['start'])} --> {_srt_time(c[-1]['end'])}\n{text}\n")
    return "\n".join(out)


def _caption_xy(grid, W, H, dx, dy):
    """Anchor point for the 9-grid + user offsets."""
    row, col = grid.split("-")
    x = {"left": W * 0.25, "center": W / 2, "right": W * 0.75}[col] + dx
    y = {"top": H * 0.16, "middle": H / 2, "bottom": H * 0.84}[row] + dy
    return int(x), int(y)


def _grid_to_ass_align(grid):
    row, col = grid.split("-")
    base = {"left": 1, "center": 2, "right": 3}[col]
    return base + {"bottom": 0, "middle": 3, "top": 6}[row]


def _anim_tags(anim, x, y):
    if anim == "fade":
        return r"{\fad(180,180)}"
    if anim == "pop":
        return r"{\fscx25\fscy25\t(0,280,\fscx100\fscy100)}"
    if anim == "slide_up":
        return r"{\move(%d,%d,%d,%d,0,320)}" % (x, y + 60, x, y)
    if anim == "slide_down":
        return r"{\move(%d,%d,%d,%d,0,320)}" % (x, y - 60, x, y)
    return ""


def _emit_caption_line(events, ws, p, x, y):
    """Emit one ASS dialogue event from a word chunk + resolved preset."""
    s0, s1 = ws[0]["start"], ws[-1]["end"] + 0.25
    upper = (lambda s: s.upper()) if p["uppercase"] else (lambda s: s)

    def karaoke_text(part):
        out = []
        for w in part:
            k = max(1, int(round((w["end"] - w["start"]) * 100)))
            out.append("{\\k%d}%s" % (k, upper(w["word"])))
        return "".join(out)

    def plain_text(part):
        return upper("".join(w["word"] for w in part)).strip()

    halves = [ws]
    if p["lines"] == 2 and len(ws) > 1:
        mid = len(ws) // 2
        halves = [ws[:mid], ws[mid:]]

    tags = r"{\pos(%d,%d)}" % (x, y) + _anim_tags(p["anim"], x, y)
    if p["glow"]:
        tags += r"{\blur2}"
    if p["karaoke"] == "word":
        text = "\\N".join(karaoke_text(h) for h in halves)
        events.append(f"Dialogue: 0,{_ass_time(s0)},{_ass_time(s1)},Cap,,0,0,0,,{tags}{text}")
    elif p["karaoke"] == "reveal":
        for j in range(len(ws)):
            t = plain_text(ws[:j + 1])
            events.append(
                f"Dialogue: 0,{_ass_time(ws[j]['start'])},{_ass_time(s1)},Cap,,0,0,0,,{tags}{t}")
    else:
        text = "\\N".join(plain_text(h) for h in halves)
        events.append(f"Dialogue: 0,{_ass_time(s0)},{_ass_time(s1)},Cap,,0,0,0,,{tags}{text}")


def build_ass_file(words, segments, opts, W, H, path):
    """Write an ASS subtitle file from word timestamps (preset engine v2).

    opts: preset id + tweaks; see resolve_caption_opts().
    segments: [(start, end)] per cue segment.
    """
    p = resolve_caption_opts(opts)
    primary = _hex_to_ass(p["color"])
    secondary = _hex_to_ass(p["hl"])
    outline_c = _hex_to_ass(p["glow"] or p["outline_color"])
    back_c = "&H00000000" if p["box"] else "&H80000000"
    border = 3 if p["box"] else 1
    align = _grid_to_ass_align(p["grid"])
    x, y = _caption_xy(p["grid"], W, H, p["dx"], p["dy"])
    bold = -1 if p["bold"] else 0

    L = ["[Script Info]", "ScriptType: v4.00+",
         f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 2",
         "[V4+ Styles]",
         "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
         "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
         "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
         "Alignment, MarginL, MarginR, MarginV, Encoding",
         f"Style: Cap,{p['font']},{p['size']},{primary},{secondary},{outline_c},"
         f"{back_c},{bold},0,0,0,100,100,0,0,{border},{p['outline']},{p['shadow']},"
         f"{align},20,20,24,1",
         "[Events]",
         "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
         "Effect, Text"]
    for s0, s1 in segments:
        seg_words = [w for w in words if s0 - 0.05 <= w["start"] < s1]
        cur = []
        for w in seg_words:
            cur.append(w)
            if len(cur) >= p["wpl"] or w["word"].strip().endswith((".", "!", "?", "…")):
                _emit_caption_line(L, cur, p, x, y)
                cur = []
        if cur:
            _emit_caption_line(L, cur, p, x, y)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


def build_video(audio_path, image_paths, cue_starts, out_path, total_duration,
                width=1280, height=720, fps=30,
                transition="none", motion="none", effect="none",
                captions=None, words=None, sfx_on=True,
                sfx_kind=None, sfx_vol=0.5):
    """Assemble the final MP4: each image/clip covers its cued segment.

    transition: key of TRANSITIONS (xfade between segments, centered on cues)
    motion: key of MOTIONS (Ken Burns on images; clips stay static)
    effect: key of EFFECTS (applied to the whole video)
    captions: None or caption-opts dict (see resolve_caption_opts)
    words: word-timestamp list (needed for captions)
    sfx_on: auto-place a matching sound effect at each transition cut
    sfx_kind: SFX_KINDS key overriding the transition default (or None)
    sfx_vol: 0.0..1.0 volume for the SFX track
    """
    W, H = width, height
    n = len(image_paths)
    bounds = [0.0] + list(cue_starts[1:]) + [total_duration]
    durs = [bounds[i + 1] - bounds[i] for i in range(n)]
    if any(d <= 0 for d in durs):
        raise CueError("A segment has zero/negative duration — check your cue order.")

    # --- transition overlap: extend inputs so xfades land centered on cues ---
    T = 0.0
    if transition in TRANSITIONS and n > 1:
        T = min(0.5, min(durs) * 0.49)
        if T < 0.05:
            transition = "none"
            T = 0.0
    if T > 0:
        in_durs = [durs[0] + T / 2] + [d + T for d in durs[1:-1]] + [durs[-1] + T / 2]
    else:
        in_durs = list(durs)

    cmd = ["ffmpeg", "-y"]
    for img, d in zip(image_paths, in_durs):
        ext = os.path.splitext(img)[1].lower()
        if ext in VIDEO_EXTS:
            cmd += ["-stream_loop", "-1", "-t", f"{d:.3f}", "-i", img]
        else:
            cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{d:.3f}", "-i", img]
    cmd += ["-i", audio_path]

    # --- auto sound effects at each transition cut ---
    sfx_path = None
    if sfx_on and T > 0:
        sfx_path = build_sfx_track(transition, bounds, T,
                                   out_path + ".sfx.wav",
                                   gain=max(0.0, min(1.0, sfx_vol)),
                                   kind=sfx_kind)
    if sfx_path:
        cmd += ["-i", sfx_path]
        sfx_idx = n + 1

    # --- per-segment video chain ---
    filters = []
    for i, img in enumerate(image_paths):
        ext = os.path.splitext(img)[1].lower()
        is_video = ext in VIDEO_EXTS
        if not is_video and motion in MOTIONS:
            tf = max(1, round(in_durs[i] * fps))
            chain = _motion_filter(motion, tf, W, H, fps)
        elif is_video:
            chain = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                     f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},"
                     f"trim=duration={in_durs[i]:.3f},setpts=PTS-STARTPTS")
        else:
            chain = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                     f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}")
        filters.append(f"[{i}:v]{chain}[v{i}]")

    # --- join segments: xfade chain or plain concat ---
    if T > 0:
        cur = "[v0]"
        offset = in_durs[0] - T
        for i in range(1, n):
            out = f"[x{i}]"
            filters.append(
                f"{cur}[v{i}]xfade=transition={transition}:"
                f"duration={T:.3f}:offset={offset:.3f}{out}")
            cur = out
            offset += in_durs[i] - T
        vmix = cur
    else:
        filters.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[vmix]")
        vmix = "[vmix]"

    # --- whole-video effect ---
    veff = vmix
    eff = _effect_filter(effect, W, H)
    if eff:
        # rgbsplit internally consumes/produces multiple streams; handle labels
        if effect == "rgbsplit":
            # rewrite: feed vmix into the split recipe, output [veff]
            filters.append(f"{vmix}split=3[rs][gs][bs];"
                           f"[rs]pad=iw+8:ih:8:0,crop=iw-8:ih:0:0,format=gbrp[rr];"
                           f"[gs]format=gbrp[gg];"
                           f"[bs]pad=iw+8:ih:0:0,crop=iw-8:ih:8:0,format=gbrp[bb];"
                           f"[rr][gg][bb]mergeplanes=0x001020:gbrp,format=yuv420p[veff]")
        else:
            filters.append(f"{vmix}{eff}[veff]")
        veff = "[veff]"

    # --- captions (ASS burn-in) ---
    vout = veff
    if captions and words:
        segments = [(bounds[i], bounds[i + 1]) for i in range(n)]
        ass_path = out_path + ".ass"
        build_ass_file(words, segments, captions, W, H, ass_path)
        esc = ass_path.replace("\\", "/").replace(":", "\\:")
        vout = "[vout]"
        filters.append(f"{veff}subtitles='{esc}'{vout}")

    if sfx_path:
        filters.append(f"[{n}:a][{sfx_idx}:a]"
                       f"amix=inputs=2:duration=first:dropout_transition=0[aout]")
        amap = ["-map", "[aout]"]
    else:
        amap = ["-map", f"{n}:a"]

    cmd += ["-filter_complex", ";".join(filters),
            "-map", vout] + amap + [
            "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-shortest", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + r.stderr[-1500:])
    return {"durations": [round(d, 2) for d in durs], "path": out_path}


# ----------------------------------------------------------------------------
# Phase 1 tools: Caption Generator (burn-in) + AI Clipper (9:16 auto-reframe)
# ----------------------------------------------------------------------------

def _video_dims(path):
    """Return (width, height). Prefers OpenCV; falls back to ffprobe."""
    try:
        import cv2
    except ImportError:
        cv2 = None
    if cv2 is not None:
        cap = cv2.VideoCapture(path)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        if w > 0 and h > 0:
            return w, h
    # fallback: ffprobe (ships with every ffmpeg install)
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
        capture_output=True, text=True)
    try:
        w, h = (int(x) for x in r.stdout.strip().split(","))
    except (ValueError, TypeError):
        raise CueError("Could not read the video size — the file looks broken.")
    if w <= 0 or h <= 0:
        raise CueError("Could not read the video — the file looks broken.")
    return w, h


def burn_captions(video_path, out_path, opts, words=None, model=None):
    """Transcribe the video's audio and burn styled captions into a new MP4.

    opts: preset id + tweaks (see resolve_caption_opts).
    words: pre-transcribed word list (skips transcription); each word's
      start/end are relative to video_path.
    model: transcription model ('tiny'/'base'/'small') when transcribing.
    """
    if words is None:
        words = transcribe(video_path, model=model)["words"]
    if not words:
        raise CueError("No speech found in the audio — cannot make captions.")
    # chunk words into caption segments (~4s each)
    segments, cur = [], [words[0]]
    for w in words[1:]:
        if (w["start"] - cur[0]["start"] > 4.0
                or w["word"].strip().endswith((".", "!", "?", "…"))):
            segments.append((cur[0]["start"], cur[-1]["end"]))
            cur = [w]
        else:
            cur.append(w)
    if cur:
        segments.append((cur[0]["start"], cur[-1]["end"]))
    W, H = _video_dims(video_path)
    ass_path = out_path + ".ass"
    build_ass_file(words, segments, opts, W, H, ass_path)
    esc = ass_path.replace("\\", "/").replace(":", "\\:")
    cmd = ["ffmpeg", "-y", "-i", video_path, "-vf", f"subtitles='{esc}'",
           "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-movflags", "+faststart", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + r.stderr[-1500:])
    return {"path": out_path, "words": len(words)}


def _media_duration(path):
    """Media duration in seconds, parsed from ffmpeg stderr."""
    r = subprocess.run(["ffmpeg", "-i", path], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    if not m:
        return 0.0
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def find_engaging_clips(video_path, n_clips=3, clip_dur=30.0, min_gap=5.0):
    """Find the most engaging moments of a video, any language.

    Scores 0.5s windows by audio RMS energy (loud = exciting: shouting,
    laughter, music peaks), smooths it, and greedily picks the top
    non-overlapping clip windows. Language-independent — it never looks
    at words. Returns [(start, end), ...] in seconds.
    """
    audio = decode_audio(video_path)
    sr = 16000
    win = int(0.5 * sr)
    n = len(audio) // win
    dur = len(audio) / sr
    if n < 4 or dur <= 0:
        raise CueError("Video is too short for auto clips.")
    rms = np.sqrt(np.mean(audio[:n * win].reshape(n, win) ** 2, axis=1) + 1e-9)
    k = 6  # ~3s smoothing
    smooth = np.convolve(rms, np.ones(k) / k, mode="same")
    cw = max(2, int(clip_dur / 0.5))
    gapw = max(1, int(min_gap / 0.5))
    used = np.zeros(n, dtype=bool)
    picks = []
    for _ in range(max(1, n_clips)):
        best, best_i = -1.0, None
        for i in range(0, max(1, n - cw)):
            if used[i:i + cw].any():
                continue
            sc = float(smooth[i:i + cw].mean())
            if sc > best:
                best, best_i = sc, i
        if best_i is None or best <= 1e-6:
            break
        picks.append(best_i * 0.5)
        used[max(0, best_i - gapw):min(n, best_i + cw + gapw)] = True
    picks.sort()
    if not picks:
        # flat/silent audio: spread clips evenly
        step = (dur - clip_dur) / max(1, n_clips)
        picks = [max(0.0, min(i * step, dur - clip_dur)) for i in range(n_clips)]
    return [(round(s, 2), round(min(s + clip_dur, dur), 2)) for s in picks]


def make_auto_clips(video_path, out_dir, n_clips=3, clip_dur=30.0,
                    cam="face", captions=None, model=None):
    """Cut the N most engaging clips, reframe to 9:16, burn captions.

    captions: caption opts dict (resolve_caption_opts) or None.
    Returns list of mp4 paths.
    """
    segs = find_engaging_clips(video_path, n_clips, clip_dur)
    words = None
    if captions is not None:
        words = transcribe(video_path, model=model)["words"]
    paths = []
    for i, (s, e) in enumerate(segs, 1):
        seg = os.path.join(out_dir, f"seg{i}.mp4")
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(s), "-t", str(round(e - s, 2)),
             "-i", video_path, "-c", "copy", seg],
            capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(seg):
            raise RuntimeError("ffmpeg trim failed: " + r.stderr[-800:])
        vert = os.path.join(out_dir, f"clip{i}.mp4")
        reframe_916(seg, vert, track_faces=(cam == "face"))
        try:
            os.remove(seg)
        except OSError:
            pass
        if captions is not None and words:
            wseg = [dict(w, start=max(0.0, w["start"] - s), end=max(0.0, w["end"] - s))
                    for w in words if s - 0.05 <= w["start"] < e]
            tmp = vert + ".cap.mp4"
            burn_captions(vert, tmp, captions, words=wseg)
            os.replace(tmp, vert)
        paths.append(vert)
    return paths


def preview_caption_clip(video_path, out_path, opts, model=None, preview_dur=5.0):
    """Render a short (middle 5s) sample of the video with captions burned.

    Real WYSIWYG preview for the Caption Generator: same renderer, tiny
    audio slice, fast model. Returns out_path.
    """
    dur = _media_duration(video_path)
    start = max(0.0, (dur - preview_dur) / 2) if dur > preview_dur else 0.0
    clip = out_path + ".pv.mp4"
    r = subprocess.run(
        ["ffmpeg", "-y", "-ss", str(round(start, 2)), "-t", str(preview_dur),
         "-i", video_path, "-c:v", "libx264", "-preset", "veryfast",
         "-pix_fmt", "yuv420p", "-c:a", "aac", clip],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg preview trim failed: " + r.stderr[-800:])
    try:
        burn_captions(clip, out_path, opts, model=model)
    finally:
        try:
            os.remove(clip)
        except OSError:
            pass
    return out_path


def preview_studio_clip(audio_path, image_path, out_path, opts, words,
                        preview_dur=5.0):
    """Render a 5s studio sample (first image + first 5s of audio) with
    captions burned. Real WYSIWYG preview for Studio captions."""
    wseg = [w for w in words if w["start"] < preview_dur]
    build_video(audio_path, [image_path], [0.0], out_path,
                total_duration=preview_dur, width=1280, height=720,
                captions=opts, words=wseg, sfx_on=False)
    return out_path


def reframe_916(in_path, out_path, track_faces=True):
    """Auto-reframe a landscape video to 9:16 vertical (Shorts/Reels).

    Faces are detected with OpenCV and the crop window is centered on the
    median face position, so the speaker stays framed (track_faces=True).
    With track_faces=False a plain center crop is used. Falls back to a
    center crop when no faces are found. Already-vertical videos pass
    through (re-encoded).
    """
    W, H = _video_dims(in_path)
    crop_w = int(H * 9 / 16)
    crop_w -= crop_w % 2  # even width for yuv420p
    faces_found = 0
    if crop_w >= W or W <= H:
        filt = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    else:
        if track_faces:
            try:
                import cv2
            except ImportError:
                raise CueError(
                    "OpenCV is not installed (needed for face detection). "
                    "Please run install.bat again to install the new libraries.")
            cap = cv2.VideoCapture(in_path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            centers = []
            step = max(1, int(fps * 0.5))  # sample a frame every ~0.5s
            i = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                if i % step == 0:
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    scale = 1.0
                    if W > 640:
                        scale = W / 640.0
                        gray = cv2.resize(gray, (640, int(H / scale)))
                    for (x, y, fw, fh) in cascade.detectMultiScale(gray, 1.2, 4):
                        centers.append((x + fw / 2) * scale)
                i += 1
            cap.release()
            cx = sorted(centers)[len(centers) // 2] if centers else W / 2
            faces_found = len(centers)
        else:
            cx = W / 2  # plain centered crop
        x0 = int(min(max(cx - crop_w / 2, 0), W - crop_w))
        x0 -= x0 % 2
        filt = f"crop={crop_w}:{H}:{x0}:0"
    cmd = ["ffmpeg", "-y", "-i", in_path, "-vf", filt,
           "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-movflags", "+faststart", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + r.stderr[-1500:])
    return {"path": out_path, "faces": faces_found}
