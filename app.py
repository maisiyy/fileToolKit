"""
File Toolkit - a local converter app (pretty version, built with CustomTkinter).

Features:
  1. Merge several PDFs into one
  2. Convert video to MP3
  3. Convert image type (png, jpg, webp, bmp, gif, tiff) and images to PDF
  4. Convert PDF pages to images (png / jpg)
  5. Rotate, reorder and delete PDF pages
  6. Convert a video clip to an animated GIF
  7. Download the audio of a YouTube link (mp3 / m4a / wav / flac)

Run with:  python app.py
"""

import os                                  # work with file names and paths
import subprocess                          # run another program (ffmpeg)
import threading                           # run work without freezing the window
import tkinter as tk                       # StringVar / BooleanVar live here
from tkinter import filedialog, messagebox # file pickers and popups

import customtkinter as ctk                # modern-looking widgets on top of tkinter
import pymupdf                             # render PDF pages to images
import imageio_ffmpeg                      # gives us the path to ffmpeg
from PIL import Image                      # Pillow: open and save images
from pypdf import PdfReader, PdfWriter     # PdfReader opens a PDF, PdfWriter builds a new one
import yt_dlp                              # downloads audio/video from YouTube and similar sites

# ---------- look and feel ----------
ctk.set_appearance_mode("dark")            # "dark", "light" or "system"
ctk.set_default_color_theme("blue")        # built-in themes: blue, green, dark-blue

# A pair like ("light value", "dark value") makes a colour that adapts to the mode
TEXT = ("gray10", "gray90")
MUTED = ("gray40", "gray60")
OK_GREEN = "#2FA572"
ERROR_RED = "#E5534B"


# ----------------------------------------------------------------------
# PART 1: CORE FUNCTIONS (no window code, so they are easy to test)
# ----------------------------------------------------------------------

def merge_pdfs(pdf_paths, output_path):
    """Join all PDFs in pdf_paths (in order) into one file."""
    writer = PdfWriter()                   # an empty PDF we will fill
    for path in pdf_paths:                 # go through the files in order
        writer.append(path)                # add every page of this PDF
    with open(output_path, "wb") as f:     # "wb" = write binary
        writer.write(f)                    # save the combined PDF
    writer.close()


def video_to_mp3(video_path, output_path):
    """Extract the audio of a video and save it as an MP3."""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()   # full path to the bundled ffmpeg
    command = [
        ffmpeg,
        "-y",                      # overwrite the output file if it exists
        "-i", video_path,          # -i = input file
        "-vn",                     # vn = "no video", keep only audio
        "-codec:a", "libmp3lame",  # encode the audio as MP3
        "-q:a", "2",               # quality: 0 is best, 9 is worst; 2 is high
        output_path,
    ]
    # On Windows this stops a black console window from flashing up
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(command, capture_output=True, text=True, creationflags=flags)
    if result.returncode != 0:     # 0 means success, anything else is an error
        raise RuntimeError(result.stderr[-500:] or "ffmpeg failed")


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


