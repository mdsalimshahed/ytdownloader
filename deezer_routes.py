import os
import io
import json
import time
import tempfile
import threading
from urllib.parse import quote, urlparse
import requests
from flask import Blueprint, render_template, request, send_file, jsonify, Response

from deemix import generateDownloadObject
from deemix.downloader import Downloader
from deemix.settings import load as load_settings
from deezer import Deezer

deezer_bp = Blueprint('deezer', __name__)
progress_store_deezer = {}

# --- ADD THIS NEW BLOCK RIGHT HERE ---
@deezer_bp.after_request
def add_cors_headers(response):
    """Automatically allow Cross-Origin requests from your React frontend"""
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET,PUT,POST,DELETE,OPTIONS'
    return response
# -------------------------------------

def get_deezer_listener(session_id):
    """Custom listener for deemix download progress."""
    class DeezerListener:
        def send(self, key, value):
            if key == "downloadProgress" and session_id in progress_store_deezer:
                downloaded = value.get("downloaded", 0)
                total = value.get("total", 1)
                percent = round((downloaded / total) * 100, 1) if total > 0 else 0
                progress_store_deezer[session_id] = {
                    'status': 'downloading',
                    'percent': percent,
                    'message': f"Fetching Deezer Audio Stream: {percent}%"
                }
            elif key == "updateQueue" and value.get("status") == "completed":
                if session_id in progress_store_deezer:
                    progress_store_deezer[session_id] = {
                        'status': 'processing',
                        'percent': 95.0,
                        'message': "Finalizing stream preparation..."
                    }
    return DeezerListener()

@deezer_bp.route('/search-deezer', methods=['GET'])
def search_deezer():
    raw_query = request.args.get('q', '').strip()
    if not raw_query:
        return jsonify({'results': []})
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
        'Cache-Control': 'no-cache'
    }

    try:
        # Case 1: Direct Deezer Track URL pasted into the search box
        if 'deezer.com' in raw_query and '/track/' in raw_query:
            parsed = urlparse(raw_query)
            track_id = parsed.path.split('/track/')[1].split('/')[0]
            clean_track_url = f"https://api.deezer.com/track/{track_id}"
            
            resp = requests.get(clean_track_url, headers=headers, timeout=10)
            if resp.status_code == 200:
                item = resp.json()
                if 'id' in item:
                    return jsonify({'results': [{
                        'id': item.get('id'),
                        'title': item.get('title'),
                        'artist': item.get('artist', {}).get('name'),
                        'album': item.get('album', {}).get('title'),
                        'cover': item.get('album', {}).get('cover_medium'),
                        'link': item.get('link'),
                        'duration': item.get('duration', 0)
                    }]})

        # Case 2: Standard text query search
        encoded_query = quote(raw_query)
        url = f"https://api.deezer.com/search?q={encoded_query}&limit=30&strict=off"
        
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            results = []
            for item in data.get('data', []):
                results.append({
                    'id': item.get('id'),
                    'title': item.get('title'),
                    'artist': item.get('artist', {}).get('name'),
                    'album': item.get('album', {}).get('title'),
                    'cover': item.get('album', {}).get('cover_medium'),
                    'link': item.get('link'),
                    'duration': item.get('duration', 0)
                })
            return jsonify({'results': results})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'results': []})

@deezer_bp.route('/deezer-progress/<session_id>')
def deezer_progress_stream(session_id):
    def event_stream():
        while True:
            data = progress_store_deezer.get(session_id, {
                'status': 'starting',
                'percent': 0,
                'message': 'Initializing Deezer engine...'
            })
            yield f"data: {json.dumps(data)}\n\n"
            if data.get('status') == 'completed' or data.get('status') == 'error':
                break
            time.sleep(0.5)
    return Response(event_stream(), mimetype='text/event-stream')

@deezer_bp.route('/download-deezer', methods=['POST'])
def download_deezer():
    session_id = request.form.get('session_id')
    track_url = request.form.get('url', '').strip()
    arl_token = request.form.get('arl_token', '').strip()
    quality = request.form.get('quality', '1')
    action = request.form.get('action', 'download')

    if not track_url:
        return jsonify({'error': 'No Deezer URL provided'}), 400
    if not arl_token:
        return jsonify({'error': 'Deezer ARL Token is required for authentication.'}), 400

    progress_store_deezer[session_id] = {
        'status': 'starting',
        'percent': 5.0,
        'message': 'Authenticating with Deezer API...'
    }

    dz = Deezer()
    if not dz.login_via_arl(arl_token):
        progress_store_deezer[session_id] = {'status': 'error', 'message': 'Invalid or expired Deezer ARL token.'}
        return jsonify({'error': 'Invalid or expired Deezer ARL token.'}), 400

    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            progress_store_deezer[session_id] = {
                'status': 'downloading',
                'percent': 10.0,
                'message': 'Fetching track metadata and preparing audio stream...'
            }

            settings = load_settings()
            settings['downloadLocation'] = temp_dir
            settings['tracknameTemplate'] = '%artist% - %title%'

            download_info = generateDownloadObject(dz, track_url, int(quality))
            listener = get_deezer_listener(session_id)

            downloader = Downloader(dz, download_info, settings, listener)
            downloader.start()

            downloaded_files = []
            for root, _, files in os.walk(temp_dir):
                for f in files:
                    if not f.endswith('.tmp'):
                        downloaded_files.append(os.path.join(root, f))

            if not downloaded_files:
                raise Exception("Failed to retrieve audio stream from Deezer. Verify link or ARL token.")

            file_path = downloaded_files[0]
            download_name = os.path.basename(file_path)

            progress_store_deezer[session_id] = {
                'status': 'completed',
                'percent': 100.0,
                'message': 'Stream ready!' if action == 'stream' else 'Transferring track to your device...'
            }

            with open(file_path, 'rb') as f:
                file_bytes = io.BytesIO(f.read())

            ext = os.path.splitext(download_name)[1].lower()
            mimetype = 'audio/flac' if ext == '.flac' else 'audio/mpeg'
            is_attachment = (action != 'stream')

            response = send_file(
                file_bytes,
                mimetype=mimetype,
                as_attachment=is_attachment,
                download_name=download_name
            )
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response

        except Exception as e:
            progress_store_deezer[session_id] = {'status': 'error', 'message': str(e)}
            return jsonify({'error': str(e)}), 500
        finally:
            def cleanup():
                time.sleep(5)
                progress_store_deezer.pop(session_id, None)
            threading.Thread(target=cleanup).start()