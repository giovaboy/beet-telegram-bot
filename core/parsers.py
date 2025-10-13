"""
Parser for beet output
"""
import re

def clean_ansi_codes(text):
    """Removes ANSI codes from the output"""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)

def parse_beet_match_info(output):
    """Extracts detailed match info from beet"""
    cleaned = clean_ansi_codes(output)
    
    info = {
        'similarity': None,
        'artist': None,
        'title': None,
        'album': None,
        'mb_url': None,
        'differences': []
    }
    
    # Similarity
    sim_match = re.search(r'Match\s*\(?\s*([\d.]+)%', cleaned)
    if sim_match:
        info['similarity'] = sim_match.group(1)
    
    # MusicBrainz URL
    mb_match = re.search(r'https://musicbrainz\.org/\S+', cleaned)
    if mb_match:
        info['mb_url'] = mb_match.group(0)
        # Extract ID from URL
        id_match = re.search(r'/([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})', info['mb_url'])
        if id_match:
            info['mb_id'] = id_match.group(1)
    
    # Artist, Title, Album
    for line in cleaned.split('\n'):
        if '* Artist:' in line:
            info['artist'] = line.split('* Artist:')[1].strip()
        elif '* Title:' in line or '* Album:' in line:
            parts = line.split(':', 1)
            if len(parts) == 2:
                key = parts[0].strip('* ').lower()
                info[key] = parts[1].strip()
        elif '≠' in line:
            info['differences'].append(line.strip())
    
    return info

def parse_beet_candidates(output):
    """Extracts the list of candidates from the beet output"""
    candidates = []
    cleaned = clean_ansi_codes(output)
    lines = cleaned.split('\n')
    
    in_candidates = False
    for i, line in enumerate(lines):
        if 'Candidates:' in line or 'Finding tags for' in line:
            in_candidates = True
            continue
        
        if in_candidates:
            match = re.match(r'^\s*(\d+)\.\s+(.+?)(?:\s+-\s+similarity:\s*([\d.]+)%)?$', line)
            if match:
                num = match.group(1)
                info = match.group(2).strip()
                similarity = match.group(3) if match.group(3) else "N/A"
                
                mb_id = None
                if i + 1 < len(lines):
                    next_line = lines[i + 1]
                    mb_match = re.search(r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})', next_line)
                    if mb_match:
                        mb_id = mb_match.group(1)
                
                if not mb_id:
                    mb_match = re.search(r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})', info)
                    if mb_match:
                        mb_id = mb_match.group(1)
                
                candidates.append({
                    'number': num,
                    'info': info[:100],
                    'similarity': similarity,
                    'mb_id': mb_id
                })
            
            elif line.strip() == '' or 'selection' in line.lower() or 'skip' in line.lower():
                break
    
    return candidates

def parse_beet_output(stdout, stderr, path):
    """Parses beet output to extract options"""
    output = stdout + stderr
    cleaned = clean_ansi_codes(output)
    
    # 1. Success
    if any(s in cleaned.lower() for s in ['successfully imported', 'already in library', 'imported and tagged']):
        return {
            'status': 'success',
            'message': 'success',
            'path': path
        }
    
    # 2. Single proposed match
    if 'Match (' in output and 'MusicBrainz' in output:
        match_info = parse_beet_match_info(output)
        
        return {
            'status': 'single_match',
            'message': 'single_match',
            'path': path,
            'match_info': match_info,
            'output': cleaned[:1000]
        }
    
    # 3. Multiple candidates
    candidates = parse_beet_candidates(output)
    
    if candidates:
        return {
            'status': 'has_candidates',
            'message': 'has_candidates',
            'path': path,
            'output': cleaned[:1000],
            'candidates': candidates
        }
    
    # 4. No match/low similarity
    if 'No matching release found' in cleaned or 'no candidates' in cleaned.lower():
        return {
            'status': 'no_match',
            'message': 'no_match',
            'path': path,
            'output': cleaned[:1000]
        }
    
    if 'skip' in cleaned.lower() and 'similarity' in cleaned.lower():
        return {
            'status': 'low_similarity',
            'message': 'low_similarity',
            'path': path,
            'output': cleaned[:1000]
        }
    
    # 5. Default
    return {
        'status': 'needs_input',
        'message': 'needs_input',
        'output': cleaned[:1000],
        'path': path
    }
