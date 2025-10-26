"""
BeetImportManager (refactored)
Uses parsers.parse_beet_output to produce a canonical import structure.
"""
import os
import json
import subprocess
import shutil
from pathlib import Path
from config import IMPORT_PATH, STATE_FILE, BEET_CONTAINER, BEET_USER, setup_logging
from core.parsers import parse_beet_output  # updated parser
from core.parsers import clean_ansi_codes

logger = setup_logging()


class BeetImportManager:
    def __init__(self):
        self.current_import = None
        self.load_state()

    # ======================================================
    # STATE MANAGEMENT
    # ======================================================
    def load_state(self):
        """Load current import state from JSON file"""
        try:
            if Path(STATE_FILE).exists():
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    self.current_import = json.load(f)
            else:
                self.current_import = None
        except Exception as e:
            logger.warning(f"Failed to load state: {e}")
            self.current_import = None

    def save_state(self):
        """Persist current import state"""
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.current_import, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def clear_state(self):
        """Clear current import state and delete file"""
        self.current_import = None
        try:
            Path(STATE_FILE).unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"Could not clear state: {e}")

    # ======================================================
    # DIRECTORY OPERATIONS
    # ======================================================
    def get_import_directories(self):
        """Return a sorted list of directories under the import folder"""
        import_path = Path(IMPORT_PATH)
        if not import_path.exists():
            return []

        dirs = [
            d for d in import_path.iterdir()
            if d.is_dir() and d.name not in ["skipped", ".", ".."]
        ]
        dirs.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        return dirs

    def _validate_path(self, path):
        """Ensure the path is inside the import root"""
        dir_path = Path(path)
        if not dir_path.exists():
            return "error_not_found"
        if str(dir_path) == str(IMPORT_PATH):
            return "error_root"
        if not str(dir_path).startswith(str(IMPORT_PATH)):
            return "error_not_found"
        return None

    def translate_path_for_beet(self, path):
        """Translate a local path to the path beet expects (for Docker setups)"""
        return path

    # ======================================================
    # SUBPROCESS HELPERS
    # ======================================================
    def _build_command(self, beet_args, interactive=False):
        """Build the full command array for beet, supporting Docker"""
        if BEET_CONTAINER:
            cmd = ["docker", "exec"]
            if interactive:
                cmd.append("-i")
            if BEET_USER:
                cmd.extend(["-u", BEET_USER])
            cmd.extend([BEET_CONTAINER] + beet_args)
        else:
            cmd = beet_args
        return cmd

    def _run_command(self, beet_args, input_data=None, timeout=300, interactive=False):
        """Execute beet command and return subprocess result"""
        cmd = self._build_command(beet_args, interactive)
        try:
            result = subprocess.run(
                cmd,
                input=input_data,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            self._log_result(cmd, result)
            return result
        except subprocess.TimeoutExpired:
            logger.error(f"Timeout executing beet command: {' '.join(cmd)}")
            return None
        except Exception as e:
            logger.error(f"Error executing beet command: {e}")
            return None

    def _log_result(self, cmd, result):
        """Log standardized subprocess output"""
        logger.debug(f"[BEET CMD] {' '.join(cmd)}")
        if not result:
            return
        logger.debug(f"[RETURN] {result.returncode}")
        if result.stdout:
            logger.debug(result.stdout[:2000])
        if result.stderr:
            logger.warning(result.stderr[:2000])

    # ======================================================
    # SEARCH & IMPORT OPERATIONS
    # ======================================================
    def search_candidates(self, path):
        """Search for candidate matches without importing"""
        beet_path = self.translate_path_for_beet(path)
        result = self._run_command(["beet", "ls", "-a", f"path:{beet_path}"], timeout=10)
        if not result:
            return {"status": "error", "message": "Search failed", "path": path}

        return {
            "status": "search_result",
            "output": (result.stdout or "") + (result.stderr or ""),
            "path": path,
        }

    def start_import(self, path):
        """
        Start an import and parse the output to build the canonical structure.
        This function returns the canonical dict and also sets manager.current_import.
        """
        beet_path = self.translate_path_for_beet(path)
        result = self._run_command(["beet", "-vv", "import", beet_path], timeout=300)

        if not result:
            parsed = {
                'status': 'error',
                'path': path,
                'has_multiple_candidates': False,
                'selected_index': None,
                'candidates': [],
                'single_match': None,
                'raw_output': '',
                'timestamp': None
            }
            self.current_import = parsed
            return parsed
        logger.debug('pre parseoutput')
        parsed = parse_beet_output(result.stdout, result.stderr, path)
        # Persist parsed into manager state
        self.current_import = parsed
        self.save_state()
        return parsed

    def import_with_id(self, path, mb_id=None, discogs_id=None, auto_apply=False):
        """
        Import a release by specifying a MusicBrainz or Discogs ID.
        If auto_apply is False we run beet and send the 'B' (abort) to get a preview,
        which is then parsed and returned as 'needs_confirmation' with match info.
        """
        if not (mb_id or discogs_id):
            return {"status": "error", "message": "No ID specified"}

        beet_path = self.translate_path_for_beet(path)
        if mb_id:
            beet_args = ["beet", "import", "--search-id", mb_id, beet_path]
        else:
            beet_args = ["beet", "import", "--search", f"discogs_id::{discogs_id}", beet_path]

        # Use "A" to accept, "B" to cancel/preview
        stdin_input = "A\n" if auto_apply else "B\n"

        result = self._run_command(
            beet_args,
            input_data=stdin_input,
            timeout=300,
            interactive=True
        )

        if not result:
            return {"status": "error", "message": "Import failed", "output": ""}

        output = (result.stdout or "") + "\n" + (result.stderr or "")

        # If we auto-applied and beet returned 0 assume success (best-effort)
        if result.returncode == 0 and auto_apply:
            return {"status": "success", "message": "Import completed!", "output": output}

        # If we previewed (auto_apply=False) we parse the output and provide a
        # preview structure consistent with parse_beet_output (single match case).
        parsed_preview = parse_beet_output(result.stdout, result.stderr, path)
        # If parsed_preview is single_match or has_candidates; return it as preview
        if parsed_preview.get('status') in ['single_match', 'has_candidates', 'needs_input', 'low_similarity']:
            return {"status": "needs_confirmation", "preview": parsed_preview, "output": output}
        else:
            # otherwise bubble up an error message
            err = (result.stderr or "").strip() or (result.stdout or "")[-500:]
            return {"status": "error", "message": err or "Import failed", "output": output}

    # ======================================================
    # FILE MANAGEMENT
    # ======================================================
    def delete_directory(self, path):
        """Delete a directory safely"""
        err = self._validate_path(path)
        if err:
            return {"status": "error", "message": err}

        try:
            shutil.rmtree(path)
            return {"status": "success", "message": Path(path).name}
        except Exception as e:
            logger.error(f"Failed to delete directory: {e}")
            return {"status": "error", "message": str(e)}

    def skip_item(self, path):
        """Move a directory to 'skipped' folder"""
        skip_dir = Path(IMPORT_PATH) / "skipped"
        skip_dir.mkdir(exist_ok=True)
        src = Path(path)
        dst = skip_dir / src.name

        # Rename if conflict
        counter = 1
        while dst.exists():
            dst = skip_dir / f"{src.name}_{counter}"
            counter += 1

        try:
            shutil.move(str(src), str(dst))
            return {"status": "success", "message": f"Moved to {dst.name}"}
        except Exception as e:
            logger.error(f"Skip failed: {e}")
            return {"status": "error", "message": str(e)}