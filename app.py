"""
File Toolkit - a local converter app (built with CustomTkinter).

PDF
  - Merge several PDFs into one
  - Rotate, reorder and delete PDF pages
  - Convert PDF pages to images (png / jpg)
Video
  - Convert video type (mkv, mp4, mov, avi, webm)
  - Convert video to MP3
  - Convert a video clip to an animated GIF
  - Trim (cut) a video
  - Compress a video
Audio
  - Convert audio type (mp3, m4a, wav, flac, ogg, opus)
  - Download the audio of a YouTube link
Images
  - Convert image type (png, jpg, webp, bmp, gif, tiff, heic) and images to PDF
  - Image tools: resize, compress and add a text watermark

Everyday helpers: drag and drop files, remembers your last folder, "Open folder" button,
real percentage progress for video and audio jobs.

Run with:  python app.py
"""

import json                                # save small settings (the last folder) to a file
import math                                # math.isfinite, to reject "inf" / "nan" times
import os                                  # work with file names and paths
import re                                  # regular expressions: find patterns in text
import subprocess                          # run another program (ffmpeg)
import sys                                 # sys.platform tells us Windows / Mac / Linux
import threading                           # run work without freezing the window
import tkinter as tk                       # StringVar / BooleanVar live here
from tkinter import filedialog, messagebox # file pickers and popups

import customtkinter as ctk                # modern-looking widgets on top of tkinter
import pymupdf                             # render PDF pages to images
import imageio_ffmpeg                      # gives us the path to ffmpeg
from PIL import Image, ImageDraw, ImageFont, ImageOps   # Pillow: open, draw on and save images
from pypdf import PdfReader, PdfWriter     # PdfReader opens a PDF, PdfWriter builds a new one
import yt_dlp                              # downloads audio/video from YouTube and similar sites

# Optional extras: if one is not installed the app still works, just without that feature.
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES      # drag and drop files into the window
    DnDMixin = TkinterDnD.DnDWrapper
except Exception:
    TkinterDnD, DND_FILES, DnDMixin = None, None, object

try:
    import pillow_heif                                  # lets Pillow open iPhone .heic photos
    pillow_heif.register_heif_opener()
    HEIC_OK = True
except Exception:
    HEIC_OK = False

# ---------- look and feel ----------
ctk.set_appearance_mode("dark")            # "dark", "light" or "system"
ctk.set_default_color_theme("blue")        # built-in themes: blue, green, dark-blue

# A pair like ("light value", "dark value") makes a colour that adapts to the mode
TEXT = ("gray10", "gray90")
MUTED = ("gray40", "gray60")
OK_GREEN = "#2FA572"
ERROR_RED = "#E5534B"

# ---------- file types the pickers (and drag and drop) accept ----------
VIDEO_PATTERNS = "*.mp4 *.mkv *.avi *.mov *.webm *.flv *.wmv *.m4v *.ts"
AUDIO_PATTERNS = "*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus *.wma"
IMAGE_PATTERNS = "*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tiff" + (" *.heic *.heif" if HEIC_OK else "")
PDF_PATTERNS = "*.pdf"


# ----------------------------------------------------------------------
# PART 0: SMALL HELPERS (settings, file dialogs, text formatting)
# ----------------------------------------------------------------------

SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".filetoolkit.json")


def load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:                      # no file yet, or it is damaged: start fresh
        return {}


def save_settings(settings):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f)
    except Exception:
        pass                               # not being able to save a setting is never worth an error


SETTINGS = load_settings()                 # e.g. {"last_dir": "C:/Users/Mai/Videos"}


def start_dir():
    """The folder the file dialogs should open in: the last one you used."""
    folder = SETTINGS.get("last_dir")
    return folder if folder and os.path.isdir(folder) else os.path.expanduser("~")


def remember(path):
    """Remember the folder of a file or folder the user just picked."""
    if not path:
        return
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    if folder and folder != SETTINGS.get("last_dir"):
        SETTINGS["last_dir"] = folder
        save_settings(SETTINGS)


# Thin wrappers around the tkinter dialogs: they open in the last folder and remember the new one.
def ask_open_file(filetypes):
    path = filedialog.askopenfilename(filetypes=filetypes, initialdir=start_dir())
    remember(path)
    return path


def ask_open_files(filetypes):
    paths = filedialog.askopenfilenames(filetypes=filetypes, initialdir=start_dir())
    if paths:
        remember(paths[0])
    return paths


def ask_save_file(**options):
    path = filedialog.asksaveasfilename(initialdir=start_dir(), **options)
    remember(path)
    return path


def ask_folder(title):
    path = filedialog.askdirectory(title=title, initialdir=start_dir())
    remember(path)
    return path


def human_size(size):
    """12345678 -> '11.8 MB'"""
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024


def format_time(seconds):
    """83.4 -> '1:23', 3723 -> '1:02:03'"""
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def parse_time(text):
    """'90' -> 90.0, '1:30' -> 90.0, '1:02:03.5' -> 3723.5. Raises ValueError for anything else."""
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(text)
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)       # float("abc") raises ValueError for us
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(text)
    return seconds


def free_output_path(folder, base, ext, source, used):
    """Pick a file name in `folder` that is not the source file and not already used in this batch."""
    out = os.path.join(folder, f"{base}.{ext}")
    copy = 1
    while os.path.abspath(out) == os.path.abspath(source) or out in used:
        copy += 1
        out = os.path.join(folder, f"{base}_{copy}.{ext}")
    used.add(out)
    return out


# ----------------------------------------------------------------------
# PART 1: CORE FUNCTIONS (no window code, so they are easy to test)
# ----------------------------------------------------------------------

# On Windows this stops a black console window from flashing up
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Lines ffmpeg prints about its progress (as opposed to lines that describe a problem)
PROGRESS_LINE = re.compile(
    r"^(frame|fps|stream_\d+_\d+_q|bitrate|total_size|out_time_us|out_time_ms|out_time|"
    r"dup_frames|drop_frames|speed|progress)=")


def probe_duration(path):
    """Length of a video or audio file in seconds, or None if ffmpeg can't tell."""
    result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", path],
                            capture_output=True, text=True, encoding="utf-8", errors="replace",
                            stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    # ffmpeg prints "Duration: 00:03:25.12" even though it then complains it has no output file
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def run_ffmpeg(args, total_seconds=None, on_percent=None):
    """Run ffmpeg with `args` (everything after the program name).

    If total_seconds is known, on_percent(fraction) is called while it works (fraction is 0.0 to 1.0).
    Raises RuntimeError with ffmpeg's message if it fails.
    """
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
               "-nostats", "-progress", "pipe:1"] + args    # "-progress pipe:1" = report progress as text
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                               errors="replace", creationflags=NO_WINDOW)
    problems = []
    for line in process.stdout:                  # read ffmpeg's output line by line while it runs
        line = line.strip()
        if line.startswith("out_time_us="):      # how much of the output is finished, in microseconds
            try:
                seconds = int(line.split("=", 1)[1]) / 1_000_000
            except ValueError:                   # ffmpeg sometimes prints "N/A" at the start
                continue
            if on_percent and total_seconds:
                on_percent(max(0.0, min(seconds / total_seconds, 1.0)))
        elif line and not PROGRESS_LINE.match(line):
            problems.append(line)                # anything else is a message about a problem
    process.wait()
    if process.returncode != 0:                  # 0 means success, anything else is an error
        raise RuntimeError("\n".join(problems)[-500:] or "ffmpeg failed")


def merge_pdfs(pdf_paths, output_path):
    """Join all PDFs in pdf_paths (in order) into one file."""
    writer = PdfWriter()                   # an empty PDF we will fill
    for path in pdf_paths:                 # go through the files in order
        writer.append(path)                # add every page of this PDF
    with open(output_path, "wb") as f:     # "wb" = write binary
        writer.write(f)                    # save the combined PDF
    writer.close()


def video_to_mp3(video_path, output_path, on_percent=None):
    """Extract the audio of a video and save it as an MP3."""
    run_ffmpeg([
        "-i", video_path,          # -i = input file
        "-vn",                     # vn = "no video", keep only audio
        "-codec:a", "libmp3lame",  # encode the audio as MP3
        "-q:a", "2",               # quality: 0 is best, 9 is worst; 2 is high
        output_path,
    ], probe_duration(video_path), on_percent)


