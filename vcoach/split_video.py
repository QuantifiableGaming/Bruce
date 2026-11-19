"""Cut a long game video into parts that are small enough to send for testing.

The parts are exact copies of the video (no quality loss). The sound is left out.
The tool uses the ffmpeg program from the imageio-ffmpeg package. It installs the package if it is missing.
"""
import os
import re
import subprocess
import sys

PART_MB = 200   # Parts of about 270 MB timed out when they were copied to Claude (19 Nov).


def _ffmpeg():
    try:
        import imageio_ffmpeg
    except ImportError:
        print("Installing the video tool (imageio-ffmpeg). This takes a minute...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "imageio-ffmpeg"])
        import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _duration(ffmpeg, path):
    """Return the video length in seconds."""
    out = subprocess.run([ffmpeg, "-hide_banner", "-i", path], capture_output=True, text=True,
                         errors="replace").stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", out)
    if not m:
        raise SystemExit("Could not read the length of the video. Is it a video file?")
    h, mi, s = m.groups()
    video = re.search(r"Video:\s*(\w+)", out)
    return int(h) * 3600 + int(mi) * 60 + float(s), (video.group(1) if video else "?")


def split(path, out_root=None):
    if not os.path.isfile(path):
        raise SystemExit(f"File not found: {path}")
    ffmpeg = _ffmpeg()
    seconds, codec = _duration(ffmpeg, path)
    size_mb = os.path.getsize(path) / 1e6
    # Cut a little short of the limit, because parts start at key frames and vary in size.
    part_s = max(20, int(seconds * PART_MB * 0.85 / size_mb))
    name = re.sub(r"[^\w-]+", "_", os.path.splitext(os.path.basename(path))[0])[:40]
    # The parts go next to the video, in a "parts" folder. They do not go into the coach folder,
    # because a coach folder on the Desktop is often in OneDrive, which uploads every file.
    out_dir = os.path.join(out_root or os.path.join(os.path.dirname(os.path.abspath(path)), "parts"), name)
    os.makedirs(out_dir, exist_ok=True)
    old = [f for f in os.listdir(out_dir) if re.match(r"part_\d{3}\.mp4$", f)]
    for f in old:
        os.remove(os.path.join(out_dir, f))   # Old parts of this video, from an earlier split.
    if old:
        print(f"Removed {len(old)} old parts of this video.")
    print(f"Video: {size_mb / 1000:.1f} GB, {seconds / 60:.1f} minutes, codec {codec}.")
    print(f"Cutting it into parts of about {part_s // 60} min {part_s % 60} s in {out_dir} ...")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", path, "-map", "0:v:0", "-c", "copy", "-an",
           "-f", "segment", "-segment_time", str(part_s), "-reset_timestamps", "1",
           os.path.join(out_dir, "part_%03d.mp4")]
    subprocess.check_call(cmd)
    parts = sorted(f for f in os.listdir(out_dir) if f.startswith("part_"))
    big = [f for f in parts if os.path.getsize(os.path.join(out_dir, f)) > 300e6]
    print(f"Done: {len(parts)} parts.")
    if big:
        print(f"Warning: {len(big)} parts are larger than 300 MB: {', '.join(big)}")
    print("Tell Claude that the parts are ready.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Drag a video file onto the Valorant Coach shortcut (or Start Coach.bat).")
    for p in sys.argv[1:]:
        split(p)
