"""
Telegram message formatting
"""
from pathlib import Path
from i18n.translations import t

def format_directory_details(dir_name, structure):
    """Formats the directory details message"""
    msg = f"📁 *{dir_name}*\n\n"
    
    if structure['type'] == 'multi_disc':
        msg += t('directory.multi_disc', count=len(structure['discs'])) + "\n\n"
        
        for disc in structure['discs']:
            msg += f"*{disc['name']}*\n"
            msg += "  " + t('directory.tracks', 
                count=disc['audio_count'], 
                size=int(disc['total_size'] / 1024 / 1024)
            ) + "\n"
            if disc['images']:
                msg += "  " + t('directory.images', count=len(disc['images'])) + "\n"
        
        if structure['images']:
            msg += "\n" + t('directory.images_main', count=len(structure['images'])) + "\n"
    else:
        msg += t('directory.single_album') + "\n\n"
        msg += t('directory.tracks', 
            count=structure['audio_count'],
            size=int(structure['total_size'] / 1024 / 1024)
        ) + "\n"
        if structure['images']:
            msg += t('directory.images', count=len(structure['images'])) + "\n"
    
    return msg

def format_file_list(structure):
    """Formats the file list message"""
    msg = t('directory.file_list') + "\n\n"
    
    if structure['type'] == 'multi_disc':
        for disc in structure['discs']:
            msg += f"*{disc['name']}*\n"
            for f in disc['audio_files'][:20]:
                size_mb = f['size'] / 1024 / 1024
                msg += f"  • `{f['name'][:40]}` ({size_mb:.1f} MB)\n"
            if len(disc['audio_files']) > 20:
                msg += "  " + t('directory.more_files', count=len(disc['audio_files']) - 20) + "\n"
            msg += "\n"
    else:
        for f in structure['audio_files'][:30]:
            size_mb = f['size'] / 1024 / 1024
            msg += f"• `{f['name'][:40]}` ({size_mb:.1f} MB)\n"
        if len(structure['audio_files']) > 30:
            msg += "\n" + t('directory.more_files', count=len(structure['audio_files']) - 30) + "\n"
    
    return msg

def format_import_status(result):
    """Formats the import status message"""
    path = Path(result['path'])
    
    status_emoji = {
        'success': '✅', 'error': '❌', 'no_match': '🔍',
        'multiple_matches': '❓', 'has_candidates': '📋',
        'single_match': '🎯', 'waiting_input': '⏸️',
        'unknown': '❔', 'needs_input': '⏸️', 'low_similarity': '⚠️'
    }
    
    emoji = status_emoji.get(result['status'], '📝')
    
    msg = f"{emoji} {t('status.header')}\n\n"
    msg += t('fields.directory', name=path.name) + "\n\n"
    
    # Single Match
    if result['status'] == 'single_match' and 'match_info' in result:
        info = result['match_info']
        
        msg += t('status.single_match', similarity=info.get('similarity', 'N/A')) + "\n\n"
        
        if info.get('artist'):
            msg += t('fields.artist', artist=info['artist']) + "\n"
        if info.get('album'):
            msg += t('fields.album', album=info['album']) + "\n"
        if info.get('title'):
            msg += t('fields.title', title=info['title']) + "\n"
        
        if info.get('differences'):
            msg += "\n" + t('status.differences') + "\n"
            for diff in info['differences'][:3]:
                msg += f"  • {diff}\n"
        
        if info.get('mb_url'):
            msg += "\n" + t('fields.mb_link', url=info['mb_url']) + "\n"
    
    # Multiple Candidates
    elif result['status'] == 'has_candidates' and 'candidates' in result:
        msg += t('status.has_candidates', count=len(result['candidates'])) + "\n\n"
        msg += t('status.select_candidate') + "\n\n"
        
        for cand in result['candidates'][:5]:
            num_emoji = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣'][int(cand['number']) - 1] if int(cand['number']) <= 5 else f"{cand['number']}."
            msg += f"{num_emoji} `{cand['info']}` ({cand['similarity']}%)\n"
        
        if len(result['candidates']) > 5:
            msg += "\n_" + t('status.more_candidates', count=len(result['candidates']) - 5) + "_\n"
        
        msg += "\n"
    
    # Raw Output
    elif 'output' in result and result['output'].strip():
        msg += t('fields.status', status=result['status']) + "\n"
        msg += t(f'status.{result["message"]}') + "\n\n"
        
        output_lines = result['output'].split('\n')[:15]
        msg += f"```\n" + '\n'.join(output_lines) + "\n```"
        if len(result['output'].split('\n')) > 15:
            msg += "\n_" + t('status.output_truncated') + "_"
    else:
        msg += t('fields.status', status=result['status']) + "\n"
        msg += t(f'status.{result["message"]}') + "\n"
    
    return msg