def convert_image(image_path, output_path):
    """Convert one image to the format given by output_path's extension."""
    img = Image.open(image_path)
    ext = os.path.splitext(output_path)[1].lower()   # e.g. ".jpg"
    # JPEG and PDF cannot store transparency, so flatten onto a white background
    if ext in (".jpg", ".jpeg", ".pdf") and img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))  # white canvas
        background.paste(img, mask=img.split()[-1])  # paste using alpha as the mask
        img = background
    elif ext in (".jpg", ".jpeg", ".pdf") and img.mode != "RGB":
        img = img.convert("RGB")
    img.save(output_path)          # Pillow picks the format from the extension


def images_to_pdf(image_paths, output_path):
    """Put several images into one PDF, one image per page."""
    pages = []
    for path in image_paths:
        img = Image.open(path)
        pages.append(img.convert("RGB"))             # PDF needs RGB
    pages[0].save(output_path, save_all=True, append_images=pages[1:])


def pdf_to_images(pdf_path, output_folder, image_format="png", zoom=2.0):
    """Save every page of a PDF as an image. Returns how many pages were saved."""
    doc = pymupdf.open(pdf_path)
    base = os.path.splitext(os.path.basename(pdf_path))[0]  # file name without ".pdf"
    matrix = pymupdf.Matrix(zoom, zoom)       # zoom 2 = 144 dpi, sharper than default
    for number, page in enumerate(doc, start=1):             # pages numbered from 1
        pix = page.get_pixmap(matrix=matrix)                 # draw the page as pixels
        out = os.path.join(output_folder, f"{base}_page{number}.{image_format}")
        pix.save(out)
    count = len(doc)
    doc.close()
    return count


def make_page_thumbnails(pdf_path, max_size=(56, 72)):
    """Return a small PIL image for every page of a PDF (used by the page editor)."""
    doc = pymupdf.open(pdf_path)
    thumbs = []
    for page in doc:
        pix = page.get_pixmap(matrix=pymupdf.Matrix(0.3, 0.3))     # draw the page small
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        img.thumbnail(max_size)                                    # shrink to fit the box
        thumbs.append(img)
    doc.close()
    return thumbs


def edit_pdf_pages(pdf_path, output_path, pages):
    """Build a new PDF from `pages`, a list of (original_page_index, extra_rotation_degrees).

    The ORDER of the list is the new page order. Pages not in the list are deleted.
    """
    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    for index, rotation in pages:
        page = reader.pages[index]
        if rotation:
            page.rotate(rotation)          # adds to any rotation the page already has
        writer.add_page(page)
    with open(output_path, "wb") as f:
        writer.write(f)
    writer.close()


def video_to_gif(video_path, output_path, fps=12, width=480, start=0.0, duration=5.0, on_percent=None):
    """Turn a part of a video into an animated GIF."""
    # GIFs only have 256 colours. These two filters first work out the best 256 colours
    # for this clip (palettegen) and then use them (paletteuse), which looks much better.
    filters = (f"fps={fps},scale={width}:-1:flags=lanczos,"
               "split[a][b];[a]palettegen[p];[b][p]paletteuse")
    run_ffmpeg([
        "-ss", str(start),         # where to start, in seconds
        "-t", str(duration),       # how many seconds to keep
        "-i", video_path,
        "-vf", filters,
        "-loop", "0",              # 0 = repeat forever
        output_path,
    ], duration, on_percent)


VIDEO_FORMATS = ["mp4", "mkv", "mov", "avi", "webm"]
VIDEO_QUALITY = {"High": 18, "Good": 23, "Smaller file": 28}   # x264 "crf": lower number = better picture


def convert_video(video_path, output_path, quality="Good", fast=False, on_percent=None):
    """Convert a video to the type given by output_path's extension (mp4, mkv, mov, avi, webm).

    fast=True first tries to just re-wrap the video without re-encoding (very quick, no quality
    loss). If that is not possible for this file, it falls back to a normal conversion.
    """
    ext = os.path.splitext(output_path)[1].lower()
    crf = VIDEO_QUALITY[quality]
    total = probe_duration(video_path)

    # Keep the video and every audio track; skip subtitle/data tracks, because mp4 can't hold them.
    base = ["-i", video_path, "-map", "0:v:0", "-map", "0:a?"]

    if fast:
        try:
            run_ffmpeg(base + ["-c", "copy", output_path], total, on_percent)   # copy = no re-encoding
            return
        except RuntimeError:
            if os.path.exists(output_path):
                os.remove(output_path)                     # remove the half-written file, then re-encode

    if ext == ".webm":                                     # webm must use VP9 video + Opus audio
        options = ["-c:v", "libvpx-vp9", "-crf", str(crf + 9), "-b:v", "0", "-row-mt", "1",
                   "-deadline", "good", "-cpu-used", "4", "-pix_fmt", "yuv420p", "-c:a", "libopus"]
    elif ext == ".avi":                                    # avi: H.264 video + MP3 audio
        options = ["-c:v", "libx264", "-crf", str(crf), "-preset", "fast", "-pix_fmt", "yuv420p",
                   "-c:a", "libmp3lame", "-q:a", "2"]
    else:                                                  # mp4 / mkv / mov: H.264 video + AAC audio
        options = ["-c:v", "libx264", "-crf", str(crf), "-preset", "fast", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-b:a", "192k"]
        if ext in (".mp4", ".mov"):
            options += ["-movflags", "+faststart"]         # lets the video start playing before it is fully loaded

    run_ffmpeg(base + options + [output_path], total, on_percent)


VIDEO_COMPRESS_LEVELS = {"Balanced": 26, "Smaller": 30, "Smallest": 34}    # higher crf = smaller file
VIDEO_MAX_HEIGHT = {"Keep size": None, "1080p": 1080, "720p": 720, "480p": 480}


def compress_video(video_path, output_path, level="Balanced", max_height=None, on_percent=None):
    """Make a video file smaller (saved as mp4). max_height shrinks big videos, e.g. 720."""
    options = []
    if max_height:
        # min(...) means a video that is already smaller is never stretched up
        options = ["-vf", f"scale=-2:'min({max_height},ih)'"]
    run_ffmpeg([
        "-i", video_path, "-map", "0:v:0", "-map", "0:a?",
        *options,
        "-c:v", "libx264", "-crf", str(VIDEO_COMPRESS_LEVELS[level]), "-preset", "fast",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
        output_path,
    ], probe_duration(video_path), on_percent)


def trim_video(video_path, output_path, start, end=None, fast=False, on_percent=None):
    """Save the part of a video from `start` to `end` seconds (end=None means: until the end)."""
    total = probe_duration(video_path)
    if end is None:
        length = (total - start) if total else None
    else:
        length = end - start
    cut = ["-ss", str(start), "-i", video_path]            # -ss before -i = jump straight to the start
    if length:
        cut += ["-t", str(length)]                         # -t = how many seconds to keep
    streams = ["-map", "0:v:0", "-map", "0:a?"]
    if fast:
        # copy = no re-encoding: instant, but the cut can only begin at a keyframe (often a bit early)
        options = ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    else:
        options = ["-c:v", "libx264", "-crf", "20", "-preset", "fast", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
    run_ffmpeg(cut + streams + options + [output_path], length, on_percent)


# audio type -> the ffmpeg encoder settings for it
AUDIO_FORMATS = {
    "mp3": ["-c:a", "libmp3lame"],
    "m4a": ["-c:a", "aac"],
    "wav": ["-c:a", "pcm_s16le"],
    "flac": ["-c:a", "flac"],
    "ogg": ["-c:a", "libvorbis"],
    "opus": ["-c:a", "libopus"],
}
AUDIO_BITRATES = ["128k", "192k", "256k", "320k"]


def convert_audio(audio_path, output_path, bitrate="192k", on_percent=None):
    """Convert an audio file (or the sound of a video) to the type given by output_path's extension."""
    kind = os.path.splitext(output_path)[1].lower().lstrip(".")
    options = list(AUDIO_FORMATS[kind])
    if kind not in ("wav", "flac"):                        # wav and flac are lossless: no bitrate to choose
        options += ["-b:a", bitrate]
    run_ffmpeg(["-i", audio_path, "-vn", "-map", "0:a:0"] + options + [output_path],
               probe_duration(audio_path), on_percent)


def youtube_to_audio(url, output_folder, audio_format="mp3", on_progress=None):
    """Download the audio of one YouTube video and convert it. Returns the video title."""
    def hook(d):                           # yt-dlp calls this while it downloads
        if on_progress and d.get("status") == "downloading":
            on_progress("Downloading... " + d.get("_percent_str", "").strip())

    def pp_hook(d):                        # ...and this when it starts converting the audio
        if on_progress and d.get("status") == "started":
            on_progress(f"Converting to {audio_format}...")

    options = {
        "format": "bestaudio/best",                            # best audio-only stream
        "outtmpl": os.path.join(output_folder, "%(title)s.%(ext)s"),   # file name = video title
        "windowsfilenames": True,                              # remove characters Windows dislikes
        "noplaylist": True,                                    # a playlist link = only that one video
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),    # use our bundled ffmpeg
        "progress_hooks": [hook],
        "postprocessor_hooks": [pp_hook],
        "quiet": True,
        "noprogress": True,                                    # no text progress bar (there is no console)
        "no_warnings": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",                       # convert the download to audio
            "preferredcodec": audio_format,
            "preferredquality": "192",                         # 192 kbps (ignored for wav/flac)
        }],
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
    return info.get("title", "audio")


