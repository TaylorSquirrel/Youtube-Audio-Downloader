import sys
import subprocess
import os
import tkinter as tk
from tkinter import messagebox, ttk
import platform
import zipfile
import urllib.request
import shutil
import threading
import json

# -----------------------------
# Auto-install required libraries
# -----------------------------
required_libs = ["yt-dlp", "Pillow", "psutil"]
for lib in required_libs:
    try:
        __import__(lib if lib != "Pillow" else "PIL")
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", lib])

# Now import them safely
import yt_dlp
from PIL import Image
import psutil

# -----------------------------
# Ensure Python 3.12
# -----------------------------
if sys.version_info[:2] != (3, 12):
    print(f"Warning: You are running Python {sys.version_info[0]}.{sys.version_info[1]}. This script requires Python 3.12.")
    sys.exit(1)

# -----------------------------
# FFmpeg Setup
# -----------------------------
FFMPEG_DIR = os.path.join(os.getcwd(), "ffmpeg_bin")
FFMPEG_EXE = os.path.join(FFMPEG_DIR, "ffmpeg.exe")

def download_ffmpeg():
    if platform.system() != "Windows":
        messagebox.showerror("Unsupported OS", "Automatic FFmpeg download only supports Windows.")
        sys.exit(1)
    os.makedirs(FFMPEG_DIR, exist_ok=True)
    zip_url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    zip_path = os.path.join(FFMPEG_DIR, "ffmpeg.zip")
    urllib.request.urlretrieve(zip_url, zip_path)
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(FFMPEG_DIR)
    for root_dir, dirs, files in os.walk(FFMPEG_DIR):
        if "ffmpeg.exe" in files:
            shutil.move(os.path.join(root_dir, "ffmpeg.exe"), FFMPEG_EXE)
            break
    os.remove(zip_path)

