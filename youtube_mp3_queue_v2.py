
import os
import sys
import re
import json
import shutil
import subprocess
import threading
import queue
import venv
import platform
import urllib.request
import zipfile
import datetime
import logging
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

MUSIC_DIR = Path.home() / "Music"

if MUSIC_DIR.is_dir():
    DOWNLOAD_DIR = MUSIC_DIR
else:
    DOWNLOAD_DIR = BASE_DIR / "Music"
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

VENV_DIR = BASE_DIR / ".youtube_mp3_venv"
LOG_FILE = BASE_DIR / "youtube_mp3_debug.log"

IS_WINDOWS = platform.system() == "Windows"

OUTPUT_TEMPLATE = str(
    DOWNLOAD_DIR / "%(title).180B [%(id)s].%(ext)s"
)

THUMBNAIL_TEMPLATE = str(
    DOWNLOAD_DIR / "%(id)s.%(ext)s"
)
# ============================================================
# DEBUG LOGGER
# ============================================================

class DebugLogger:

    def __init__(self):
        self.ui_queue = None
        self.lock = threading.Lock()

        logging.basicConfig(
            filename=str(LOG_FILE),
            level=logging.DEBUG,
            format="%(asctime)s [%(levelname)s] %(message)s",
            encoding="utf-8",
            force=True
        )

    def log(self, message, level="INFO"):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] [{level}] {message}"

        with self.lock:
            print(line, flush=True)

            if level == "ERROR":
                logging.error(message)
            elif level == "WARNING":
                logging.warning(message)
            else:
                logging.info(message)

            if self.ui_queue is not None:
                self.ui_queue.put(("debug", line))


debugger = DebugLogger()


# ============================================================
# VIRTUAL ENVIRONMENT
# ============================================================

def current_python():
    if IS_WINDOWS:
        return VENV_DIR / "Scripts" / "python.exe"

    return VENV_DIR / "bin" / "python"


def ensure_venv():
    python_exe = current_python()

    if not python_exe.exists():
        debugger.log("Creating virtual environment.")

        venv.EnvBuilder(
            with_pip=True,
            system_site_packages=not IS_WINDOWS
        ).create(VENV_DIR)

    debugger.log("Installing/updating Python dependencies.")

    subprocess.check_call([
        str(python_exe),
        "-m",
        "pip",
        "install",
        "--upgrade",
        "yt-dlp",
        "Pillow"
    ])

    if Path(sys.executable).resolve() != python_exe.resolve():
        debugger.log("Restarting inside virtual environment.")

        os.execv(
            str(python_exe),
            [str(python_exe), str(Path(__file__).resolve())]
        )


# ============================================================
# FFMPEG SETUP
# ============================================================

def install_windows_ffmpeg():
    ffmpeg_dir = (
        Path(os.environ.get("LOCALAPPDATA", str(BASE_DIR)))
        / "YouTubeMP3Queue"
        / "ffmpeg"
    )

    ffmpeg_dir.mkdir(parents=True, exist_ok=True)

    existing = list(ffmpeg_dir.rglob("ffmpeg.exe"))

    if existing:
        debugger.log(f"Using FFmpeg: {existing[0]}")
        return str(existing[0])

    url = (
        "https://www.gyan.dev/ffmpeg/builds/"
        "ffmpeg-release-essentials.zip"
    )

    zip_path = ffmpeg_dir / "ffmpeg.zip"

    debugger.log("Downloading FFmpeg.")

    urllib.request.urlretrieve(url, zip_path)

    with zipfile.ZipFile(zip_path, "r") as archive:
        archive.extractall(ffmpeg_dir)

    zip_path.unlink(missing_ok=True)

    found = list(ffmpeg_dir.rglob("ffmpeg.exe"))

    if not found:
        raise RuntimeError("FFmpeg installation failed.")

    debugger.log(f"FFmpeg installed: {found[0]}")

    return str(found[0])