# how the "Image tools" tab can resize: label -> (maximum width in pixels, or percent)
IMAGE_RESIZE = {
    "Keep size": (None, None),
    "Max width 1920 px": (1920, None),
    "Max width 1280 px": (1280, None),
    "Max width 800 px": (800, None),
    "Half size (50%)": (None, 50),
    "Quarter size (25%)": (None, 25),
}
IMAGE_OUTPUT_TYPES = ["Keep type", "jpg", "png", "webp"]
WATERMARK_POSITIONS = ["Bottom right", "Bottom left", "Top right", "Top left", "Center"]


def load_font(size):
    """A TrueType font at the given size (Arial on Windows), or Pillow's built-in one."""
    for name in ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def add_watermark(img, text, position="Bottom right"):
    """Draw semi-transparent white text (with a soft shadow) on the image."""
    base = img.convert("RGBA")
    size = max(14, base.width // 28)                       # text size grows with the picture
    font = load_font(size)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))     # a see-through layer to draw on
    draw = ImageDraw.Draw(layer)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)   # how big the text will be
    text_w, text_h = right - left, bottom - top
    margin = max(10, size // 2)
    if "Left" in position:
        x = margin
    elif "Right" in position:
        x = base.width - text_w - margin
    else:
        x = (base.width - text_w) // 2
    if "Top" in position:
        y = margin
    elif "Bottom" in position:
        y = base.height - text_h - margin
    else:
        y = (base.height - text_h) // 2
    x, y = x - left, y - top                               # the text box may not start exactly at 0,0
    shadow = max(1, size // 14)
    draw.text((x + shadow, y + shadow), text, font=font, fill=(0, 0, 0, 120))     # shadow first
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 190))                 # then the text
    return Image.alpha_composite(base, layer)


def process_image(image_path, output_path, max_width=None, percent=None, quality=85,
                  watermark="", position="Bottom right"):
    """Resize (only ever smaller), add a watermark and save with the chosen quality."""
    img = Image.open(image_path)
    img = ImageOps.exif_transpose(img)         # phone photos store "which way is up": apply it
    if percent:
        new_size = (max(1, round(img.width * percent / 100)), max(1, round(img.height * percent / 100)))
        img = img.resize(new_size, Image.LANCZOS)
    elif max_width and img.width > max_width:
        img = img.resize((max_width, round(img.height * max_width / img.width)), Image.LANCZOS)
    if watermark:
        img = add_watermark(img, watermark, position)

    ext = os.path.splitext(output_path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        if img.mode in ("RGBA", "LA", "P"):    # JPEG has no transparency: flatten onto white
            img = img.convert("RGBA")
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")
        img.save(output_path, quality=quality, optimize=True)
    elif ext == ".webp":
        img.save(output_path, quality=quality)
    else:                                      # png is lossless: "quality" does not apply, only optimize
        img.save(output_path, optimize=True)


# ----------------------------------------------------------------------
# PART 2: GUI HELPERS
# ----------------------------------------------------------------------

def run_in_background(work, on_done, on_error, root):
    """Run work() in another thread so the window doesn't freeze."""
    def target():
        try:
            result = work()
            root.after(0, lambda: on_done(result))   # hand result back to the window thread
        except Exception as e:
            root.after(0, lambda: on_error(e))
    threading.Thread(target=target, daemon=True).start()


class FileList(ctk.CTkFrame):
    """A card showing chosen files as rows, each with up / down / remove buttons."""

    def __init__(self, parent, filetypes, height=210):
        super().__init__(parent, corner_radius=12)
        self.filetypes = filetypes
        self.allowed = self.allowed_extensions(filetypes)  # used to filter dropped files
        self.files = []                                   # the full paths, in order

        top = ctk.CTkFrame(self, fg_color="transparent")  # row with Add / Clear / count
        top.pack(fill="x", padx=12, pady=(12, 4))
        ctk.CTkButton(top, text="+ Add files", width=110, command=self.add).pack(side="left")
        ctk.CTkButton(top, text="Clear all", width=90, fg_color="transparent",
                      border_width=1, text_color=TEXT,
                      command=self.clear).pack(side="left", padx=8)
        self.count_label = ctk.CTkLabel(top, text="", text_color=MUTED)
        self.count_label.pack(side="right")

        # a frame that scrolls when there are many rows
        self.body = ctk.CTkScrollableFrame(self, height=height)
        self.body.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        self.refresh()

    @staticmethod
    def allowed_extensions(filetypes):
        """[("Images", "*.png *.jpg")] -> {".png", ".jpg"}.  None means every file type is allowed."""
        extensions = set()
        for _, patterns in filetypes:
            for pattern in patterns.split():
                if pattern == "*.*":
                    return None
                extensions.add(pattern.lstrip("*").lower())
        return extensions

    def add(self):
        self.add_paths(ask_open_files(self.filetypes))

    def add_paths(self, paths):
        """Add files (from the Add button or drag and drop), skipping the wrong types."""
        good = [p for p in paths if os.path.isfile(p)
                and (self.allowed is None or os.path.splitext(p)[1].lower() in self.allowed)]
        self.files.extend(good)
        self.refresh()
        if len(good) < len(paths):
            self.winfo_toplevel().set_status(f"Skipped {len(paths) - len(good)} file(s) of the wrong type", TEXT)

    def clear(self):
        self.files = []
        self.refresh()

    def remove(self, i):
        del self.files[i]
        self.refresh()

    def move(self, i, step):
        j = i + step                                      # -1 = up, +1 = down
        if 0 <= j < len(self.files):
            self.files[i], self.files[j] = self.files[j], self.files[i]  # swap
            self.refresh()

    def refresh(self):
        """Rebuild all the rows from self.files."""
        for child in self.body.winfo_children():          # throw away the old rows
            child.destroy()
        self.count_label.configure(text=f"{len(self.files)} file(s)")

        if not self.files:
            ctk.CTkLabel(self.body, text="No files yet. Click '+ Add files' or drop files here.",
                         text_color=MUTED).pack(pady=40)
            return

        for i, path in enumerate(self.files):
            row = ctk.CTkFrame(self.body, corner_radius=8)
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text=f"{i + 1}.  {os.path.basename(path)}",
                         anchor="w").pack(side="left", fill="x", expand=True, padx=10, pady=6)
            # i=i freezes the current value of i for each button
            ctk.CTkButton(row, text="✕", width=30, fg_color="transparent", text_color=TEXT,
                          hover_color=("#F2B8B5", "#6B2B28"),
                          command=lambda i=i: self.remove(i)).pack(side="right", padx=(0, 6))
            ctk.CTkButton(row, text="↓", width=30, fg_color="transparent", text_color=TEXT,
                          command=lambda i=i: self.move(i, 1)).pack(side="right")
            ctk.CTkButton(row, text="↑", width=30, fg_color="transparent", text_color=TEXT,
                          command=lambda i=i: self.move(i, -1)).pack(side="right")
            self.winfo_toplevel().droppable(row, self.add_paths)   # the new row accepts drops too

    def paths(self):
        return list(self.files)


