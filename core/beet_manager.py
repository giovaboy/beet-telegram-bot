"""
Main manager for beet operations
"""
import os
import json
import subprocess
import shutil
import logging
from pathlib import Path
from config import (
    IMPORT_PATH, STATE_FILE,
    BEET_CONTAINER, BEET_USER
)
from core.parsers import parse_beet_output, clean_ansi_codes

logger = logging.getLogger(__name__)

class BeetImportManager:
    def __init__(self):
        self.current_import = None
        self.load_state()
    
    def load_state(self):
        """Loads the current import state"""
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    self.current_import = json.load(f)
            except:
                self.current_import = None
    
    def save_state(self):
        """Saves the import state"""
        with open(STATE_FILE, 'w') as f:
            json.dump(self.current_import, f)
    
    def clear_state(self):
        """Clears the state"""
        self.current_import = None
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
    
    def get_import_directories(self):
        """Gets the list of directories in the import folder"""
        import_path = Path(IMPORT_PATH)
        if not import_path.exists():
            return []
        
        dirs = [
            d for d in import_path.iterdir() 
            if d.is_dir() and d.name not in ['skipped', '.', '..']
        ]
        
        dirs.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        
        return dirs
    
    def translate_path_for_beet(self, path):
        # NOTE: This function currently does nothing, but is left for potential future translation logic 
        # (e.g., if the bot runs outside the Docker volume scope)
        return path
    
    def search_candidates(self, path):
        """Searches for candidates without importing"""
        try:
            beet_path = self.translate_path_for_beet(path)
            
            if BEET_CONTAINER:
                cmd = ['docker', 'exec']
                if BEET_USER:
                    cmd.extend(['-u', BEET_USER])
                cmd.extend([BEET_CONTAINER, 'beet', 'ls', '-a', 'path:' + beet_path])
            else:
                cmd = ['beet', 'ls', '-a', 'path:' + beet_path]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            
            return {
                'status': 'search_result',
                'output': result.stdout + result.stderr,
                'path': path
            }
        except Exception as e:
            return {
                'status': 'error',
                'message': f'Search error: {str(e)}',
                'path': path
            }
    
    def start_import(self, path):
        """Starts an interactive import and captures the initial status"""
        try:
            beet_path = self.translate_path_for_beet(path)
            
            if BEET_CONTAINER:
                cmd = ['docker', 'exec']
                if BEET_USER:
                    cmd.extend(['-u', BEET_USER])
                cmd.extend([BEET_CONTAINER, 'beet', 'import', beet_path])
            else:
                cmd = ['beet', 'import', beet_path]
            
            # Run the command with no standard input to trigger non-interactive mode initially
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300, 
                stdin=subprocess.DEVNULL
            )
            
            output_lower = result.stdout.lower() + result.stderr.lower()
            if result.returncode == 0 and any(s in output_lower for s in ['successfully imported', 'already in library', 'imported and tagged']):
                 return {
                    'status': 'success',
                    'message': 'success',
                    'path': path
                }
            
            return parse_beet_output(result.stdout, result.stderr, path)
        
        except subprocess.TimeoutExpired as e:
            logger.error(f"Import timeout for {path}")
        
            # Correct timeout handling
            stdout = e.stdout.decode('utf-8') if e.stdout else ''
            stderr = e.stderr.decode('utf-8') if e.stderr else ''
        
            return {
                'status': 'timeout',
                'path': path,
                'message': 'Import timeout - operation too long',
                'output': stdout,
                'error': stderr,
                'candidates': []
            }
            
        except Exception as e:
            logger.error(f"Import error: {e}")
            return {
                'status': 'error',
                'message': f'Error: {str(e)}',
                'path': path
            }
    
    def import_with_id(self, path, mb_id=None, discogs_id=None):
        """Import by specifying a MusicBrainz or Discogs ID"""
        try:
            beet_path = self.translate_path_for_beet(path)
            
            if mb_id:
                # Use --search-id for MusicBrainz ID
                beet_cmd = ['beet', 'import', '--search-id', mb_id, beet_path]
            elif discogs_id:
                # Use --search with the discogs_id plugin format
                beet_cmd = ['beet', 'import', '--search', f'discogs_id::{discogs_id}', beet_path]
            else:
                return {'status': 'error', 'message': 'No ID specified'}
            
            if BEET_CONTAINER:
                # Use -i to enable piping input (needed for 'A' answer)
                cmd = ['docker', 'exec', '-i']
                if BEET_USER:
                    cmd.extend(['-u', BEET_USER])
                cmd.extend([BEET_CONTAINER] + beet_cmd)
            else:
                cmd = beet_cmd
            
            # Pipe 'A\n' (Apply) to automatically confirm the proposed match
            result = subprocess.run(cmd, input='A\n', capture_output=True, text=True, timeout=300)
            
            logger.info(f"Return code: {result.returncode}")
            logger.info(f"STDOUT: {result.stdout[:5000]}")
            logger.info(f"STDERR: {result.stderr[:500]}")
            
            output_lower = result.stdout.lower() + result.stderr.lower()
            if result.returncode == 0 and any(s in output_lower for s in ['successfully imported', 'already in library', 'imported and tagged']):
                return {'status': 'success', 'message': 'Import completed!'}
            else:
                return {
                    'status': 'error',
                    'message': f'Import error: {result.stderr[:400]}'
                }
        
        except Exception as e:
            logger.error(f"Import error with ID: {e}")
            return {'status': 'error', 'message': f'Error: {str(e)}'}
    
    def delete_directory(self, path):
        """Completely deletes a directory and all its contents"""
        dir_path = Path(path)
        
        # Security check 1: Prevent deletion of the root import path
        if str(dir_path) == IMPORT_PATH:
            return {'status': 'error', 'message': 'error_root'}

        # Security check 2: Ensure path is within the IMPORT_PATH
        if not dir_path.exists() or not str(dir_path).startswith(IMPORT_PATH):
            return {'status': 'error', 'message': 'error_not_found'}

        try:
            shutil.rmtree(dir_path)
            return {'status': 'success', 'message': f'{dir_path.name}'}
        except Exception as e:
            logger.error(f"Directory deletion error: {e}")
            return {'status': 'error', 'message': str(e)}
    
    def skip_item(self, path):
        """Moves an item to a 'skipped' folder"""
        skip_dir = Path(IMPORT_PATH) / 'skipped'
        skip_dir.mkdir(exist_ok=True)
        
        src = Path(path)
        dst = skip_dir / src.name
        
        # Handle conflicts by renaming (e.g., dir_name_1)
        counter = 1
        while dst.exists():
            dst = skip_dir / f"{src.name}_{counter}"
            counter += 1
        
        try:
            src.rename(dst)
            return {'status': 'success', 'message': f'Moved to {dst.name}'}
        except Exception as e:
            logger.error(f"Skip error: {e}")
            return {'status': 'error', 'message': str(e)}