def ensure_linux_ffmpeg():
    if shutil.which("ffmpeg"):
        debugger.log("FFmpeg found.")
        return True

    debugger.log("FFmpeg not found.", "WARNING")

    if os.geteuid() != 0:
        debugger.log(
            "Install FFmpeg with your Linux package manager.",
            "ERROR"
        )
        return False

    commands = [
        ["apt-get", "install", "-y", "ffmpeg"],
        ["dnf", "install", "-y", "ffmpeg"],
        ["pacman", "-S", "--noconfirm", "ffmpeg"],
        ["zypper", "--non-interactive", "install", "ffmpeg"]
    ]

    for command in commands:
        if shutil.which(command[0]):
            subprocess.run(command, check=False)
            break

    return shutil.which("ffmpeg") is not None


# ============================================================
# INITIALIZE DEPENDENCIES
# ============================================================

ensure_venv()

if IS_WINDOWS:
    FFMPEG = install_windows_ffmpeg()
else:
    if not ensure_linux_ffmpeg():
        input("Install FFmpeg and restart. Press Enter to exit.")
        sys.exit(1)

    FFMPEG = shutil.which("ffmpeg")


# ============================================================
# THIRD-PARTY IMPORTS
# ============================================================

from PIL import Image, ImageTk
import yt_dlp


# ============================================================
# METADATA AND FILE HELPERS
# ============================================================

def extract_video_id(path):
    match = re.search(
        r"\[([A-Za-z0-9_-]{11})\]$",
        path.stem
    )

    return match.group(1) if match else None


def find_thumbnail(mp3_path):
    video_id = extract_video_id(mp3_path)

    if not video_id:
        return None

    for path in DOWNLOAD_DIR.glob(video_id + ".*"):
        if path.suffix.lower() in (
            ".jpg", ".jpeg", ".png", ".webp"
        ):
            return path

    return None


def find_info_json(mp3_path):
    video_id = extract_video_id(mp3_path)

    if not video_id:
        return None

    for path in DOWNLOAD_DIR.glob("*.info.json"):
        if video_id in path.name:
            return path

    return None


def load_metadata(mp3_path):
    info_path = find_info_json(mp3_path)

    if not info_path:
        return {}

    try:
        with open(info_path, "r", encoding="utf-8") as file:
            return json.load(file)

    except Exception as error:
        debugger.log(f"Metadata reading error: {error}", "ERROR")
        return {}


def get_metadata_tags(info):
    tags = {}

    title = info.get("track") or info.get("title")

    if title:
        tags["title"] = str(title)

    artist = (
        info.get("artist")
        or info.get("artists")
        or info.get("creator")
        or info.get("uploader")
        or info.get("channel")
    )

    if isinstance(artist, list):
        artist = ", ".join(str(item) for item in artist)

    if artist:
        tags["artist"] = str(artist)
        tags["album_artist"] = str(artist)

    album = info.get("album")

    if album:
        tags["album"] = str(album)

    date = (
        info.get("release_date")
        or info.get("upload_date")
    )

    if date and len(str(date)) == 8:
        date = (
            str(date)[:4] + "-"
            + str(date)[4:6] + "-"
            + str(date)[6:8]
        )

    if date:
        tags["date"] = str(date)

    genre = info.get("genre")

    if isinstance(genre, list):
        genre = ", ".join(str(item) for item in genre)

    if genre:
        tags["genre"] = str(genre)

    if info.get("track_number"):
        tags["track"] = str(info["track_number"])

    if info.get("disc_number"):
        tags["disc"] = str(info["disc_number"])

    if info.get("description"):
        tags["comment"] = str(info["description"])[:4000]

    return tags


def clean_mp3_filename(mp3_path):
    new_name = re.sub(
        r"\s*\[[A-Za-z0-9_-]{11}\](?=\.mp3$)",
        "",
        mp3_path.name
    )

    new_path = mp3_path.with_name(new_name)

    if new_path == mp3_path:
        return mp3_path

    if new_path.exists():
        counter = 2

        while True:
            candidate = mp3_path.with_name(
                f"{new_path.stem} ({counter}){new_path.suffix}"
            )

            if not candidate.exists():
                new_path = candidate
                break

            counter += 1

    mp3_path.rename(new_path)

    debugger.log(
        f"Renamed {mp3_path.name} -> {new_path.name}"
    )

    return new_path


