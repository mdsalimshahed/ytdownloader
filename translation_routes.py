# --- translation_routes.py ---
import requests
import urllib.parse
from flask import Blueprint, request, jsonify

translation_bp = Blueprint('translation', __name__)

@translation_bp.route('/api/translate', methods=['POST'])
def translate_text():
    data = request.get_json()
    text = data.get('text', '').strip()
    
    if not text:
        return jsonify({'error': 'No text provided'}), 400

    try:
        # Same GTX endpoint used in the React frontend
        encoded_text = urllib.parse.quote(text)
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=en&dt=t&dt=rm&q={encoded_text}"
        
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return jsonify({'error': 'Translation service unavailable'}), 500
        
        result = resp.json()
        
        translation = ""
        transliteration = ""
        src_lang = result[2] if len(result) > 2 else "auto"

        if result and result[0]:
            # Extract standard translation
            for item in result[0]:
                if item[0] and item[1]:
                    translation += item[0]
            
            # Extract transliteration/pronunciation (usually packed at the end of the array)
            last_item = result[0][-1]
            if last_item and (len(last_item) > 2 or len(last_item) > 3) and not last_item[1]:
                transliteration = last_item[2] if len(last_item) > 2 and last_item[2] else (last_item[3] if len(last_item) > 3 else "")
            elif len(result[0]) > 1:
                # Fallback search for transliteration
                for item in result[0]:
                    if len(item) > 2 and item[2]:
                        transliteration = item[2]
                        break
                    elif len(item) > 3 and item[3]:
                        transliteration = item[3]
                        break

        return jsonify({
            'translation': translation.strip(),
            'pronunciation': transliteration.strip() if transliteration else None,
            'srcLang': src_lang
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500