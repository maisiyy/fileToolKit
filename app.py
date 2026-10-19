"""
File Toolkit - a local converter app (pretty version, built with CustomTkinter).

Features:
  1. Merge several PDFs into one
  2. Convert video to MP3
  3. Convert image type (png, jpg, webp, bmp, gif, tiff) and images to PDF
  4. Convert PDF pages to images (png / jpg)

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
from pypdf import PdfWriter                # build a new PDF

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
        self.geometry("880x600")
        self.minsize(780, 560)

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


if __name__ == "__main__":          # only runs when you start app.py directly
    App().mainloop()