# ============================================================
# ARTWORK CROPPING
# ============================================================

def create_artwork(source, mode):
    image = Image.open(source).convert("RGB")

    if mode == "original":
        return image

    width, height = image.size
    shortest = min(width, height)

    if mode == "square":
        crop_size = shortest

    elif mode == "smaller":
        crop_size = int(shortest * 0.85)

    else:
        return image

    left = (width - crop_size) // 2
    top = (height - crop_size) // 2

    return image.crop((
        left,
        top,
        left + crop_size,
        top + crop_size
    ))


# ============================================================
# EMBED METADATA AND COVER ART
# ============================================================

def finalize_mp3(mp3_path, thumbnail=None, mode=None):
    debugger.log(f"Finalizing: {mp3_path.name}")

    info = load_metadata(mp3_path)
    tags = get_metadata_tags(info)

    debugger.log(f"Metadata fields found: {list(tags.keys())}")

    temp_mp3 = mp3_path.with_name(
        mp3_path.stem + "_temporary.mp3"
    )

    temp_image = DOWNLOAD_DIR / "temporary_cover.jpg"

    command = [
        FFMPEG,
        "-y",
        "-i", str(mp3_path)
    ]

    has_artwork = thumbnail is not None and mode is not None

    if has_artwork:
        artwork = create_artwork(thumbnail, mode)
        artwork.save(temp_image, "JPEG", quality=95)

        command.extend([
            "-i", str(temp_image),
            "-map", "0:a:0",
            "-map", "1:v:0"
        ])
    else:
        command.extend([
            "-map", "0:a:0"
        ])

    command.extend([
        "-map_metadata", "0",
        "-c:a", "copy",
        "-id3v2_version", "3"
    ])

    if has_artwork:
        command.extend([
            "-c:v", "mjpeg",
            "-disposition:v:0", "attached_pic",
            "-metadata:s:v", "title=Album cover",
            "-metadata:s:v", "comment=Cover (front)"
        ])

    for key, value in tags.items():
        command.extend([
            "-metadata",
            f"{key}={value}"
        ])

    command.append(str(temp_mp3))

    debugger.log("Running FFmpeg:")
    debugger.log(subprocess.list2cmdline(command))

    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if result.stderr:
            for line in result.stderr.splitlines():
                debugger.log("FFmpeg: " + line)

        if result.returncode != 0:
            raise RuntimeError(
                result.stderr[-3000:]
            )

        os.replace(temp_mp3, mp3_path)

        debugger.log("Metadata/artwork successfully embedded.")

    finally:
        temp_image.unlink(missing_ok=True)
        temp_mp3.unlink(missing_ok=True)


# ============================================================
# TEMPORARY FILE CLEANUP
# ============================================================

def cleanup_song_files(mp3_path):
    video_id = extract_video_id(mp3_path)

    if not video_id:
        return

    thumbnail = find_thumbnail(mp3_path)
    info_json = find_info_json(mp3_path)

    for path in (thumbnail, info_json):
        if path:
            try:
                path.unlink(missing_ok=True)
                debugger.log(f"Deleted temporary file: {path.name}")

            except OSError as error:
                debugger.log(
                    f"Could not delete {path.name}: {error}",
                    "WARNING"
                )


# ============================================================
# YT-DLP LOGGER AND PROGRESS HOOK
# ============================================================

class YTDLPDebugLogger:

    def debug(self, message):
        debugger.log("yt-dlp: " + str(message))

    def info(self, message):
        debugger.log("yt-dlp: " + str(message))

    def warning(self, message):
        debugger.log("yt-dlp WARNING: " + str(message), "WARNING")

    def error(self, message):
        debugger.log("yt-dlp ERROR: " + str(message), "ERROR")


