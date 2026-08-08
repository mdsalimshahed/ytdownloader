# --- deezer_routes.py ---
import os
import io
import re
import json
import time
import shutil
import tempfile
import threading
from datetime import datetime
from urllib.parse import quote, urlparse
import requests
from flask import Blueprint, render_template, request, send_file, jsonify, Response
from deemix import generateDownloadObject
from deemix.downloader import Downloader
from deemix.settings import load as load_settings
from deezer import Deezer

deezer_bp = Blueprint('deezer', __name__)
progress_store_deezer = {}

# Environment Detection (Render automatically sets RENDER=true)
IS_LOCAL = os.environ.get('RENDER') is None

# --- EXPLICIT CORS HOOK FOR DEEZER ROUTES ---
@deezer_bp.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET,PUT,POST,DELETE,OPTIONS'
    # Expose the custom obfuscation header to the React frontend
    response.headers['Access-Control-Expose-Headers'] = 'Content-Disposition, X-Audio-Obfuscated'
    return response

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

@deezer_bp.route('/track-info-deezer/<track_id>', methods=['GET'])
def get_track_info_deezer(track_id):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9'
        }
        track_url = f"https://api.deezer.com/track/{track_id}"
        resp_track = requests.get(track_url, headers=headers, timeout=10)
        if resp_track.status_code != 200:
            return jsonify({'error': 'Failed to retrieve track metadata'}), resp_track.status_code

        track_data = resp_track.json()
        genres = []
        if 'album' in track_data and 'id' in track_data['album']:
            album_id = track_data['album']['id']
            album_url = f"https://api.deezer.com/album/{album_id}"
            resp_album = requests.get(album_url, headers=headers, timeout=10)
            if resp_album.status_code == 200:
                album_data = resp_album.json()
                if 'genres' in album_data and 'data' in album_data['genres']:
                    genres = [g.get('name') for g in album_data['genres']['data'] if g.get('name')]

        lyrics_text = "No lyrics available for this track."
        lyrics_url = f"https://api.deezer.com/track/{track_id}/lyrics"
        resp_lyrics = requests.get(lyrics_url, headers=headers, timeout=10)
        if resp_lyrics.status_code == 200:
            lyrics_data = resp_lyrics.json()
            if 'lyrics' in lyrics_data:
                lyrics_text = lyrics_data['lyrics']
            elif 'text' in lyrics_data:
                lyrics_text = lyrics_data['text']

        track_data['extracted_genres'] = genres if genres else ["Not Specified"]
        track_data['extracted_lyrics'] = lyrics_text

        return jsonify({'success': True, 'data': track_data})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
    frontend_local_dir = request.form.get('local_dir', '').strip()
    index_counter_str = request.form.get('index_counter', '').strip()
    
    # Read the obfuscation flag
    obfuscate_flag = request.form.get('obfuscate', 'false').lower() == 'true'

    if not track_url:
        return jsonify({'error': 'No Deezer URL provided'}), 400
    if not arl_token:
        return jsonify({'error': 'Deezer ARL Token is required for authentication.'}), 400

    prefix = f"{{{index_counter_str}}} " if index_counter_str.isdigit() and action != 'stream' else ""
    
    track_id = None
    if 'deezer.com' in track_url and '/track/' in track_url:
        try:
            parsed = urlparse(track_url)
            track_id = parsed.path.split('/track/')[1].split('/')[0]
        except:
            pass

    song_name = "Deezer_Track"
    artist_name = "Unknown_Artist"
    explicit_tag = ""
    
    if track_id:
        try:
            h = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept-Language': 'en-US,en;q=0.9'
            }
            t_resp = requests.get(f"https://api.deezer.com/track/{track_id}", headers=h, timeout=5)
            if t_resp.status_code == 200:
                t_data = t_resp.json()
                if 'title' in t_data:
                    song_name = t_data['title']
                if 'artist' in t_data and 'name' in t_data['artist']:
                    artist_name = t_data['artist']['name']
                if t_data.get('explicit_lyrics'):
                    explicit_tag = " (Explicit)"
        except Exception:
            pass

    song_name = re.sub(r'[\\/*?:"<>|]', "", song_name).strip()
    artist_name = re.sub(r'[\\/*?:"<>|]', "", artist_name).strip()

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
            
            # --- APPLY OS-LEVEL CHARACTER LIMITS & FORMATTING ---
            ext = os.path.splitext(file_path)[1].lower()
            base_name = f"{prefix}{song_name} by {artist_name}{explicit_tag}"
            
            if len(base_name) + len(ext) > 250:
                base_name = f"{prefix}{song_name}{explicit_tag}"
                if len(base_name) + len(ext) > 250:
                    allowed_len = 250 - len(prefix) - len(explicit_tag) - len(ext)
                    base_name = f"{prefix}{song_name[:allowed_len].strip()}{explicit_tag}"
            
            download_name = base_name + ext

            # ==========================================
            # DIRECTORY CREATION & SAVE (LOCAL ONLY)
            # ==========================================
            saved_locally = False
            date_str = ""
            
            if action != 'stream' and IS_LOCAL and frontend_local_dir:
                try:
                    date_str = datetime.now().strftime("%d %B %Y").lstrip("0")
                    final_save_dir = os.path.join(frontend_local_dir, date_str)
                    os.makedirs(final_save_dir, exist_ok=True)
                    
                    server_file_path = os.path.join(final_save_dir, download_name)
                    shutil.copy2(file_path, server_file_path)
                    saved_locally = True
                except Exception as local_err:
                    print(f"Local save error: {local_err}")

            if saved_locally and action != 'stream':
                progress_store_deezer[session_id] = {
                    'status': 'completed',
                    'percent': 100.0,
                    'message': f'Saved locally to {date_str} folder!'
                }
                return jsonify({
                    'success': True,
                    'message': f'File saved directly to: {date_str} folder'
                })

            progress_store_deezer[session_id] = {
                'status': 'completed',
                'percent': 100.0,
                'message': 'Stream ready!' if action == 'stream' else 'Transferring track to browser...'
            }

            with open(file_path, 'rb') as f:
                raw_bytes = bytearray(f.read())

            if obfuscate_flag:
                # OBFUSCATION ENGINE: Scramble the first 2048 bytes of the file 
                OBFUSCATION_KEY = 0x5A
                limit = min(len(raw_bytes), 2048)
                for i in range(limit):
                    raw_bytes[i] ^= OBFUSCATION_KEY

                # Force generic binary type to bypass network sniffers
                response = Response(bytes(raw_bytes), mimetype='application/octet-stream')
                response.headers['X-Audio-Obfuscated'] = 'true'
                return response
            else:
                mimetype = 'audio/flac' if ext == '.flac' else 'audio/mpeg'
                return send_file(
                    io.BytesIO(raw_bytes),
                    mimetype=mimetype,
                    as_attachment=(action != 'stream'),
                    download_name=download_name
                )

        except Exception as e:
            progress_store_deezer[session_id] = {'status': 'error', 'message': str(e)}
            return jsonify({'error': str(e)}), 500
        finally:
            def cleanup():
                time.sleep(5)
                progress_store_deezer.pop(session_id, None)
            threading.Thread(target=cleanup).start()