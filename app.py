from flask import Flask, request, jsonify
import requests
import spotipy
from flask.cli import load_dotenv
from spotipy.oauth2 import SpotifyOAuth
from datetime import datetime
import os

# Load environment variables
load_dotenv()

CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
REDIRECT_URI = os.getenv('REDIRECT_URI')
SCOPE = os.getenv('SCOPE')
API_TOKEN = os.getenv("GPT_API_TOKEN")

if not API_TOKEN:
    raise ValueError("GPT_API_TOKEN is not set. Please configure it in your environment.")

def require_auth(f):
    def wrapped(*args, **kwargs):
        token = request.headers.get("Authorization")
        if not token or token.split(" ")[-1] != API_TOKEN:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    wrapped.__name__ = f.__name__
    return wrapped

# Check for required Spotify credentials
if not CLIENT_ID or not CLIENT_SECRET or not REDIRECT_URI or not SCOPE:
    raise ValueError("Missing environment variables for Spotify API")

def is_token_expired():
    expires_at = os.getenv("SPOTIPY_EXPIRES_AT")
    if not expires_at:
        return True
    return datetime.now().timestamp() > float(expires_at)

# Initialize Spotipy
if os.getenv("SPOTIPY_ACCESS_TOKEN") and not is_token_expired():
    sp = spotipy.Spotify(auth=os.getenv("SPOTIPY_ACCESS_TOKEN"))
else:
    sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
        scope=SCOPE,
        redirect_uri=REDIRECT_URI,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET
    ))

app = Flask(__name__)

@app.route('/')
def home():
    return "Server is running."

@require_auth
@app.route('/debug', methods=['GET'])
def debug():
    """
    Debug endpoint to verify server environment variables.
    """
    try:
        token_info = sp.auth_manager.get_cached_token()
        return jsonify({
            "client_id": CLIENT_ID,
            "client_secret_present": bool(CLIENT_SECRET),
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "access_token": token_info.get("access_token") if token_info else None,
            "token_expired": sp.auth_manager.is_token_expired(token_info) if token_info else None
        })
    except Exception as e:
        return jsonify({"error": f"Error in debug: {str(e)}"}), 500

@require_auth
@app.route('/validate_token', methods=['GET'])
def validate_token():
    try:
        token_info = sp.auth_manager.get_cached_token()
        return jsonify({
            "access_token": token_info.get("access_token") if token_info else None,
            "token_expired": sp.auth_manager.is_token_expired(token_info) if token_info else None
        })
    except Exception as e:
        return jsonify({"error": f"Error validating token: {str(e)}"}), 500

@require_auth
@app.route('/get_user_playlists', methods=['GET'])
def get_user_playlists():
    """
    Retrieves playlists created by the authenticated user.
    """
    try:
        current_user = sp.current_user()
        me = current_user.get("display_name", "Unknown")
        playlists = sp.current_user_playlists(limit=50)
        playlist_details = []

        for playlist in playlists.get('items', []):
            playlist_id = playlist['id']
            playlist_name = playlist['name']
            user_playlist = playlist.get('owner', {}).get('display_name', "Unknown")
            if user_playlist != me:
                continue

            tracks = sp.playlist_items(playlist_id)
            track_names = [track['track']['name'] for track in tracks.get('items', [])]
            playlist_details.append({
                "id": playlist_id,
                "name": playlist_name,
                "tracks": track_names
            })

        return jsonify({"playlists": playlist_details}), 200
    except Exception as e:
        return jsonify({"error": f"Error retrieving playlists: {str(e)}"}), 500

@require_auth
@app.route('/create_playlist', methods=['POST'])
def create_playlist():
    """
    Creates a new playlist for the authenticated user.
    """
    data = request.json
    playlist_name = data.get('playlist_name')
    description = data.get('description', "")
    public = data.get('public', True)

    if not playlist_name:
        return jsonify({"error": "The 'playlist_name' parameter is required"}), 400

    try:
        user_id = sp.current_user()['id']
        new_playlist = sp.user_playlist_create(
            user=user_id,
            name=playlist_name,
            public=public,
            description=description
        )

        return jsonify({
            "message": f"Playlist '{playlist_name}' created successfully.",
            "playlist_id": new_playlist['id'],
            "playlist_url": new_playlist['external_urls']['spotify']
        }), 201
    except Exception as e:
        return jsonify({"error": f"Error creating playlist: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(debug=True)