# ----------------------------------------------------------------------
# PART 3: THE APP WINDOW
# ----------------------------------------------------------------------

class App(ctk.CTk, DnDMixin):
    def __init__(self):
        super().__init__()
        self.title("File Toolkit")
        self.geometry("940x700")
        self.minsize(860, 660)

        # Turn on drag and drop. If it can't load, the app simply works without it.
        self.dnd_ok = False
        if TkinterDnD is not None:
            try:
                self.TkdndVersion = TkinterDnD._require(self)
                self.dnd_ok = True
            except Exception:
                pass

        # column 0 = sidebar (fixed), column 1 = content (stretches)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.pages = {}                       # page name -> its frame
        self.nav = {}                         # page name -> its sidebar button
        self.last_group = None                # remembers which sidebar heading we are under
        self.result_path = None               # what the "Open folder" button opens

        self.build_sidebar()
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew", padx=24, pady=(20, 10))
        self.build_status_bar()

        self.add_page("Merge PDF", self.build_merge_page, "PDF")
        self.add_page("PDF pages", self.build_pages_page, "PDF")
        self.add_page("PDF to images", self.build_pdf_page, "PDF")
        self.add_page("Video convert", self.build_vconvert_page, "Video")
        self.add_page("Video to MP3", self.build_video_page, "Video")
        self.add_page("Video to GIF", self.build_gif_page, "Video")
        self.add_page("Trim video", self.build_trim_page, "Video")
        self.add_page("Compress video", self.build_compress_page, "Video")
        self.add_page("Audio convert", self.build_audio_page, "Audio")
        self.add_page("YouTube audio", self.build_youtube_page, "Audio")
        self.add_page("Image convert", self.build_image_page, "Images")
        self.add_page("Image tools", self.build_itools_page, "Images")
        self.show_page("Merge PDF")

    # ---------- layout pieces ----------
    def build_sidebar(self):
        self.sidebar = ctk.CTkFrame(self, width=210, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        ctk.CTkLabel(self.sidebar, text="File Toolkit",
                     font=ctk.CTkFont(size=22, weight="bold")).pack(padx=20, pady=(20, 0), anchor="w")
        ctk.CTkLabel(self.sidebar, text="Convert files locally",
                     text_color=MUTED).pack(padx=20, pady=(0, 8), anchor="w")

        # packed at the bottom so it stays there under the nav buttons
        self.theme_switch = ctk.CTkSwitch(self.sidebar, text="Dark mode", command=self.toggle_theme)
        self.theme_switch.select()            # start switched on = dark
        self.theme_switch.pack(side="bottom", padx=20, pady=16, anchor="w")

        # the sidebar buttons live in their own frame, which gets all the space that is left
        self.nav_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.nav_frame.pack(fill="both", expand=True)

    def build_status_bar(self):
        bar = ctk.CTkFrame(self, height=38, corner_radius=0)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.status_label = ctk.CTkLabel(bar, text="Ready", anchor="w")
        self.status_label.pack(side="left", padx=16, pady=8)
        # indeterminate = a bar that slides back and forth while we wait
        self.progress = ctk.CTkProgressBar(bar, mode="indeterminate", width=160)
        # shown only after a job has finished
        self.open_button = ctk.CTkButton(bar, text="Open folder", width=110, height=26,
                                         command=self.open_result)

    def add_page(self, name, builder, group):
        if group != self.last_group:          # a small heading whenever the group changes
            ctk.CTkLabel(self.nav_frame, text=group.upper(), text_color=MUTED, anchor="w",
                         font=ctk.CTkFont(size=11, weight="bold")).pack(fill="x", padx=20, pady=(8, 0))
            self.last_group = group
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        builder(page)                         # fill the page with widgets
        self.pages[name] = page
        button = ctk.CTkButton(self.nav_frame, text=name, anchor="w", height=30, corner_radius=8,
                               fg_color="transparent", text_color=TEXT,
                               hover_color=("gray75", "gray25"),
                               command=lambda: self.show_page(name))
        button.pack(fill="x", padx=12, pady=1)
        self.nav[name] = button

    def show_page(self, name):
        for page in self.pages.values():
            page.pack_forget()                # hide every page...
        self.pages[name].pack(fill="both", expand=True)   # ...then show the chosen one
        for n, button in self.nav.items():    # highlight the active sidebar button
            if n == name:
                button.configure(fg_color=("#3B8ED0", "#1F6AA5"), text_color="white")
            else:
                button.configure(fg_color="transparent", text_color=TEXT)

    def toggle_theme(self):
        ctk.set_appearance_mode("dark" if self.theme_switch.get() else "light")

    # ---------- drag and drop ----------
    def droppable(self, widget, handler):
        """Let files be dragged onto `widget` and everything inside it. handler(paths) gets the file list."""
        if not self.dnd_ok:
            return

        def register(w):
            try:
                w.drop_target_register(DND_FILES)
                # splitlist turns '{C:/My Files/a.mp4} C:/b.mp4' into ['C:/My Files/a.mp4', 'C:/b.mp4']
                w.dnd_bind("<<Drop>>", lambda event: (handler(list(self.tk.splitlist(event.data))), event.action)[1])
            except Exception:
                pass                          # a few inner widgets can't be targets: skip them
            for child in w.winfo_children():
                register(child)
        register(widget)

    def first_with_extension(self, paths, patterns):
        """For single-file tabs: the first dropped file whose type is allowed, else None (and say so)."""
        allowed = FileList.allowed_extensions([("", patterns)])
        for path in paths:
            if os.path.isfile(path) and os.path.splitext(path)[1].lower() in allowed:
                return path
        self.set_status("That file type is not supported on this tab", TEXT)
        return None

    # ---------- shared helpers ----------
    def header(self, page, title, subtitle):
        ctk.CTkLabel(page, text=title, font=ctk.CTkFont(size=26, weight="bold"),
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(page, text=subtitle, text_color=MUTED, anchor="w").pack(fill="x", pady=(0, 14))

    def action_button(self, page, text, command):
        ctk.CTkButton(page, text=text, command=command, height=44,
                      font=ctk.CTkFont(size=15, weight="bold")).pack(fill="x", pady=(16, 0))

    def picker(self, parent, variable, command):
        """A text box showing the chosen path, plus a Browse button."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=16)
        ctk.CTkEntry(row, textvariable=variable, height=36).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text="Browse", width=100, height=36,
                      command=command).pack(side="left", padx=(10, 0))

    def set_status(self, text, color):
        self.status_label.configure(text=text, text_color=color)

    def busy(self, message):
        self.set_status(message, TEXT)
        self.open_button.pack_forget()                    # the previous job's button is out of date
        self.progress.configure(mode="indeterminate")
        self.progress.pack(side="right", padx=16)         # show the moving bar
        self.progress.start()

    def set_progress(self, fraction):
        """Turn the moving bar into a real percentage bar (fraction is 0.0 to 1.0)."""
        if self.progress.cget("mode") != "determinate":
            self.progress.stop()
            self.progress.configure(mode="determinate")
        self.progress.set(fraction)

    def progress_reporter(self, label, number=1, total=1):
        """Make a function the worker thread can call with 0.0-1.0 for file `number` of `total`."""
        def report(fraction):
            overall = (number - 1 + fraction) / total

            def apply():                                  # runs in the window thread
                self.set_progress(overall)
                self.set_status(f"{label}  {int(overall * 100)}%", TEXT)
            self.after(0, apply)
        return report

    def finished(self, message, result_path=None):
        self.progress.stop()
        self.progress.pack_forget()                       # hide the bar again
        self.set_status(message, OK_GREEN)
        self.result_path = result_path
        if result_path:
            self.open_button.pack(side="right", padx=16)  # offer to open the result

    def failed(self, error):
        self.progress.stop()
        self.progress.pack_forget()
        self.set_status("Failed", ERROR_RED)
        messagebox.showerror("Error", str(error))

    def open_result(self):
        """Show the finished file (or folder) in the file explorer."""
        path = self.result_path
        if not path:
            return
        try:
            if sys.platform == "win32":
                if os.path.isfile(path):
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])   # selects the file
                else:
                    os.startfile(os.path.normpath(path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path] if os.path.isfile(path) else ["open", path])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path) if os.path.isfile(path) else path])
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # ---------- Page: Merge PDF ----------
    def build_merge_page(self, page):
        self.header(page, "Merge PDF", "Add PDFs, put them in order, and join them into one file.")
        self.merge_list = FileList(page, [("PDF files", PDF_PATTERNS)])
        self.merge_list.pack(fill="both", expand=True)
        self.action_button(page, "Merge into one PDF", self.do_merge)
        self.droppable(page, self.merge_list.add_paths)

    def do_merge(self):
        paths = self.merge_list.paths()
        if len(paths) < 2:
            messagebox.showinfo("Merge PDF", "Add at least two PDFs.")
            return
        out = ask_save_file(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
        if not out:                                       # user pressed Cancel
            return
        self.busy("Merging...")
        run_in_background(
            lambda: merge_pdfs(paths, out),
            lambda _: self.finished(f"Saved {out}", out),
            self.failed, self)

    # ---------- Page: Video to MP3 ----------
    def build_video_page(self, page):
        self.header(page, "Video to MP3", "Pick a video and save its audio as an MP3.")
        card = ctk.CTkFrame(page, corner_radius=12)
        card.pack(fill="x")
        self.video_path = tk.StringVar()                  # holds the chosen file path
        self.picker(card, self.video_path, self.pick_video)
        self.action_button(page, "Convert to MP3", self.do_video)
        self.droppable(page, lambda paths: self.drop_into(self.video_path, paths, VIDEO_PATTERNS))

    def drop_into(self, variable, paths, patterns):
        """Dropped files on a single-file tab: put the first suitable one into the path box."""
        path = self.first_with_extension(paths, patterns)
        if path:
            variable.set(path)

    def pick_video(self):
        p = ask_open_file([("Video files", VIDEO_PATTERNS), ("All files", "*.*")])
        if p:
            self.video_path.set(p)

    def do_video(self):
        src = self.video_path.get()
        if not src:
            messagebox.showinfo("Video to MP3", "Choose a video first.")
            return
        default = os.path.splitext(os.path.basename(src))[0] + ".mp3"   # suggest same name
        out = ask_save_file(initialfile=default, defaultextension=".mp3", filetypes=[("MP3", "*.mp3")])
        if not out:
            return
        self.busy("Converting video... this can take a while")
        report = self.progress_reporter("Converting to MP3...")
        run_in_background(
            lambda: video_to_mp3(src, out, report),
            lambda _: self.finished(f"Saved {out}", out),
            self.failed, self)

    # ---------- Page: Image convert ----------
    def build_image_page(self, page):
        self.header(page, "Image convert", "Change image types, or combine images into one PDF.")
        self.image_list = FileList(page, [("Images", IMAGE_PATTERNS)], height=150)
        self.image_list.pack(fill="both", expand=True)

        options = ctk.CTkFrame(page, corner_radius=12)
        options.pack(fill="x", pady=(12, 0))
        ctk.CTkLabel(options, text="Convert to:").pack(side="left", padx=(16, 8), pady=14)
        self.image_target = tk.StringVar(value="jpg")
        ctk.CTkOptionMenu(options, values=["png", "jpg", "webp", "bmp", "gif", "tiff", "pdf"],
                          variable=self.image_target, width=110).pack(side="left")
        self.image_one_pdf = tk.BooleanVar(value=False)   # the checkbox state
        ctk.CTkCheckBox(options, text="Combine all into one PDF",
                        variable=self.image_one_pdf).pack(side="left", padx=24)

        self.action_button(page, "Convert", self.do_image)
        self.droppable(page, self.image_list.add_paths)

    def do_image(self):
        paths = self.image_list.paths()
        if not paths:
            messagebox.showinfo("Image convert", "Add at least one image.")
            return
        target = self.image_target.get()

        # Option A: all images become pages of a single PDF
        if target == "pdf" and self.image_one_pdf.get():
            out = ask_save_file(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
            if not out:
                return
            self.busy("Creating PDF...")
            run_in_background(
                lambda: images_to_pdf(paths, out),
                lambda _: self.finished(f"Saved {out}", out),
                self.failed, self)
            return

        # Option B: convert each image separately into a chosen folder
        folder = ask_folder("Choose output folder")
        if not folder:
            return

        def work():
            for p in paths:
                name = os.path.splitext(os.path.basename(p))[0] + "." + target
                convert_image(p, os.path.join(folder, name))
            return len(paths)

        self.busy("Converting images...")
        run_in_background(work, lambda n: self.finished(f"Converted {n} image(s) into {folder}", folder),
                          self.failed, self)

    # ---------- Page: PDF to images ----------
    def build_pdf_page(self, page):
        self.header(page, "PDF to images", "Save every page of a PDF as a picture.")
        card = ctk.CTkFrame(page, corner_radius=12)
        card.pack(fill="x")
        self.pdf_path = tk.StringVar()
        self.picker(card, self.pdf_path, self.pick_pdf)

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 16))
        ctk.CTkLabel(row, text="Format:").pack(side="left", padx=(0, 8))
        self.pdf_format = tk.StringVar(value="png")
        ctk.CTkOptionMenu(row, values=["png", "jpg"], variable=self.pdf_format,
                          width=90).pack(side="left")

        self.action_button(page, "Convert PDF", self.do_pdf)
        self.droppable(page, lambda paths: self.drop_into(self.pdf_path, paths, PDF_PATTERNS))

    def pick_pdf(self):
        p = ask_open_file([("PDF files", PDF_PATTERNS)])
        if p:
            self.pdf_path.set(p)

    def do_pdf(self):
        src = self.pdf_path.get()
        if not src:
            messagebox.showinfo("PDF to images", "Choose a PDF first.")
            return
        folder = ask_folder("Choose output folder")
        if not folder:
            return
        fmt = self.pdf_format.get()
        self.busy("Converting PDF...")
        run_in_background(
            lambda: pdf_to_images(src, folder, fmt),
            lambda n: self.finished(f"Saved {n} page(s) into {folder}", folder),
            self.failed, self)

    # ---------- Page: PDF pages (rotate / reorder / delete) ----------
    def build_pages_page(self, page):
        self.header(page, "PDF pages", "Rotate, reorder or delete pages, then save a new PDF.")
        card = ctk.CTkFrame(page, corner_radius=12)
        card.pack(fill="x")
        self.edit_pdf_path = tk.StringVar()
        self.picker(card, self.edit_pdf_path, self.pick_edit_pdf)

        info = ctk.CTkFrame(page, fg_color="transparent")        # row: page count + Reset
        info.pack(fill="x", pady=(10, 0))
        self.pages_info = ctk.CTkLabel(info, text="", text_color=MUTED)
        self.pages_info.pack(side="left")
        ctk.CTkButton(info, text="Reset", width=80, fg_color="transparent", border_width=1,
                      text_color=TEXT, command=self.reset_pages).pack(side="right")

        self.page_items = []        # one dict per page: {"orig": original index, "rot": degrees, "thumb": image}
        self.image_cache = {}       # (orig, rot) -> CTkImage, so rows rebuild quickly
        self.page_list = ctk.CTkScrollableFrame(page, height=150)
        self.page_list.pack(fill="both", expand=True, pady=(6, 0))
        self.refresh_pages()
        self.action_button(page, "Save as new PDF", self.do_save_pages)
        self.droppable(page, self.drop_edit_pdf)

    def drop_edit_pdf(self, paths):
        path = self.first_with_extension(paths, PDF_PATTERNS)
        if path:
            self.load_edit_pdf(path)

    def pick_edit_pdf(self):
        p = ask_open_file([("PDF files", PDF_PATTERNS)])
        if p:
            self.load_edit_pdf(p)

    def load_edit_pdf(self, path):
        self.edit_pdf_path.set(path)
        self.busy("Loading pages...")
        run_in_background(lambda: make_page_thumbnails(path), self.pages_loaded, self.failed, self)

    def pages_loaded(self, thumbs):
        self.loaded_thumbs = thumbs                 # kept so Reset can start over
        self.reset_pages()
        self.finished(f"Loaded {len(thumbs)} page(s)")

    def reset_pages(self):
        thumbs = getattr(self, "loaded_thumbs", [])
        self.page_items = [{"orig": i, "rot": 0, "thumb": t} for i, t in enumerate(thumbs)]
        self.image_cache.clear()
        self.refresh_pages()

    def page_image(self, item):
        """The thumbnail for a page, turned by its current rotation."""
        key = (item["orig"], item["rot"])
        if key not in self.image_cache:
            img = item["thumb"]
            if item["rot"]:
                img = img.rotate(-item["rot"], expand=True)   # PIL turns anticlockwise, so negate
            self.image_cache[key] = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
        return self.image_cache[key]

    def refresh_pages(self):
        """Rebuild all the page rows from self.page_items."""
        for child in self.page_list.winfo_children():
            child.destroy()
        self.pages_info.configure(text=f"{len(self.page_items)} page(s)")

        if not self.page_items:
            ctk.CTkLabel(self.page_list, text="Open or drop a PDF to see its pages here.",
                         text_color=MUTED).pack(pady=30)
            return

        for i, item in enumerate(self.page_items):
            row = ctk.CTkFrame(self.page_list, corner_radius=8)
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text="", image=self.page_image(item),
                         width=84, height=80).pack(side="left", padx=6, pady=4)
            note = f"   (turned {item['rot']}°)" if item["rot"] else ""
            ctk.CTkLabel(row, text=f"Page {i + 1}   ·   was page {item['orig'] + 1}{note}",
                         anchor="w").pack(side="left", fill="x", expand=True)
            # i=i freezes the current value of i for each button
            ctk.CTkButton(row, text="✕", width=34, fg_color="transparent", text_color=TEXT,
                          hover_color=("#F2B8B5", "#6B2B28"),
                          command=lambda i=i: self.delete_page(i)).pack(side="right", padx=(0, 6))
            ctk.CTkButton(row, text="↓", width=34, fg_color="transparent", text_color=TEXT,
                          command=lambda i=i: self.move_page(i, 1)).pack(side="right")
            ctk.CTkButton(row, text="↑", width=34, fg_color="transparent", text_color=TEXT,
                          command=lambda i=i: self.move_page(i, -1)).pack(side="right")
            ctk.CTkButton(row, text="↻", width=34, fg_color="transparent", text_color=TEXT,
                          command=lambda i=i: self.rotate_page(i, 90)).pack(side="right")
            ctk.CTkButton(row, text="↺", width=34, fg_color="transparent", text_color=TEXT,
                          command=lambda i=i: self.rotate_page(i, -90)).pack(side="right")
            self.droppable(row, self.drop_edit_pdf)           # the new row accepts drops too

    def rotate_page(self, i, degrees):
        item = self.page_items[i]
        item["rot"] = (item["rot"] + degrees) % 360    # keeps it at 0, 90, 180 or 270
        self.refresh_pages()

    def move_page(self, i, step):
        j = i + step                                   # -1 = up, +1 = down
        if 0 <= j < len(self.page_items):
            self.page_items[i], self.page_items[j] = self.page_items[j], self.page_items[i]
            self.refresh_pages()

    def delete_page(self, i):
        del self.page_items[i]
        self.refresh_pages()

    def do_save_pages(self):
        src = self.edit_pdf_path.get()
        if not src or not self.page_items:
            messagebox.showinfo("PDF pages", "Open a PDF and keep at least one page.")
            return
        out = ask_save_file(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
        if not out:
            return
        if os.path.abspath(out) == os.path.abspath(src):
            messagebox.showinfo("PDF pages", "Save under a different name, so your original stays safe.")
            return
        pages = [(item["orig"], item["rot"]) for item in self.page_items]   # the new plan
        self.busy("Saving PDF...")
        run_in_background(
            lambda: edit_pdf_pages(src, out, pages),
            lambda _: self.finished(f"Saved {out}", out),
            self.failed, self)

    # ---------- Page: Video to GIF ----------
    def build_gif_page(self, page):
        self.header(page, "Video to GIF", "Turn a short part of a video into an animated GIF.")
        card = ctk.CTkFrame(page, corner_radius=12)
        card.pack(fill="x")
        self.gif_video = tk.StringVar()
        self.picker(card, self.gif_video, self.pick_gif_video)

        opts = ctk.CTkFrame(card, fg_color="transparent")
        opts.pack(fill="x", padx=16, pady=(0, 8))
        self.gif_start = tk.StringVar(value="0")
        self.gif_length = tk.StringVar(value="5")
        self.gif_width = tk.StringVar(value="480")
        self.gif_fps = tk.StringVar(value="12")
        # grid = rows and columns: (row, column) places each widget
        ctk.CTkLabel(opts, text="Start at (seconds)").grid(row=0, column=0, sticky="w", pady=6)
        ctk.CTkEntry(opts, textvariable=self.gif_start, width=90).grid(row=0, column=1, padx=(10, 40))
        ctk.CTkLabel(opts, text="Length (seconds)").grid(row=0, column=2, sticky="w")
        ctk.CTkEntry(opts, textvariable=self.gif_length, width=90).grid(row=0, column=3, padx=10)
        ctk.CTkLabel(opts, text="Width (pixels)").grid(row=1, column=0, sticky="w", pady=6)
        ctk.CTkOptionMenu(opts, values=["320", "480", "640", "800"], variable=self.gif_width,
                          width=90).grid(row=1, column=1, padx=(10, 40))
        ctk.CTkLabel(opts, text="Smoothness (fps)").grid(row=1, column=2, sticky="w")
        ctk.CTkOptionMenu(opts, values=["8", "12", "15", "20"], variable=self.gif_fps,
                          width=90).grid(row=1, column=3, padx=10)

        ctk.CTkLabel(card, text="Tip: 3 to 8 seconds at 480 pixels keeps the GIF small. Max length is 30 seconds.",
                     text_color=MUTED, anchor="w").pack(fill="x", padx=16, pady=(0, 14))
        self.action_button(page, "Create GIF", self.do_gif)
        self.droppable(page, lambda paths: self.drop_into(self.gif_video, paths, VIDEO_PATTERNS))

    def pick_gif_video(self):
        p = ask_open_file([("Video files", VIDEO_PATTERNS), ("All files", "*.*")])
        if p:
            self.gif_video.set(p)

    def do_gif(self):
        src = self.gif_video.get()
        if not src:
            messagebox.showinfo("Video to GIF", "Choose a video first.")
            return
        try:
            start = float(self.gif_start.get())
            length = float(self.gif_length.get())
        except ValueError:                             # the user typed letters, not a number
            messagebox.showinfo("Video to GIF", "Start and length must be numbers, like 0 and 5.")
            return
        if start < 0 or length <= 0 or length > 30:
            messagebox.showinfo("Video to GIF", "Start must be 0 or more, and length between 0 and 30 seconds.")
            return
        width = int(self.gif_width.get())
        fps = int(self.gif_fps.get())
        default = os.path.splitext(os.path.basename(src))[0] + ".gif"
        out = ask_save_file(initialfile=default, defaultextension=".gif", filetypes=[("GIF", "*.gif")])
        if not out:
            return
        self.busy("Creating GIF...")
        report = self.progress_reporter("Creating GIF...")
        run_in_background(
            lambda: video_to_gif(src, out, fps=fps, width=width, start=start, duration=length, on_percent=report),
            lambda _: self.finished(f"Saved {out}", out),
            self.failed, self)

    # ---------- Page: YouTube audio ----------
    def build_youtube_page(self, page):
        self.header(page, "YouTube audio", "Paste a YouTube link and save its audio as a file.")
        card = ctk.CTkFrame(page, corner_radius=12)
        card.pack(fill="x")

        # no textvariable here: CustomTkinter hides the placeholder text if one is attached
        self.yt_entry = ctk.CTkEntry(card, height=38, placeholder_text="Paste a YouTube link here")
        self.yt_entry.pack(fill="x", padx=16, pady=(16, 8))

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(row, text="Format:").pack(side="left", padx=(0, 8))
        self.yt_format = tk.StringVar(value="mp3")
        ctk.CTkOptionMenu(row, values=["mp3", "m4a", "wav", "flac"], variable=self.yt_format,
                          width=90).pack(side="left")

        self.yt_folder = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Downloads"))
        folder_row = ctk.CTkFrame(card, fg_color="transparent")
        folder_row.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(folder_row, text="Save to:").pack(side="left", padx=(0, 8))
        ctk.CTkEntry(folder_row, textvariable=self.yt_folder).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(folder_row, text="Browse", width=100,
                      command=self.pick_yt_folder).pack(side="left", padx=(10, 0))

        ctk.CTkLabel(card, text="Only download videos you own or have permission to save. "
                                "If downloads stop working, run: python -m pip install -U yt-dlp",
                     text_color=MUTED, anchor="w", justify="left",
                     wraplength=560).pack(fill="x", padx=16, pady=(0, 14))
        self.action_button(page, "Download audio", self.do_youtube)

    def pick_yt_folder(self):
        p = ask_folder("Choose where to save")
        if p:
            self.yt_folder.set(p)

    def do_youtube(self):
        url = self.yt_entry.get().strip()
        if not url.startswith(("http://", "https://")):
            messagebox.showinfo("YouTube audio", "Paste a full link that starts with https://")
            return
        folder = self.yt_folder.get().strip()
        if not folder:
            messagebox.showinfo("YouTube audio", "Choose a folder to save into.")
            return
        os.makedirs(folder, exist_ok=True)             # create the folder if it doesn't exist
        fmt = self.yt_format.get()

        def on_progress(message):                      # runs in the worker thread...
            self.after(0, lambda: self.set_status(message, TEXT))   # ...so hand it to the window thread

        self.busy("Starting download...")
        run_in_background(
            lambda: youtube_to_audio(url, folder, fmt, on_progress),
            lambda title: self.finished(f"Saved '{title}' into {folder}", folder),
            self.failed, self)

    # ---------- Page: Video convert ----------
    def build_vconvert_page(self, page):
        self.header(page, "Video convert", "Change the type of videos, for example MKV to MP4.")
        self.vconvert_list = FileList(page, [("Video files", VIDEO_PATTERNS)], height=150)
        self.vconvert_list.pack(fill="both", expand=True)

        options = ctk.CTkFrame(page, corner_radius=12)
        options.pack(fill="x", pady=(12, 0))
        row1 = ctk.CTkFrame(options, fg_color="transparent")
        row1.pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(row1, text="Convert to:").pack(side="left", padx=(0, 8))
        self.vconvert_format = tk.StringVar(value="mp4")
        ctk.CTkOptionMenu(row1, values=VIDEO_FORMATS, variable=self.vconvert_format,
                          width=90).pack(side="left")
        ctk.CTkLabel(row1, text="Quality:").pack(side="left", padx=(30, 8))
        self.vconvert_quality = tk.StringVar(value="Good")
        ctk.CTkOptionMenu(row1, values=list(VIDEO_QUALITY), variable=self.vconvert_quality,
                          width=130).pack(side="left")

        self.vconvert_fast = tk.BooleanVar(value=False)    # the checkbox state
        ctk.CTkCheckBox(options, text="Fast mode: keep the original quality, no re-encoding (when possible)",
                        variable=self.vconvert_fast).pack(anchor="w", padx=16, pady=(6, 14))

        self.action_button(page, "Convert videos", self.do_vconvert)
        self.droppable(page, self.vconvert_list.add_paths)

    def do_vconvert(self):
        paths = self.vconvert_list.paths()
        if not paths:
            messagebox.showinfo("Video convert", "Add at least one video.")
            return
        target = self.vconvert_format.get()
        quality = self.vconvert_quality.get()
        fast = self.vconvert_fast.get()
        folder = ask_folder("Choose output folder")
        if not folder:
            return

        def work():
            used = set()                                   # output names already taken in this batch
            for number, src in enumerate(paths, start=1):
                base = os.path.splitext(os.path.basename(src))[0]
                out = free_output_path(folder, base, target, src, used)
                label = f"Converting {number} of {len(paths)}: {os.path.basename(src)}"
                convert_video(src, out, quality, fast, self.progress_reporter(label, number, len(paths)))
            return len(paths)

        self.busy("Converting videos... this can take a while")
        run_in_background(work, lambda n: self.finished(f"Converted {n} video(s) into {folder}", folder),
                          self.failed, self)

    # ---------- Page: Trim video ----------
    def build_trim_page(self, page):
        self.header(page, "Trim video", "Cut out a part of a video and save it as a new file.")
        card = ctk.CTkFrame(page, corner_radius=12)
        card.pack(fill="x")
        self.trim_video_path = tk.StringVar()
        self.picker(card, self.trim_video_path, self.pick_trim_video)
        self.trim_info = ctk.CTkLabel(card, text="", text_color=MUTED, anchor="w")
        self.trim_info.pack(fill="x", padx=16, pady=(0, 6))

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 6))
        ctk.CTkLabel(row, text="Start at").pack(side="left", padx=(0, 8))
        self.trim_start = ctk.CTkEntry(row, width=110)
        self.trim_start.insert(0, "0:00")
        self.trim_start.pack(side="left")
        ctk.CTkLabel(row, text="End at").pack(side="left", padx=(30, 8))
        self.trim_end = ctk.CTkEntry(row, width=110, placeholder_text="end of video")
        self.trim_end.pack(side="left")

        ctk.CTkLabel(card, text="Type times like 90, 1:30 or 0:01:30.5. Leave 'End at' empty to cut until the end.",
                     text_color=MUTED, anchor="w").pack(fill="x", padx=16, pady=(0, 8))
        self.trim_fast = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(card, text="Fast cut: instant and no quality loss, but the start can be a few seconds early",
                        variable=self.trim_fast).pack(anchor="w", padx=16, pady=(0, 16))

        self.action_button(page, "Trim video", self.do_trim)
        self.droppable(page, self.drop_trim_video)

    def drop_trim_video(self, paths):
        path = self.first_with_extension(paths, VIDEO_PATTERNS)
        if path:
            self.set_trim_video(path)

    def pick_trim_video(self):
        p = ask_open_file([("Video files", VIDEO_PATTERNS), ("All files", "*.*")])
        if p:
            self.set_trim_video(p)

    def set_trim_video(self, path):
        self.trim_video_path.set(path)
        length = probe_duration(path)                      # quick: only reads the file's header
        self.trim_info.configure(text=f"Length: {format_time(length)}" if length else "Length: unknown")

    def do_trim(self):
        src = self.trim_video_path.get()
        if not src:
            messagebox.showinfo("Trim video", "Choose a video first.")
            return
        try:
            start = parse_time(self.trim_start.get() or "0")
            end_text = self.trim_end.get().strip()
            end = parse_time(end_text) if end_text else None
        except ValueError:
            messagebox.showinfo("Trim video", "Type times like 90, 1:30 or 0:01:30.5.")
            return
        total = probe_duration(src)
        if total and start >= total:
            messagebox.showinfo("Trim video", f"The video is only {format_time(total)} long.")
            return
        if end is not None and total:
            end = min(end, total)                          # an end after the video ends just means "to the end"
        if end is not None and end <= start:
            messagebox.showinfo("Trim video", "'End at' must be later than 'Start at'.")
            return
        fast = self.trim_fast.get()
        ext = os.path.splitext(src)[1] if fast else ".mp4"   # copying keeps the type; re-encoding makes an mp4
        default = os.path.splitext(os.path.basename(src))[0] + "_trimmed" + ext
        out = ask_save_file(initialfile=default, defaultextension=ext, filetypes=[("Video", f"*{ext}")])
        if not out:
            return
        self.busy("Trimming...")
        report = self.progress_reporter("Trimming...")
        run_in_background(
            lambda: trim_video(src, out, start, end, fast, report),
            lambda _: self.finished(f"Saved {out}", out),
            self.failed, self)

    # ---------- Page: Compress video ----------
    def build_compress_page(self, page):
        self.header(page, "Compress video", "Make big videos smaller. They are saved as MP4 files.")
        self.compress_list = FileList(page, [("Video files", VIDEO_PATTERNS)], height=150)
        self.compress_list.pack(fill="both", expand=True)

        options = ctk.CTkFrame(page, corner_radius=12)
        options.pack(fill="x", pady=(12, 0))
        row = ctk.CTkFrame(options, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=14)
        ctk.CTkLabel(row, text="Picture size:").pack(side="left", padx=(0, 8))
        self.compress_size = tk.StringVar(value="Keep size")
        ctk.CTkOptionMenu(row, values=list(VIDEO_MAX_HEIGHT), variable=self.compress_size,
                          width=120).pack(side="left")
        ctk.CTkLabel(row, text="Compression:").pack(side="left", padx=(30, 8))
        self.compress_level = tk.StringVar(value="Balanced")
        ctk.CTkOptionMenu(row, values=list(VIDEO_COMPRESS_LEVELS), variable=self.compress_level,
                          width=120).pack(side="left")

        self.action_button(page, "Compress videos", self.do_compress)
        self.droppable(page, self.compress_list.add_paths)

    def do_compress(self):
        paths = self.compress_list.paths()
        if not paths:
            messagebox.showinfo("Compress video", "Add at least one video.")
            return
        height = VIDEO_MAX_HEIGHT[self.compress_size.get()]
        level = self.compress_level.get()
        folder = ask_folder("Choose output folder")
        if not folder:
            return

        def work():
            used = set()
            before = after = 0
            for number, src in enumerate(paths, start=1):
                base = os.path.splitext(os.path.basename(src))[0] + "_compressed"
                out = free_output_path(folder, base, "mp4", src, used)
                label = f"Compressing {number} of {len(paths)}: {os.path.basename(src)}"
                compress_video(src, out, level, height, self.progress_reporter(label, number, len(paths)))
                before += os.path.getsize(src)
                after += os.path.getsize(out)
            return before, after

        def done(sizes):
            before, after = sizes
            change = abs(100 - after * 100 / before) if before else 0
            word = "smaller" if after < before else "larger (it was already well compressed)"
            self.finished(f"{human_size(before)} → {human_size(after)}  ({change:.0f}% {word})", folder)

        self.busy("Compressing videos... this can take a while")
        run_in_background(work, done, self.failed, self)

    # ---------- Page: Audio convert ----------
    def build_audio_page(self, page):
        self.header(page, "Audio convert", "Change audio types, for example WAV to MP3. Videos work too.")
        self.audio_list = FileList(page, [("Audio and video files", AUDIO_PATTERNS + " " + VIDEO_PATTERNS)],
                                   height=150)
        self.audio_list.pack(fill="both", expand=True)

        options = ctk.CTkFrame(page, corner_radius=12)
        options.pack(fill="x", pady=(12, 0))
        row = ctk.CTkFrame(options, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(14, 4))
        ctk.CTkLabel(row, text="Convert to:").pack(side="left", padx=(0, 8))
        self.audio_format = tk.StringVar(value="mp3")
        ctk.CTkOptionMenu(row, values=list(AUDIO_FORMATS), variable=self.audio_format,
                          width=90).pack(side="left")
        ctk.CTkLabel(row, text="Quality:").pack(side="left", padx=(30, 8))
        self.audio_bitrate = tk.StringVar(value="192k")
        ctk.CTkOptionMenu(row, values=AUDIO_BITRATES, variable=self.audio_bitrate,
                          width=90).pack(side="left")
        ctk.CTkLabel(options, text="Higher numbers sound better but make bigger files. WAV and FLAC ignore this.",
                     text_color=MUTED, anchor="w").pack(fill="x", padx=16, pady=(0, 12))

        self.action_button(page, "Convert audio", self.do_audio)
        self.droppable(page, self.audio_list.add_paths)

    def do_audio(self):
        paths = self.audio_list.paths()
        if not paths:
            messagebox.showinfo("Audio convert", "Add at least one audio file.")
            return
        target = self.audio_format.get()
        bitrate = self.audio_bitrate.get()
        folder = ask_folder("Choose output folder")
        if not folder:
            return

        def work():
            used = set()
            for number, src in enumerate(paths, start=1):
                base = os.path.splitext(os.path.basename(src))[0]
                out = free_output_path(folder, base, target, src, used)
                label = f"Converting {number} of {len(paths)}: {os.path.basename(src)}"
                convert_audio(src, out, bitrate, self.progress_reporter(label, number, len(paths)))
            return len(paths)

        self.busy("Converting audio...")
        run_in_background(work, lambda n: self.finished(f"Converted {n} file(s) into {folder}", folder),
                          self.failed, self)

    # ---------- Page: Image tools (resize / compress / watermark) ----------
    def build_itools_page(self, page):
        self.header(page, "Image tools", "Make images smaller, lighter, or add a text watermark.")
        self.itools_list = FileList(page, [("Images", IMAGE_PATTERNS)], height=110)
        self.itools_list.pack(fill="both", expand=True)

        options = ctk.CTkFrame(page, corner_radius=12)
        options.pack(fill="x", pady=(12, 0))

        row1 = ctk.CTkFrame(options, fg_color="transparent")
        row1.pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(row1, text="Save as:").pack(side="left", padx=(0, 8))
        self.itools_type = tk.StringVar(value="Keep type")
        ctk.CTkOptionMenu(row1, values=IMAGE_OUTPUT_TYPES, variable=self.itools_type,
                          width=110).pack(side="left")
        ctk.CTkLabel(row1, text="Resize:").pack(side="left", padx=(30, 8))
        self.itools_resize = tk.StringVar(value="Keep size")
        ctk.CTkOptionMenu(row1, values=list(IMAGE_RESIZE), variable=self.itools_resize,
                          width=170).pack(side="left")

        row2 = ctk.CTkFrame(options, fg_color="transparent")
        row2.pack(fill="x", padx=16, pady=6)
        ctk.CTkLabel(row2, text="Quality:").pack(side="left", padx=(0, 8))
        self.itools_quality = tk.DoubleVar(value=85)
        self.itools_quality_label = ctk.CTkLabel(row2, text="85", width=30)
        ctk.CTkSlider(row2, from_=40, to=100, number_of_steps=60, variable=self.itools_quality, width=220,
                      command=lambda v: self.itools_quality_label.configure(text=str(int(v)))
                      ).pack(side="left")
        self.itools_quality_label.pack(side="left", padx=8)
        ctk.CTkLabel(row2, text="(JPG and WebP only. Lower = smaller file)",
                     text_color=MUTED).pack(side="left", padx=8)

        row3 = ctk.CTkFrame(options, fg_color="transparent")
        row3.pack(fill="x", padx=16, pady=(6, 14))
        ctk.CTkLabel(row3, text="Watermark:").pack(side="left", padx=(0, 8))
        self.itools_text = ctk.CTkEntry(row3, width=220, placeholder_text="text (empty = none)")
        self.itools_text.pack(side="left")
        self.itools_position = tk.StringVar(value="Bottom right")
        ctk.CTkOptionMenu(row3, values=WATERMARK_POSITIONS, variable=self.itools_position,
                          width=130).pack(side="left", padx=(12, 0))

        self.action_button(page, "Process images", self.do_itools)
        self.droppable(page, self.itools_list.add_paths)

    def do_itools(self):
        paths = self.itools_list.paths()
        if not paths:
            messagebox.showinfo("Image tools", "Add at least one image.")
            return
        choice = self.itools_type.get()
        max_width, percent = IMAGE_RESIZE[self.itools_resize.get()]
        quality = int(round(self.itools_quality.get()))
        watermark = self.itools_text.get().strip()
        position = self.itools_position.get()
        folder = ask_folder("Choose output folder")
        if not folder:
            return

        def target_type(src):
            if choice != "Keep type":
                return choice
            ext = os.path.splitext(src)[1].lower().lstrip(".")
            return ext if ext in ("jpg", "png", "webp") else "jpg"   # jpeg/heic/bmp/gif/tiff become jpg

        def work():
            used = set()
            before = after = 0
            for number, src in enumerate(paths, start=1):
                base = os.path.splitext(os.path.basename(src))[0]
                out = free_output_path(folder, base, target_type(src), src, used)
                label = f"Processing {number} of {len(paths)}: {os.path.basename(src)}"
                self.progress_reporter(label, number, len(paths))(0.0)
                process_image(src, out, max_width, percent, quality, watermark, position)
                before += os.path.getsize(src)
                after += os.path.getsize(out)
            return before, after

        def done(sizes):
            before, after = sizes
            self.finished(f"Processed {len(paths)} image(s): {human_size(before)} → {human_size(after)}", folder)

        self.busy("Processing images...")
        run_in_background(work, done, self.failed, self)


if __name__ == "__main__":          # only runs when you start app.py directly
    App().mainloop()