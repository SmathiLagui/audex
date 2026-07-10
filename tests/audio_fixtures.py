"""Helpers to generate tiny real audio files via ffmpeg for tag-reader tests.

pytaglib wraps TagLib (a compiled C++ extension) which validates the actual
audio container - fake bytes will not open. These helpers shell out to
ffmpeg (already present on the dev machine / CI image) to synthesise short
silent clips in each supported format, then let the audio library under
test write/read real tags on them.
"""

import subprocess
from pathlib import Path

_CODEC_BY_EXT = {
    'mp3': ['-q:a', '9'],
    'flac': ['-c:a', 'flac'],
    'm4a': ['-c:a', 'aac'],
    'ogg': ['-c:a', 'libvorbis'],
    'opus': ['-c:a', 'libopus'],
    'wav': ['-c:a', 'pcm_s16le'],
    'aac': ['-c:a', 'aac', '-f', 'adts'],
}


def make_silent_audio(path: Path, duration_s: float = 0.2) -> Path:
    """Write a short silent audio clip to `path` using ffmpeg."""
    ext = path.suffix.lstrip('.').lower()
    codec_args = _CODEC_BY_EXT[ext]
    subprocess.run(
        [
            'ffmpeg',
            '-y',
            '-f',
            'lavfi',
            '-i',
            'anullsrc=r=44100:cl=mono',
            '-t',
            str(duration_s),
            *codec_args,
            str(path),
        ],
        capture_output=True,
        check=True,
    )
    return path
