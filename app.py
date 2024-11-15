from flask import Flask, request, Response, jsonify
import requests
import spotipy
from flask.cli import load_dotenv
from spotipy.oauth2 import SpotifyOAuth
from collections import OrderedDict
import json
import os

# Cargar variables de entorno
load_dotenv()

CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
REDIRECT_URI = os.getenv('REDIRECT_URI')
SCOPE = os.getenv('SCOPE')

# Manejo de errores en las credenciales
if not CLIENT_ID or not CLIENT_SECRET or not REDIRECT_URI or not SCOPE:
    raise ValueError("Faltan variables de entorno necesarias para Spotify API")

# Configurar Spotipy
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    scope=SCOPE,
    redirect_uri=REDIRECT_URI,
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET
))

app = Flask(__name__)

@app.route('/')
def home():
    return "El servidor está funcionando correctamente."

@app.route('/debug', methods=['GET'])
def debug():
    """
    Verificar configuración y variables de entorno.
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
        return jsonify({"error": f"Error en debug: {str(e)}"}), 500

@app.route('/validate_token', methods=['GET'])
def validate_token():
    try:
        token_info = sp.auth_manager.get_cached_token()
        return jsonify({
            "access_token": token_info.get("access_token") if token_info else None,
            "token_expired": sp.auth_manager.is_token_expired(token_info) if token_info else None
        })
    except Exception as e:
        return jsonify({"error": f"Error al validar el token: {str(e)}"}), 500

def get_single_lyric(artist_name, track_name):
    """
    Obtener la letra de una canción desde la API Lyrics.ovh.
    """
    try:
        r = requests.get(f"https://api.lyrics.ovh/v1/{artist_name}/{track_name}")
        r_json = r.json()
        if r_json.get("error") == "No lyrics found":
            return None, f"No se encontró la letra de '{track_name}' de {artist_name}"
        lyrics = r_json["lyrics"].replace("\n\n", "\n")
        return lyrics, None
    except Exception as e:
        return None, f"Error al obtener la letra: {str(e)}"

@app.route('/get_song_lyric', methods=['GET'])
def get_song_lyric():
    """
    Endpoint para obtener la letra de una canción.
    """
    artist_name = request.args.get('artist_name')
    track_name = request.args.get('track_name')

    if not artist_name or not track_name:
        return jsonify({"error": "Los parámetros 'artist_name' y 'track_name' son obligatorios"}), 400

    lyrics, error = get_single_lyric(artist_name, track_name)
    if error:
        return jsonify({"error": error}), 404

    return jsonify({
        "artist": artist_name,
        "track": track_name,
        "lyrics": lyrics
    }), 200

@app.route('/get_user_playlists', methods=['GET'])
def get_user_playlists():
    """
    Obtiene las playlists creadas por el usuario actual.
    """
    try:
        current_user = sp.current_user()
        me = current_user.get("display_name", "Desconocido")
        playlists = sp.current_user_playlists(limit=50)
        playlist_details = []

        for playlist in playlists.get('items', []):
            playlist_id = playlist['id']
            playlist_name = playlist['name']
            user_playlist = playlist.get('owner', {}).get('display_name', "Desconocido")
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
        return jsonify({"error": f"Error al obtener las playlists: {str(e)}"}), 500

@app.route('/create_playlist', methods=['POST'])
def create_playlist():
    """
    Crea una nueva playlist para el usuario actual.
    """
    data = request.json
    playlist_name = data.get('playlist_name')
    description = data.get('description', "")
    public = data.get('public', True)

    if not playlist_name:
        return jsonify({"error": "El parámetro 'playlist_name' es obligatorio"}), 400

    try:
        user_id = sp.current_user()['id']
        new_playlist = sp.user_playlist_create(
            user=user_id,
            name=playlist_name,
            public=public,
            description=description
        )

        return jsonify({
            "message": f"Playlist '{playlist_name}' creada exitosamente.",
            "playlist_id": new_playlist['id'],
            "playlist_url": new_playlist['external_urls']['spotify']
        }), 201
    except Exception as e:
        return jsonify({"error": f"Error al crear la playlist: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(debug=True)
