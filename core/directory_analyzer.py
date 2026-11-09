"""
Music directory analyzer (refactored)
"""
import re
from pathlib import Path
from config import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, METADATA_EXTENSIONS

MEDIA_EXTENSIONS = set(IMAGE_EXTENSIONS) | METADATA_EXTENSIONS

# ======================================================
# 🔧 HELPERS
# ======================================================

def _collect_files(path: Path, extensions: set, recursive: bool = True):
    """
    Collect files matching a set of extensions.
    Returns a list of dicts with name, size, path, and type.
    """
    search = path.rglob('*') if recursive else path.glob('*')
    files = []
    for f in search:
        if f.is_file() and f.suffix.lower() in extensions:
            # Determine file type
            ext = f.suffix.lower()
            if ext in AUDIO_EXTENSIONS:
                file_type = 'audio'
            elif ext in IMAGE_EXTENSIONS:
                file_type = 'image'
            elif ext in METADATA_EXTENSIONS:
                file_type = 'metadata'
            else:
                file_type = 'other'
            
            files.append({
                'name': f.name,
                'size': f.stat().st_size,
                'path': str(f),
                'type': file_type,
                'ext': ext
            })
    return sorted(files, key=lambda f: f['name'].lower())


def _detect_disc_subdirs(subdirs):
    """Detect if the directory contains multi-disc folders (CD1, Disc 2, etc.)."""
    disc_pattern = re.compile(r'(cd|disc|disk)\s*\d+', re.IGNORECASE)
    return [d for d in subdirs if disc_pattern.search(d.name)]


# ======================================================
# 🆕 RECURSIVE ALBUM FINDER
# ======================================================

def find_importable_albums(root_path: Path) -> list:
    """
    Recursively find all importable albums under root_path.
    
    An importable album is:
    - A directory with audio files directly (single album)
    - A directory with disc subdirs (multi-disc album)
    - NOT a parent directory containing other albums
    
    Returns:
        List of Path objects for each album directory
    """
    albums = []
    
    def is_album_dir(path: Path) -> bool:
        """Check if a directory is an album (has audio or disc subdirs)"""
        # Check for direct audio files
        has_audio = any(
            f.suffix.lower() in AUDIO_EXTENSIONS 
            for f in path.glob('*') 
            if f.is_file()
        )
        
        # Check for disc subdirs
        subdirs = [d for d in path.iterdir() if d.is_dir()]
        has_discs = len(_detect_disc_subdirs(subdirs)) > 0
        
        return has_audio or has_discs
    
    def scan_recursive(path: Path):
        """Recursively scan for albums"""
        try:
            # Check if this directory is an album
            if is_album_dir(path):
                albums.append(path)
                # Don't scan children - this is the album root
                return
            
            # Otherwise, scan subdirectories
            for subdir in path.iterdir():
                if subdir.is_dir() and not subdir.name.startswith('.'):
                    scan_recursive(subdir)
                    
        except PermissionError:
            # Skip directories we can't read
            pass
    
    scan_recursive(root_path)
    
    # Sort by modification time (newest first)
    albums.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    
    return albums


def get_relative_path(album_path: Path, root_path: Path) -> str:
    """
    Get relative path for display.
    
    Examples:
        /downloads/Album Name → "Album Name"
        /downloads/Jazz/Miles Davis → "Jazz / Miles Davis"
    """
    try:
        rel = album_path.relative_to(root_path)
        parts = list(rel.parts)
        
        if len(parts) == 1:
            # Top-level album
            return parts[0]
        else:
            # Nested album - show parent folder(s)
            return " / ".join(parts)
    except ValueError:
        # Path is not relative to root (shouldn't happen)
        return album_path.name


# ======================================================
# 🎵 MAIN ANALYSIS (unchanged)
# ======================================================

def analyze_directory(path: str):
    """
    Analyzes a directory to understand its content and structure.
    Detects multi-disc layouts, audio files, images, PDFs, and metadata files.
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

        # Include images/metadata from root
        structure['images'] = find_media(dir_path, recursive=False)  # Only root level
        structure['metadata_files'] = find_metadata(dir_path, recursive=False)

    # SINGLE DISC
    else:
        structure = {'type': 'single', **analyze_single_dir(dir_path)}

    return structure


def analyze_single_dir(path: Path):
    """Analyze a single album directory for audio, images, and metadata content."""
    audio_files = _collect_files(path, AUDIO_EXTENSIONS, recursive=True)
    media_files = find_media(path, recursive=True)
    metadata_files = find_metadata(path, recursive=True)

    return {
        'audio_files': audio_files,
        'images': [f for f in media_files if f['type'] == 'image'],
        'metadata_files': metadata_files,
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
            if ext in IMAGE_EXTENSIONS:
                media.append({
                    'name': f.name,
                    'size': f.stat().st_size,
                    'path': str(f),
                    'type': 'image',
                    'ext': ext
                })
            elif ext in METADATA_EXTENSIONS:
                media.append({
                    'name': f.name,
                    'size': f.stat().st_size,
                    'path': str(f),
                    'type': 'metadata',
                    'ext': ext
                })

    return sorted(media, key=lambda m: m['name'].lower())


def find_metadata(path: Path, recursive: bool = True):
    """Find metadata files (PDF, NFO, TXT, LOG, CUE) within a directory."""
    metadata = []
    search = path.rglob('*') if recursive else path.glob('*')

    for f in search:
        if f.is_file() and f.suffix.lower() in METADATA_EXTENSIONS:
            metadata.append({
                'name': f.name,
                'size': f.stat().st_size,
                'path': str(f),
                'type': 'metadata',
                'ext': f.suffix.lower()
            })

    return sorted(metadata, key=lambda m: m['name'].lower())

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