def check_ffmpeg():
    if os.path.isfile(FFMPEG_EXE):
        return True
    try:
        subprocess.run(['ffmpeg', '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except FileNotFoundError:
        return False

if not check_ffmpeg():
    download_ffmpeg()
    if not os.path.isfile(FFMPEG_EXE):
        messagebox.showerror("FFmpeg Error", "Failed to download FFmpeg. Please install it manually.")
        sys.exit(1)

# -----------------------------
# Setup folder
# -----------------------------
DOWNLOAD_FOLDER = os.path.join(os.getcwd(), "youtube_download")
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# -----------------------------
# URL validation
# -----------------------------
def validate_url(url):
    return "youtube.com/watch" in url or "youtu.be/" in url or "youtube.com/playlist" in url

# -----------------------------
# Progress hook
# -----------------------------
def progress_hook(d):
    if d['status'] == 'downloading':
        total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate') or 1
        downloaded_bytes = d.get('downloaded_bytes', 0)
        percent = downloaded_bytes / total_bytes * 100
        progress_var.set(percent)
        root.update_idletasks()
    elif d['status'] == 'finished':
        progress_var.set(100)
        root.update_idletasks()

# -----------------------------
# Cleanup helpers
# -----------------------------
def delete_info_json_files():
    for root_dir, dirs, files in os.walk(DOWNLOAD_FOLDER):
        for file in files:
            if file.endswith(".info.json"):
                try:
                    os.remove(os.path.join(root_dir, file))
                except:
                    pass

# -----------------------------
# Thumbnail + metadata hook
# -----------------------------
def thumbnail_and_metadata_hook(d):
    if d['status'] != 'finished':
        return

    if d.get("postprocessor") not in ("ExtractAudio", "FFmpegExtractAudio"):
        return

    info = d.get("info_dict", {})
    if not info:
        return

    source_file = info.get("_filename")
    if not source_file:
        return

    mp3_file = os.path.splitext(source_file)[0] + ".mp3"
    if not os.path.exists(mp3_file):
        return

    base = mp3_file.rsplit(".", 1)[0]
    info_json_path = base + ".info.json"
    thumb_webp = base + ".webp"
    thumb_jpg = base + ".jpg"

    if not os.path.exists(info_json_path):
        return

    with open(info_json_path, "r", encoding="utf-8") as f:
        info_json = json.load(f)

    artist = info_json.get("uploader") or info_json.get("channel") or ""
    title = info_json.get("title") or ""

    if os.path.exists(thumb_webp):
        img = Image.open(thumb_webp).convert("RGB")
        img.save(thumb_jpg)
        os.remove(thumb_webp)

    if not os.path.exists(thumb_jpg):
        return

    img = Image.open(thumb_jpg)
    w, h = img.size
    if w != h:
        new_size = max(w, h)
        square = Image.new("RGB", (new_size, new_size), (0, 0, 0))
        offset = ((new_size - w) // 2, (new_size - h) // 2)
        square.paste(img, offset)
        square.save(thumb_jpg)

    tmp_file = mp3_file + ".tmp.mp3"
    ffmpeg_cmd = [
        FFMPEG_EXE, "-y",
        "-i", mp3_file,
        "-i", thumb_jpg,
        "-map", "0",
        "-map", "1",
        "-c", "copy",
        "-id3v2_version", "3",
        "-metadata", f"artist={artist}",
        "-metadata", f"title={title}",
        tmp_file
    ]

    subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    os.replace(tmp_file, mp3_file)
    os.remove(thumb_jpg)
    delete_info_json_files()

# -----------------------------
# Detect if Chrome is running
# -----------------------------
def is_chrome_running():
    """Return True if any chrome.exe process is running"""
    for proc in psutil.process_iter(["name"]):
        if proc.info["name"] and "chrome.exe" in proc.info["name"].lower():
            return True
    return False

# -----------------------------
# YDL options
# -----------------------------
def get_ydl_opts(download_type):
    base_opts = {
        'outtmpl': os.path.join(DOWNLOAD_FOLDER, '%(title)s.%(ext)s'),
        'noplaylist': False,
        'ffmpeg_location': FFMPEG_EXE,
        'progress_hooks': [progress_hook],

        # JS runtime fix
        'js_runtimes': {'node': {}},
        'extractor_args': {'youtube': {'player_client': ['android']}},
        'retries': 10,
        'fragment_retries': 10,
        'concurrent_fragment_downloads': 1,
    }

    # Only use cookies if Chrome is not running
    if not is_chrome_running():
        base_opts['cookiesfrombrowser'] = ('chrome',)

    if download_type == "MP4":
        base_opts.update({'format': 'bv*+ba/b'})
    else:
        base_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '128',
            }],
            'writethumbnail': True,
            'writeinfojson': True,
            'postprocessor_hooks': [thumbnail_and_metadata_hook],
        })

    return base_opts

# -----------------------------
# Download logic
# -----------------------------
def download_video(url, download_type):
    try:
        status_label.config(text="Starting download...")
        root.update_idletasks()
        ydl_opts = get_ydl_opts(download_type)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        status_label.config(text="Download completed!")
        messagebox.showinfo("Success", "Download completed!")
    except Exception as e:
        messagebox.showerror("Error", str(e))
        status_label.config(text="Idle")

def start_download():
    url = url_entry.get()
    if not validate_url(url):
        messagebox.showerror("Invalid URL", "Please enter a valid YouTube video or playlist URL.")
        return
    download_type = var.get()
    threading.Thread(target=download_video, args=(url, download_type), daemon=True).start()

# -----------------------------
# GUI
# -----------------------------
root = tk.Tk()
root.title("YouTube Downloader")
root.geometry("500x250")
root.resizable(False, False)

tk.Label(root, text="YouTube Video/Playlist URL:").pack(pady=(10, 0))
url_entry = tk.Entry(root, width=60)
url_entry.pack(pady=5)

var = tk.StringVar(value="MP4")
frame_radio = tk.Frame(root)
frame_radio.pack(pady=5)
tk.Radiobutton(frame_radio, text="MP4", variable=var, value="MP4").pack(side="left", padx=20)
tk.Radiobutton(frame_radio, text="MP3", variable=var, value="MP3").pack(side="left", padx=20)

progress_var = tk.DoubleVar()
progress_bar = ttk.Progressbar(root, variable=progress_var, maximum=100, length=400)
progress_bar.pack(pady=15)

status_label = tk.Label(root, text="Idle")
status_label.pack()

tk.Button(root, text="Download", command=start_download, bg="#4CAF50", fg="white", width=20).pack(pady=10)

root.mainloop()
