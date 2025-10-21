"""
Parsers for beet output (refactored)
"""
import re
import subprocess
import logging
from config import BEET_CONTAINER, BEET_USER

logger = logging.getLogger(__name__)

# ======================================================
# 🔧 HELPERS
# ======================================================

def clean_ansi_codes(text: str) -> str:
    """Remove ANSI color and control codes from terminal output."""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)


def _run_verbose_beet(path: str, timeout: int = 60):
    """Run beet in verbose mode to extract candidates with MB IDs."""
    try:
        beet_cmd = ['beet', '-vv', 'import', '--no-autotag', path]
        cmd = (
            ['docker', 'exec', '-i', *(["-u", BEET_USER] if BEET_USER else []), BEET_CONTAINER, *beet_cmd]
            if BEET_CONTAINER else beet_cmd
        )

        logger.info(f"Running beet verbose: {' '.join(cmd)}")
        result = subprocess.run(cmd, input='\n', capture_output=True, text=True, timeout=timeout)
        return result.stdout + result.stderr

    except subprocess.TimeoutExpired:
        logger.error("Beet verbose command timed out")
        return ""
    except Exception as e:
        logger.error(f"Error executing beet verbose: {e}")
        return ""


def _result(status, path, **kwargs):
    """Utility for consistent return objects."""
    return {'status': status, 'path': path, **kwargs}


# ======================================================
# 🧩 CANDIDATE PARSING
# ======================================================

def parse_verbose_candidates(output: str):
    """Parse verbose beet output for candidate list and MB IDs."""
    cleaned = clean_ansi_codes(output)
    candidates = []

    # Pattern 1: extract candidates from verbose "Candidate: Artist - Album (uuid)"
    verbose_pattern = re.findall(
        r'Candidate:\s+(.+?)\s+\(([a-f0-9-]{36})\)', cleaned, flags=re.IGNORECASE
    )
    info_to_mbid = {info.strip(): mbid for info, mbid in verbose_pattern}

    # Pattern 2: from numbered prompt "1. (60.6%) Artist - Album"
    for num, similarity, info in re.findall(
        r'^\s*(\d+)\.\s+\(?([\d.]+)%?\)?\s+(.+?)(?:\n|$)',
        cleaned, flags=re.MULTILINE
    ):
        mb_id = None
        for verbose_info, vid in info_to_mbid.items():
            if verbose_info[:60] in info or info[:60] in verbose_info:
                mb_id = vid
                break

        candidates.append({
            'number': num,
            'info': info.strip()[:120],
            'similarity': similarity,
            'mb_id': mb_id
        })

    logger.info(f"Extracted {len(candidates)} candidates (with {len(info_to_mbid)} MBIDs)")
    return candidates


def get_candidates_with_mbids(path):
    """Run beet and extract candidates with MB IDs."""
    output = _run_verbose_beet(path)
    return parse_verbose_candidates(output) if output else []


# ======================================================
# 🎯 MATCH PARSING
# ======================================================

def parse_beet_match_info(output: str):
    """Extract detailed match information from beet output."""
    cleaned = clean_ansi_codes(output)
    info = {
        'similarity': None,
        'artist': None,
        'title': None,
        'album': None,
        'mb_url': None,
        'mb_id': None,
        'differences': []
    }

    # Similarity
    if match := re.search(r'Match\s*\(?\s*([\d.]+)%', cleaned):
        info['similarity'] = match.group(1)

    # MB URL / ID
    if mb := re.search(r'https://musicbrainz\.org/release/([a-f0-9-]{36})', cleaned):
        info['mb_id'] = mb.group(1)
        info['mb_url'] = f"https://musicbrainz.org/release/{info['mb_id']}"

    # Artist / Album / Differences
    for line in cleaned.splitlines():
        if '* Artist:' in line:
            info['artist'] = line.split('* Artist:')[1].strip()
        elif '* Album:' in line:
            info['album'] = line.split('* Album:')[1].strip()
        elif '* Title:' in line:
            info['title'] = line.split('* Title:')[1].strip()
        elif '≠' in line:
            info['differences'].append(line.strip())

    return info


# ======================================================
# 🧠 OUTPUT PARSING
# ======================================================

def parse_beet_output(stdout: str, stderr: str, path: str):
    """
    Parse beet import output and classify result.
    """
    output = clean_ansi_codes(stdout + stderr)
    low = output.lower()

    # ✅ Direct success
    if any(s in low for s in ['successfully imported', 'already in library', 'imported and tagged']):
        return _result('success', path, message='success')

    # ✅ Single match proposed
    if 'match (' in low and 'musicbrainz' in low:
        return _result('single_match', path, match_info=parse_beet_match_info(output))

    # ✅ Multiple candidates found
    candidates = parse_verbose_candidates(output)
    if candidates:
        return _result('has_candidates', path, candidates=candidates)

    # ✅ No match / low similarity
    if 'no matching release found' in low or 'no candidates' in low:
        return _result('no_match', path)
    if 'low similarity' in low:
        return _result('low_similarity', path)

    # ✅ Default fallback
    return _result('needs_input', path, output=output[:1000])