def progress_hook(data):
    status = data.get("status")

    if status == "downloading":
        filename = Path(data.get("filename", "download")).name
        percent = data.get("_percent_str", "").strip()
        speed = data.get("_speed_str", "").strip()
        eta = data.get("_eta_str", "").strip()

        debugger.log(
            f"{filename} | {percent} | Speed: {speed} | ETA: {eta}"
        )

    elif status == "finished":
        debugger.log(
            f"Download completed: {data.get('filename', '')}"
        )


# ============================================================
# APPLICATION
# ============================================================

class YouTubeMP3App:

    def __init__(self, root):
        self.root = root

        self.root.title("YouTube MP3 Queue")
        self.root.geometry("850x780")
        self.root.minsize(650, 600)

        self.jobs = queue.Queue()
        debugger.ui_queue = self.jobs

        self.downloaded_files = []
        self.artwork_index = 0
        self.busy = False
        self.current_dialog = None

        self.build_ui()

        debugger.log("Application started.")
        debugger.log(f"Download directory: {DOWNLOAD_DIR}")
        debugger.log(f"FFmpeg: {FFMPEG}")

        self.root.after(100, self.check_jobs)

    # ========================================================
    # UI
    # ========================================================

    def build_ui(self):
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)

        ttk.Label(
            main,
            text="YouTube MP3 Downloader",
            font=("Arial", 18, "bold")
        ).pack(pady=(0, 10))

        ttk.Label(
            main,
            text="Paste a YouTube URL:"
        ).pack(anchor="w")

        url_frame = ttk.Frame(main)
        url_frame.pack(fill="x", pady=5)

        self.url_entry = ttk.Entry(url_frame)
        self.url_entry.pack(side="left", fill="x", expand=True)

        self.add_button = ttk.Button(
            url_frame,
            text="Add to Queue",
            command=self.add_url
        )
        self.add_button.pack(side="left", padx=6)

        ttk.Label(
            main,
            text="Download queue:"
        ).pack(anchor="w", pady=(10, 4))

        self.queue_list = tk.Listbox(main, height=6)
        self.queue_list.pack(fill="x")

        button_frame = ttk.Frame(main)
        button_frame.pack(fill="x", pady=8)

        self.remove_button = ttk.Button(
            button_frame,
            text="Remove Selected",
            command=self.remove_selected
        )
        self.remove_button.pack(side="left")

        self.download_button = ttk.Button(
            button_frame,
            text="Download Queue",
            command=self.start_downloads
        )
        self.download_button.pack(side="right")

        self.status = ttk.Label(main, text="Ready")
        self.status.pack(anchor="w", pady=(5, 3))

        self.progress = ttk.Progressbar(
            main,
            mode="indeterminate"
        )
        self.progress.pack(fill="x", pady=5)

        ttk.Separator(main).pack(fill="x", pady=8)

        debug_header = ttk.Frame(main)
        debug_header.pack(fill="x")

        ttk.Label(
            debug_header,
            text="Debug Console",
            font=("Arial", 11, "bold")
        ).pack(side="left")

        ttk.Button(
            debug_header,
            text="Clear",
            command=self.clear_debug
        ).pack(side="right")

        ttk.Button(
            debug_header,
            text="Open Log File",
            command=self.open_log_file
        ).pack(side="right", padx=5)

        debug_frame = ttk.Frame(main)
        debug_frame.pack(fill="both", expand=True, pady=5)

        self.debug_console = tk.Text(
            debug_frame,
            height=14,
            wrap="word",
            state="disabled",
            background="#151515",
            foreground="#00ff66",
            insertbackground="white",
            font=("Consolas", 9)
        )

        debug_scrollbar = ttk.Scrollbar(
            debug_frame,
            command=self.debug_console.yview
        )

        self.debug_console.configure(
            yscrollcommand=debug_scrollbar.set
        )

        self.debug_console.pack(
            side="left",
            fill="both",
            expand=True
        )

        debug_scrollbar.pack(side="right", fill="y")

    # ========================================================
    # DEBUG UI
    # ========================================================

    def write_debug(self, message):
        self.debug_console.config(state="normal")

        self.debug_console.insert(
            tk.END,
            message + "\n"
        )

        self.debug_console.see(tk.END)
        self.debug_console.config(state="disabled")

    def clear_debug(self):
        self.debug_console.config(state="normal")
        self.debug_console.delete("1.0", tk.END)
        self.debug_console.config(state="disabled")

    def open_log_file(self):
        try:
            if IS_WINDOWS:
                os.startfile(LOG_FILE)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(LOG_FILE)])
            else:
                subprocess.Popen(["xdg-open", str(LOG_FILE)])

        except Exception as error:
            messagebox.showerror(
                "Log error",
                str(error)
            )

    # ========================================================
    # QUEUE
    # ========================================================

    def add_url(self):
        url = self.url_entry.get().strip()

        if not url:
            return

        self.queue_list.insert(tk.END, url)
        self.url_entry.delete(0, tk.END)

        debugger.log(f"Added to queue: {url}")

    def remove_selected(self):
        selected = self.queue_list.curselection()

        if selected:
            value = self.queue_list.get(selected[0])
            self.queue_list.delete(selected[0])
            debugger.log(f"Removed from queue: {value}")

    def start_downloads(self):
        if self.busy:
            return

        urls = list(self.queue_list.get(0, tk.END))

        if not urls:
            messagebox.showinfo(
                "Queue empty",
                "Add at least one YouTube URL."
            )
            return

        self.busy = True

        self.download_button.config(state="disabled")
        self.add_button.config(state="disabled")
        self.remove_button.config(state="disabled")

        self.progress.start(10)
        self.status.config(text="Downloading...")

        self.queue_list.delete(0, tk.END)

        debugger.log(f"Starting queue: {len(urls)} URL(s).")

        threading.Thread(
            target=self.download_worker,
            args=(urls,),
            daemon=True
        ).start()

    # ========================================================
    # DOWNLOAD WORKER
    # ========================================================

    def download_worker(self, urls):
        downloaded = []

        for index, url in enumerate(urls, start=1):
            self.jobs.put((
                "status",
                f"Downloading {index} of {len(urls)}..."
            ))

            debugger.log(
                f"Starting URL {index}/{len(urls)}: {url}"
            )

            before = {
                path: path.stat().st_mtime_ns
                for path in DOWNLOAD_DIR.glob("*.mp3")
            }

            options = {
                "format": "bestaudio/best",

                "outtmpl": {
                    "default": OUTPUT_TEMPLATE,
                    "thumbnail": THUMBNAIL_TEMPLATE
                },

                "writethumbnail": True,
                "writeinfojson": True,

                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192"
                    },
                    {
                        "key": "FFmpegMetadata",
                        "add_metadata": True,
                        "add_chapters": True
                    }
                ],

                "ffmpeg_location": FFMPEG,
                "logger": YTDLPDebugLogger(),
                "progress_hooks": [progress_hook],

                "ignoreerrors": True,
                "noplaylist": False,
                "quiet": False,
                "no_warnings": False
            }

            try:
                with yt_dlp.YoutubeDL(options) as downloader:
                    downloader.download([url])

                debugger.log(f"Finished URL: {url}")

            except Exception as error:
                debugger.log(
                    f"DOWNLOAD FAILED: {error}",
                    "ERROR"
                )

                self.jobs.put((
                    "error",
                    f"Download error:\n{error}"
                ))

            for path in DOWNLOAD_DIR.glob("*.mp3"):
                try:
                    current_time = path.stat().st_mtime_ns
                    previous_time = before.get(path)

                    if (
                        previous_time is None
                        or current_time != previous_time
                    ):
                        if path not in downloaded:
                            downloaded.append(path)
                            debugger.log(
                                f"Added downloaded file: {path.name}"
                            )

                except OSError as error:
                    debugger.log(
                        f"File detection error: {error}",
                        "ERROR"
                    )

        debugger.log(
            f"Download stage finished. Found {len(downloaded)} MP3 file(s)."
        )

        self.jobs.put((
            "downloads_finished",
            downloaded
        ))

    # ========================================================
    # JOB HANDLER
    # ========================================================

    def check_jobs(self):
        try:
            while True:
                job = self.jobs.get_nowait()
                kind = job[0]

                if kind == "debug":
                    self.write_debug(job[1])

                elif kind == "status":
                    self.status.config(text=job[1])

                elif kind == "error":
                    messagebox.showerror(
                        "Download error",
                        job[1]
                    )

                elif kind == "downloads_finished":
                    self.progress.stop()

                    self.downloaded_files = job[1]
                    self.artwork_index = 0

                    if self.downloaded_files:
                        self.status.config(
                            text="Downloads finished. Reviewing artwork..."
                        )

                        self.open_next_artwork()

                    else:
                        self.finish()

                elif kind == "song_finished":
                    dialog, success, error = job[1]

                    if success:
                        debugger.log("Song processing completed.")

                        if dialog and dialog.winfo_exists():
                            dialog.destroy()

                        self.artwork_index += 1
                        self.open_next_artwork()

                    else:
                        debugger.log(
                            f"SONG PROCESSING ERROR: {error}",
                            "ERROR"
                        )

                        messagebox.showerror(
                            "Processing error",
                            error
                        )

                        if dialog and dialog.winfo_exists():
                            self.set_editor_buttons(dialog, True)

        except queue.Empty:
            pass

        self.root.after(100, self.check_jobs)

    # ========================================================
    # PER-SONG ARTWORK EDITOR
    # ========================================================

    def open_next_artwork(self):
        if self.artwork_index >= len(self.downloaded_files):
            self.finish()
            return

        mp3_path = self.downloaded_files[self.artwork_index]

        if not mp3_path.exists():
            debugger.log(
                f"Missing MP3 file: {mp3_path}",
                "WARNING"
            )

            self.artwork_index += 1
            self.open_next_artwork()
            return

        thumbnail = find_thumbnail(mp3_path)

        if thumbnail is None:
            debugger.log(
                f"No thumbnail found for {mp3_path.name}",
                "WARNING"
            )

            answer = messagebox.askyesno(
                "Thumbnail not found",
                f"No thumbnail found for:\n{mp3_path.name}\n\n"
                "Save metadata without artwork?"
            )

            if answer:
                self.process_song(
                    None,
                    mp3_path,
                    None,
                    None
                )
            else:
                self.process_song(
                    None,
                    mp3_path,
                    None,
                    None
                )

            return

        self.show_artwork_editor(
            mp3_path,
            thumbnail
        )

    def show_artwork_editor(self, mp3_path, thumbnail):
        dialog = tk.Toplevel(self.root)

        self.current_dialog = dialog

        dialog.title(
            f"Artwork {self.artwork_index + 1} of "
            f"{len(self.downloaded_files)}"
        )

        dialog.geometry("900x590")
        dialog.minsize(750, 500)

        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(
            dialog,
            text=mp3_path.name,
            font=("Arial", 12, "bold")
        ).pack(pady=10)

        ttk.Label(
            dialog,
            text="Choose the artwork style for this song:"
        ).pack()

        preview_frame = ttk.Frame(
            dialog,
            padding=10
        )

        preview_frame.pack(
            fill="both",
            expand=True
        )

        choices = [
            ("original", "As is"),
            ("square", "Square crop"),
            ("smaller", "Smaller square")
        ]

        selected_mode = tk.StringVar(value="original")
        photo_references = []

        for mode, label in choices:
            column = ttk.Frame(
                preview_frame,
                padding=8
            )

            column.pack(
                side="left",
                fill="both",
                expand=True
            )

            try:
                preview = create_artwork(
                    thumbnail,
                    mode
                )

                preview.thumbnail((240, 240))

                photo = ImageTk.PhotoImage(preview)
                photo_references.append(photo)

                ttk.Label(
                    column,
                    image=photo
                ).pack(pady=8)

            except Exception as error:
                debugger.log(
                    f"Preview error: {error}",
                    "ERROR"
                )

                ttk.Label(
                    column,
                    text=f"Preview unavailable:\n{error}"
                ).pack(pady=8)

            ttk.Radiobutton(
                column,
                text=label,
                variable=selected_mode,
                value=mode
            ).pack(pady=8)

        dialog.photo_references = photo_references

        button_frame = ttk.Frame(
            dialog,
            padding=10
        )

        button_frame.pack(fill="x")

        save_button = ttk.Button(
            button_frame,
            text="Save & Next",
            command=lambda: self.process_song(
                dialog,
                mp3_path,
                thumbnail,
                selected_mode.get()
            )
        )

        save_button.pack(side="right", padx=5)

        skip_button = ttk.Button(
            button_frame,
            text="Skip This Song",
            command=lambda: self.process_song(
                dialog,
                mp3_path,
                None,
                None
            )
        )

        skip_button.pack(side="right", padx=5)

        dialog.save_button = save_button
        dialog.skip_button = skip_button

        dialog.protocol(
            "WM_DELETE_WINDOW",
            lambda: self.process_song(
                dialog,
                mp3_path,
                None,
                None
            )
        )

    def set_editor_buttons(self, dialog, enabled):
        if not dialog or not dialog.winfo_exists():
            return

        state = "normal" if enabled else "disabled"

        dialog.save_button.config(state=state)
        dialog.skip_button.config(state=state)

    # ========================================================
    # PROCESS SONG
    # ========================================================

    def process_song(
        self,
        dialog,
        mp3_path,
        thumbnail,
        mode
    ):
        if dialog and dialog.winfo_exists():
            self.set_editor_buttons(dialog, False)

        debugger.log(f"Processing: {mp3_path.name}")
        debugger.log(f"Artwork selection: {mode or 'Skipped'}")

        self.status.config(
            text=f"Saving metadata and artwork: {mp3_path.name}"
        )

        def worker():
            try:
                finalize_mp3(
                    mp3_path,
                    thumbnail,
                    mode
                )

                debugger.log(
                    f"Successfully processed: {mp3_path.name}"
                )

                self.jobs.put((
                    "song_finished",
                    (dialog, True, None)
                ))

            except Exception as error:
                debugger.log(
                    f"Processing failed: {error}",
                    "ERROR"
                )

                self.jobs.put((
                    "song_finished",
                    (dialog, False, str(error))
                ))

        threading.Thread(
            target=worker,
            daemon=True
        ).start()

    # ========================================================
    # FINAL CLEANUP
    # ========================================================

    def finish(self):
        debugger.log("Starting final cleanup.")

        for mp3_path in self.downloaded_files:
            try:
                if not mp3_path.exists():
                    continue

                cleanup_song_files(mp3_path)
                clean_mp3_filename(mp3_path)

            except Exception as error:
                debugger.log(
                    f"Cleanup error for {mp3_path.name}: {error}",
                    "ERROR"
                )

        self.busy = False

        self.progress.stop()

        self.download_button.config(state="normal")
        self.add_button.config(state="normal")
        self.remove_button.config(state="normal")

        self.status.config(
            text=f"Finished. Files saved in:\n{DOWNLOAD_DIR}"
        )

        debugger.log("Queue processing complete.")

        messagebox.showinfo(
            "Finished",
            "All downloads have been processed.\n\n"
            f"Files saved in:\n{DOWNLOAD_DIR}\n\n"
            f"Debug log:\n{LOG_FILE}"
        )


# ============================================================
# START APPLICATION
# ============================================================

if __name__ == "__main__":
    try:
        root = tk.Tk()
        app = YouTubeMP3App(root)
        root.mainloop()

    except Exception as error:
        debugger.log(
            f"FATAL APPLICATION ERROR: {error}",
            "ERROR"
        )
        raise

