"""
BeetImportManager (refactored)
Uses parsers.parse_beet_output to produce a canonical import structure.
"""
import os
import json
import subprocess
import shutil
from pathlib import Path
from config import IMPORT_PATH, STATE_FILE, BEET_CONTAINER, BEET_USER, BEET_PRETEND, setup_logging
from core.parsers import parse_beet_output  # updated parser
from core.parsers import clean_ansi_codes

logger = setup_logging()

# Environment for every beet run. beet only prints a change on ONE line
# ("current -> new") when it fits the terminal width; otherwise it wraps it
# over two columns, which cannot be parsed back. Without a terminal beet
# assumes 80 columns, so we pass a large width (honoured by beet >= 2.6) and
# disable colors (color codes also break words apart when lines are wrapped).
BEET_ENV = {"NO_COLOR": "1", "COLUMNS": "2000"}


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
        """
        Return a sorted list of importable albums under the import folder.
        
        Uses recursive scanning to find all albums, including nested ones.
        Albums are sorted by modification time (newest first).
        """
        import_path = Path(IMPORT_PATH)
        if not import_path.exists():
            return []

        # 🆕 Use recursive album finder
        from core.directory_analyzer import find_importable_albums
        
        albums = find_importable_albums(import_path)
        
        logger.debug(f"Found {len(albums)} importable albums in {IMPORT_PATH}")
        
        return albums

    def _validate_path(self, path):
        """
        Ensure the entry lives strictly inside the import root.
        Checks where the entry itself is located (its parent, fully resolved),
        not where it points to: a symlink inside the import root is accepted
        (rmtree refuses to delete symlinks anyway, skip just moves the link).
        Rejects things like '/downloads2/x' or '/downloads/../music'.
        """
        dir_path = Path(os.path.abspath(path))  # lexically collapses '..'
        if not dir_path.exists() and not dir_path.is_symlink():
            return "error_not_found"
        root = Path(IMPORT_PATH).resolve()
        location = dir_path.parent.resolve() / dir_path.name
        if location == root:
            return "error_root"
        if not location.is_relative_to(root):
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
            for name, value in BEET_ENV.items():
                cmd.extend(["-e", f"{name}={value}"])
            cmd.extend([BEET_CONTAINER] + beet_args)
        else:
            cmd = beet_args
        return cmd

    def _run_command(self, beet_args, input_data=None, timeout=600, interactive=False):
        """Execute beet command and return subprocess result"""
        cmd = self._build_command(beet_args, interactive)
        try:
            result = subprocess.run(
                cmd,
                input=input_data,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, **BEET_ENV},  # used when beet runs locally
            )
            self._log_result(cmd, result)
            return result
        except subprocess.TimeoutExpired as e:
            logger.error(f"Timeout executing: {' '.join(cmd)}")

            # If we are in a Docker container, kill the process
            # if BEET_CONTAINER:
            #     try:
            #         # Kill all beet processes still running inside the container
            #         kill_cmd = ["docker", "exec", BEET_CONTAINER, "pkill", "-f", "beet import"]
            #         subprocess.run(kill_cmd, capture_output=True, text=True, timeout=10)
            #         logger.warning(f"Killed hanging beet process inside container {BEET_CONTAINER}")
            #     except Exception as kill_err:
            #         logger.error(f"Failed to kill hanging beet process: {kill_err}")

            return type('Result', (), {
                'returncode': -1,
                'stdout': '',
                'stderr': f'Command timed out after {timeout}s',
                'args': cmd
            })()
        except Exception as e:
            logger.error(f"Error executing beet: {e}", exc_info=True)
            return type('Result', (), {
                'returncode': -1,
                'stdout': '',
                'stderr': str(e),
                'args': cmd
            })()

    def _log_result(self, cmd, result):
        """Log standardized subprocess output"""
        logger.debug(f"[BEET CMD] {' '.join(cmd)}")
        if not result:
            return
        logger.debug(f"[RETURN] {result.returncode}")
        if result.stdout:
            logger.debug(result.stdout[:8000])
        if result.stderr:
            logger.warning(result.stderr[:8000])

    # ======================================================
    # SEARCH & IMPORT OPERATIONS
    # ======================================================
    def search_candidates(self, path):
        """Search for candidate matches without importing"""
        beet_path = self.translate_path_for_beet(path)
        result = self._run_command(["beet", "ls", "-a", f"path:{beet_path}"], timeout=600)
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
        if BEET_PRETEND:
            pretend = "--pretend"
        else:
            pretend = "-t"
        result = self._run_command(["beet", "-vv", "import", pretend, beet_path], timeout=600)

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


    def import_with_id(self, path, id=None, auto_apply=False):
        """
        Import a release by specifying a MusicBrainz or Discogs ID.
        If auto_apply is False we run beet and send the 'B' (abort) to get a preview,
        which is then parsed and returned as 'needs_confirmation' with match info.

        Returns:
            Dict with keys:
            - status: 'success' | 'needs_confirmation' | 'error'
            - message: str (error description if status='error')
            - output: str (raw beet output)
            - preview: dict (parsed preview if status='needs_confirmation')
        """
        if not (id):
            return {
                "status": "error",
                "message": "No ID specified",
                "output": ""
            }

        beet_path = self.translate_path_for_beet(path)

        if BEET_PRETEND:
            pretend = "--pretend"
        else:
            pretend = "-t"

        beet_args = ["beet", "import", pretend, "--search-id", id, beet_path]

        # Use "A" to accept, "B" to cancel/preview
        stdin_input = "A\n" if auto_apply else "B\n"

        result = self._run_command(
            beet_args,
            input_data=stdin_input,
            timeout=600,
            interactive=True
        )

        if not result:
            return {
                "status": "error",
                "message": "Import command failed to execute",
                "output": ""
            }

        output = (result.stdout or "") + "\n" + (result.stderr or "")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Case 1: Auto-apply mode (final import)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        if auto_apply:
            if result.returncode == 0:
                # ✅ SUCCESS: Update state and return
                self.current_import = {
                    "status": "success",
                    "path": path,
                    "message": "Import completed successfully",
                    "output": output
                }
                self.save_state()

                return {
                    "status": "success",
                    "message": "Import completed successfully",
                    "output": output
                }
            else:
                # ❌ ERROR: Command failed
                err = (result.stderr or "").strip() or (result.stdout or "")[-500:]
                return {
                    "status": "error",
                    "message": err or "Import failed with unknown error",
                    "output": output
                }

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Case 2: Preview mode (auto_apply=False)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        parsed_preview = parse_beet_output(result.stdout, result.stderr, path)

        # ✅ KEY CHANGE: DON'T overwrite self.current_import during preview
        # The original state with candidates must remain intact until confirmation

        # If parsed_preview is valid, return it WITHOUT persisting
        if parsed_preview.get('status') in ['single_match', 'has_candidates', 'needs_input', 'low_similarity']:
            return {
                "status": "needs_confirmation",
                "preview": parsed_preview,
                "output": output
            }
        else:
            # Error case during preview
            err = (result.stderr or "").strip() or (result.stdout or "")[-500:]
            return {
                "status": "error",
                "message": err or "Preview failed with unknown error",
                "output": output
            }

    def import_as_is(self, path):
        """
        Import keeping the current tags (answers 'U' = use as-is to beet's prompt).

        Returns {'status': 'success'|'error', 'message': str, 'output': str}
        """
        beet_path = self.translate_path_for_beet(path)
        beet_args = ["beet", "import"]
        if BEET_PRETEND:
            beet_args.append("--pretend")
        beet_args.append(beet_path)

        result = self._run_command(beet_args, input_data="U\n", timeout=600, interactive=True)
        output = (result.stdout or "") + "\n" + (result.stderr or "")

        if result.returncode == 0:
            return {"status": "success", "message": "Import completed successfully", "output": output}

        err = (result.stderr or "").strip() or (result.stdout or "")[-500:]
        return {"status": "error", "message": err or "Import failed with unknown error", "output": output}

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
        err = self._validate_path(path)
        if err:
            return {"status": "error", "message": err}

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