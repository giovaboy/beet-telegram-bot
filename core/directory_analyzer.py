"""
Music directory analyzer
"""
import re
from pathlib import Path
from config import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS

def analyze_directory(path):
    """Analyzes a directory to understand structure and content"""
    dir_path = Path(path)
    
    subdirs = [d for d in dir_path.iterdir() if d.is_dir()]
    # Regex to find folders like 'CD1', 'Disc 2', 'disk3'
    disc_pattern = re.compile(r'(cd|disc|disk)\s*\d+', re.IGNORECASE)
    disc_dirs = [d for d in subdirs if disc_pattern.search(d.name)]
    
    if disc_dirs:
        # If disc folders are found, treat as a multi-disc album
        disc_dirs.sort(key=lambda x: x.name)
        structure = {
            'type': 'multi_disc',
            'discs': []
        }
        
        for disc_dir in disc_dirs:
            disc_info = analyze_single_dir(disc_dir)
            disc_info['name'] = disc_dir.name
            structure['discs'].append(disc_info)
        
        # Check for images in the root folder (like cover.jpg)
        structure['images'] = find_images(dir_path, recursive=False)
    else:
        # Otherwise, treat as a single album/item
        structure = {
            'type': 'single',
            **analyze_single_dir(dir_path)
        }
    
    return structure

def analyze_single_dir(path):
    """Analyzes a single directory"""
    audio_files = []
    images = []
    
    for f in sorted(path.iterdir()):
        if f.is_file():
            ext = f.suffix.lower()
            if ext in AUDIO_EXTENSIONS:
                audio_files.append({
                    'name': f.name,
                    'size': f.stat().st_size,
                    'path': str(f)
                })
            elif ext in IMAGE_EXTENSIONS:
                images.append({
                    'name': f.name,
                    'size': f.stat().st_size,
                    'path': str(f)
                })
    
    return {
        'audio_files': audio_files,
        'images': images,
        'total_size': sum(f['size'] for f in audio_files),
        'audio_count': len(audio_files)
    }

def find_images(path, recursive=True):
    """Finds all images in a directory"""
    images = []
    search_pattern = path.rglob('*') if recursive else path.glob('*')
    
    for f in search_pattern:
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS:
            images.append({
                'name': f.name,
                'size': f.stat().st_size,
                'path': str(f)
            })
    
    return images

def get_search_query(path):
    """Generates a clean search query string from the directory name"""
    dir_name = Path(path).name
    # 1. Removes standard info within parentheses or brackets
    cleaned_name = re.sub(r'[\(\[].*?[\)\]]', '', dir_name)
    # 2. Replaces non-alphanumeric characters with space
    cleaned_name = re.sub(r'[^\w\s-]', ' ', cleaned_name).strip()
    # 3. Removes multiple spaces
    cleaned_name = re.sub(r'\s+', ' ', cleaned_name).strip()
    return cleaned_name
