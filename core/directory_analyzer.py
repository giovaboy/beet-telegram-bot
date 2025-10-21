"""
Music directory analyzer (refactored)
"""
import re
from pathlib import Path
from config import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS

# Includiamo anche PDF e futuri tipi di media
MEDIA_EXTENSIONS = set(IMAGE_EXTENSIONS) | {'.pdf'}

# ======================================================
# 🔧 HELPERS
# ======================================================

def _collect_files(path: Path, extensions: set, recursive: bool = True):
    """
    Collect files matching a set of extensions.
    Returns a list of dicts with name, size, and path.
    """
    search = path.rglob('*') if recursive else path.glob('*')
    files = []
    for f in search:
        if f.is_file() and f.suffix.lower() in extensions:
            files.append({
                'name': f.name,
                'size': f.stat().st_size,
                'path': str(f)
            })
    return sorted(files, key=lambda f: f['name'].lower())


def _detect_disc_subdirs(subdirs):
    """Detect if the directory contains multi-disc folders (CD1, Disc 2, etc.)."""
    disc_pattern = re.compile(r'(cd|disc|disk)\s*\d+', re.IGNORECASE)
    return [d for d in subdirs if disc_pattern.search(d.name)]


# ======================================================
# 🎵 MAIN ANALYSIS
# ======================================================

def analyze_directory(path: str):
    """
    Analyzes a directory to understand its content and structure.
    Detects multi-disc layouts, audio files, images, and PDFs.
    """
    dir_path = Path(path)
    subdirs = [d for d in dir_path.iterdir() if d.is_dir()]
    disc_dirs = _detect_disc_subdirs(subdirs)

    # MULTI-DISC
    if disc_dirs:
        disc_dirs.sort(key=lambda x: x.name)
        structure = {'type': 'multi_disc', 'discs': []}

        for disc_dir in disc_dirs:
            disc_info = analyze_single_dir(disc_dir)
            disc_info['name'] = disc_dir.name
            structure['discs'].append(disc_info)

        # Include images/PDFs from root
        structure['images'] = find_media(dir_path, recursive=True)

    # SINGLE DISC
    else:
        structure = {'type': 'single', **analyze_single_dir(dir_path)}

    return structure


def analyze_single_dir(path: Path):
    """Analyze a single album directory for audio and media content."""
    audio_files = _collect_files(path, AUDIO_EXTENSIONS, recursive=True)
    media_files = find_media(path, recursive=True)

    return {
        'audio_files': audio_files,
        'images': media_files,
        'audio_count': len(audio_files),
        'total_size': sum(f['size'] for f in audio_files)
    }


def find_media(path: Path, recursive: bool = True):
    """Find images and PDF files within a directory."""
    media = []
    search = path.rglob('*') if recursive else path.glob('*')

    for f in search:
        if f.is_file():
            ext = f.suffix.lower()
            if ext in MEDIA_EXTENSIONS:
                media.append({
                    'name': f.name,
                    'size': f.stat().st_size,
                    'path': str(f),
                    'type': 'pdf' if ext == '.pdf' else 'image'
                })

    return sorted(media, key=lambda m: m['name'].lower())


# ======================================================
# 🔍 QUERY HELPERS
# ======================================================

def get_search_query(path: str):
    """
    Generate a clean, normalized search query string
    from a directory name (for MusicBrainz/Discogs).
    """
    dir_name = Path(path).name
    cleaned = re.sub(r'[\(\[].*?[\)\]]', '', dir_name)  # remove (...) and [...]
    cleaned = re.sub(r'[^\w\s-]', ' ', cleaned)         # keep alphanumerics/spaces/hyphens
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned