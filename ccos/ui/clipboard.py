"""Clipboard utilities for accessing image data."""

import base64
import mimetypes
import os
import platform
import subprocess
import tempfile
import uuid


def get_clipboard_image() -> str | None:
    """Check clipboard for an image and save it to a temp file if present.

    Returns:
        The absolute path to the saved image file, or None if no image.
    """
    system = platform.system()
    tmp_dir = os.path.join(os.path.expanduser("~"), ".ccos", "tmp_images")
    os.makedirs(tmp_dir, exist_ok=True)
    filename = f"clipboard_{uuid.uuid4().hex[:8]}.png"
    filepath = os.path.join(tmp_dir, filename)

    try:
        if system == "Darwin":
            # Check if there is an image
            check = subprocess.run(
                ["osascript", "-e", "the clipboard as «class PNGf»"],
                capture_output=True,
            )
            if check.returncode != 0:
                return None
            
            # Save it
            cmd = f"""
            set png_data to (the clipboard as «class PNGf»)
            set fp to open for access POSIX file "{filepath}" with write permission
            write png_data to fp
            close access fp
            """
            save = subprocess.run(["osascript", "-e", cmd], capture_output=True)
            if save.returncode == 0 and os.path.exists(filepath):
                return filepath

        elif system == "Linux":
            # Try xclip then wl-paste
            check_xclip = subprocess.run(
                "xclip -selection clipboard -t TARGETS -o 2>/dev/null | grep -E 'image/(png|jpeg|jpg|gif|webp|bmp)'",
                shell=True, capture_output=True
            )
            check_wl = subprocess.run(
                "wl-paste -l 2>/dev/null | grep -E 'image/(png|jpeg|jpg|gif|webp|bmp)'",
                shell=True, capture_output=True
            )
            if check_xclip.returncode != 0 and check_wl.returncode != 0:
                return None
            
            save_cmd = f'xclip -selection clipboard -t image/png -o > "{filepath}" 2>/dev/null || wl-paste --type image/png > "{filepath}" 2>/dev/null'
            save = subprocess.run(save_cmd, shell=True)
            if save.returncode == 0 and os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                return filepath

        elif system == "Windows":
            check = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "(Get-Clipboard -Format Image) -ne $null"],
                capture_output=True, text=True
            )
            if "True" not in check.stdout:
                return None
                
            ps_cmd = f"$img = Get-Clipboard -Format Image; if ($img) {{ $img.Save('{filepath}', [System.Drawing.Imaging.ImageFormat]::Png) }}"
            save = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True)
            if save.returncode == 0 and os.path.exists(filepath):
                return filepath

    except Exception:
        pass

    return None