def video_to_gif(video_path, output_path, fps=12, width=480, start=0.0, duration=5.0):
    """Turn a part of a video into an animated GIF."""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    # GIFs only have 256 colours. These two filters first work out the best 256 colours
    # for this clip (palettegen) and then use them (paletteuse), which looks much better.
    filters = (f"fps={fps},scale={width}:-1:flags=lanczos,"
               "split[a][b];[a]palettegen[p];[b][p]paletteuse")
    command = [
        ffmpeg, "-y",
        "-ss", str(start),         # where to start, in seconds
        "-t", str(duration),       # how many seconds to keep
        "-i", video_path,
        "-vf", filters,
        "-loop", "0",              # 0 = repeat forever
        output_path,
    ]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(command, capture_output=True, text=True, creationflags=flags)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-500:] or "ffmpeg failed")


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

    def add(self):
        for p in filedialog.askopenfilenames(filetypes=self.filetypes):
            self.files.append(p)
        self.refresh()

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
            ctk.CTkLabel(self.body, text="No files yet. Click '+ Add files'.",
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

    def paths(self):
        return list(self.files)


# ----------------------------------------------------------------------
# PART 3: THE APP WINDOW
# ----------------------------------------------------------------------

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("File Toolkit")
        self.geometry("920x680")
        self.minsize(820, 640)

        # column 0 = sidebar (fixed), column 1 = content (stretches)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.pages = {}                       # page name -> its frame
        self.nav = {}                         # page name -> its sidebar button

        self.build_sidebar()
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew", padx=24, pady=(20, 10))
        self.build_status_bar()

        self.add_page("Merge PDF", self.build_merge_page)
        self.add_page("Video to MP3", self.build_video_page)
        self.add_page("Image convert", self.build_image_page)
        self.add_page("PDF to images", self.build_pdf_page)
        self.add_page("PDF pages", self.build_pages_page)
        self.add_page("Video to GIF", self.build_gif_page)
        self.add_page("YouTube audio", self.build_youtube_page)
        self.show_page("Merge PDF")

    # ---------- layout pieces ----------
    def build_sidebar(self):
        self.sidebar = ctk.CTkFrame(self, width=200, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        ctk.CTkLabel(self.sidebar, text="File Toolkit",
                     font=ctk.CTkFont(size=22, weight="bold")).pack(padx=20, pady=(24, 4), anchor="w")
        ctk.CTkLabel(self.sidebar, text="Convert files locally",
                     text_color=MUTED).pack(padx=20, pady=(0, 20), anchor="w")

        # packed at the bottom so it stays there under the nav buttons
        self.theme_switch = ctk.CTkSwitch(self.sidebar, text="Dark mode", command=self.toggle_theme)
        self.theme_switch.select()            # start switched on = dark
        self.theme_switch.pack(side="bottom", padx=20, pady=20, anchor="w")

    def build_status_bar(self):
        bar = ctk.CTkFrame(self, height=38, corner_radius=0)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.status_label = ctk.CTkLabel(bar, text="Ready", anchor="w")
        self.status_label.pack(side="left", padx=16, pady=8)
        # indeterminate = a bar that slides back and forth while we wait
        self.progress = ctk.CTkProgressBar(bar, mode="indeterminate", width=160)

    def add_page(self, name, builder):
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        builder(page)                         # fill the page with widgets
        self.pages[name] = page
        button = ctk.CTkButton(self.sidebar, text=name, anchor="w", height=38, corner_radius=8,
                               fg_color="transparent", text_color=TEXT,
                               hover_color=("gray75", "gray25"),
                               command=lambda: self.show_page(name))
        button.pack(fill="x", padx=12, pady=3)
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
        self.progress.pack(side="right", padx=16)         # show the moving bar
        self.progress.start()

    def finished(self, message):
        self.progress.stop()
        self.progress.pack_forget()                       # hide the bar again
        self.set_status(message, OK_GREEN)

    def failed(self, error):
        self.progress.stop()
        self.progress.pack_forget()
        self.set_status("Failed", ERROR_RED)
        messagebox.showerror("Error", str(error))

    # ---------- Page 1: Merge PDF ----------
    def build_merge_page(self, page):
        self.header(page, "Merge PDF", "Add PDFs, put them in order, and join them into one file.")
        self.merge_list = FileList(page, [("PDF files", "*.pdf")])
        self.merge_list.pack(fill="both", expand=True)
        self.action_button(page, "Merge into one PDF", self.do_merge)

    def do_merge(self):
        paths = self.merge_list.paths()
        if len(paths) < 2:
            messagebox.showinfo("Merge PDF", "Add at least two PDFs.")
            return
        out = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
        if not out:                                       # user pressed Cancel
            return
        self.busy("Merging...")
        run_in_background(
            lambda: merge_pdfs(paths, out),
            lambda _: self.finished(f"Saved {out}"),
            self.failed, self)

    # ---------- Page 2: Video to MP3 ----------
    def build_video_page(self, page):
        self.header(page, "Video to MP3", "Pick a video and save its audio as an MP3.")
        card = ctk.CTkFrame(page, corner_radius=12)
        card.pack(fill="x")
        self.video_path = tk.StringVar()                  # holds the chosen file path
        self.picker(card, self.video_path, self.pick_video)
        self.action_button(page, "Convert to MP3", self.do_video)

    def pick_video(self):
        p = filedialog.askopenfilename(filetypes=[
            ("Video files", "*.mp4 *.mkv *.avi *.mov *.webm *.flv *.wmv"), ("All files", "*.*")])
        if p:
            self.video_path.set(p)

    def do_video(self):
        src = self.video_path.get()
        if not src:
            messagebox.showinfo("Video to MP3", "Choose a video first.")
            return
        default = os.path.splitext(os.path.basename(src))[0] + ".mp3"   # suggest same name
        out = filedialog.asksaveasfilename(initialfile=default, defaultextension=".mp3",
                                           filetypes=[("MP3", "*.mp3")])
        if not out:
            return
        self.busy("Converting video... this can take a while")
        run_in_background(
            lambda: video_to_mp3(src, out),
            lambda _: self.finished(f"Saved {out}"),
            self.failed, self)

    # ---------- Page 3: Image convert ----------
    def build_image_page(self, page):
        self.header(page, "Image convert", "Change image types, or combine images into one PDF.")
        self.image_list = FileList(page, [("Images", "*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tiff")],
                                   height=150)
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

    def do_image(self):
        paths = self.image_list.paths()
        if not paths:
            messagebox.showinfo("Image convert", "Add at least one image.")
            return
        target = self.image_target.get()

        # Option A: all images become pages of a single PDF
        if target == "pdf" and self.image_one_pdf.get():
            out = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
            if not out:
                return
            self.busy("Creating PDF...")
            run_in_background(
                lambda: images_to_pdf(paths, out),
                lambda _: self.finished(f"Saved {out}"),
                self.failed, self)
            return

        # Option B: convert each image separately into a chosen folder
        folder = filedialog.askdirectory(title="Choose output folder")
        if not folder:
            return

        def work():
            for p in paths:
                name = os.path.splitext(os.path.basename(p))[0] + "." + target
                convert_image(p, os.path.join(folder, name))
            return len(paths)

        self.busy("Converting images...")
        run_in_background(work, lambda n: self.finished(f"Converted {n} image(s) into {folder}"),
                          self.failed, self)

    # ---------- Page 4: PDF to images ----------
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

    def pick_pdf(self):
        p = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
        if p:
            self.pdf_path.set(p)

    def do_pdf(self):
        src = self.pdf_path.get()
        if not src:
            messagebox.showinfo("PDF to images", "Choose a PDF first.")
            return
        folder = filedialog.askdirectory(title="Choose output folder")
        if not folder:
            return
        fmt = self.pdf_format.get()
        self.busy("Converting PDF...")
        run_in_background(
            lambda: pdf_to_images(src, folder, fmt),
            lambda n: self.finished(f"Saved {n} page(s) into {folder}"),
            self.failed, self)

    # ---------- Page 5: PDF pages (rotate / reorder / delete) ----------
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

    def pick_edit_pdf(self):
        p = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
        if not p:
            return
        self.edit_pdf_path.set(p)
        self.busy("Loading pages...")
        run_in_background(lambda: make_page_thumbnails(p), self.pages_loaded, self.failed, self)

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
            ctk.CTkLabel(self.page_list, text="Open a PDF to see its pages here.",
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
        out = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
        if not out:
            return
        if os.path.abspath(out) == os.path.abspath(src):
            messagebox.showinfo("PDF pages", "Save under a different name, so your original stays safe.")
            return
        pages = [(item["orig"], item["rot"]) for item in self.page_items]   # the new plan
        self.busy("Saving PDF...")
        run_in_background(
            lambda: edit_pdf_pages(src, out, pages),
            lambda _: self.finished(f"Saved {out}"),
            self.failed, self)

    # ---------- Page 6: Video to GIF ----------
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

    def pick_gif_video(self):
        p = filedialog.askopenfilename(filetypes=[
            ("Video files", "*.mp4 *.mkv *.avi *.mov *.webm *.flv *.wmv"), ("All files", "*.*")])
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
        out = filedialog.asksaveasfilename(initialfile=default, defaultextension=".gif",
                                           filetypes=[("GIF", "*.gif")])
        if not out:
            return
        self.busy("Creating GIF...")
        run_in_background(
            lambda: video_to_gif(src, out, fps=fps, width=width, start=start, duration=length),
            lambda _: self.finished(f"Saved {out}"),
            self.failed, self)

    # ---------- Page 7: YouTube audio ----------
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
        p = filedialog.askdirectory(title="Choose where to save")
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
            lambda title: self.finished(f"Saved '{title}' into {folder}"),
            self.failed, self)


if __name__ == "__main__":          # only runs when you start app.py directly
    App().mainloop()