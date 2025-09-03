from flask import Flask, request, Response, jsonify
import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from collections import OrderedDict
from datetime import datetime
import json
import os
import logging

# Configuración de logging para depuración
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Cargar variables de entorno
from flask.cli import load_dotenv

load_dotenv()

# Variables de configuración de Spotify
CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
REDIRECT_URI = os.getenv(
    'REDIRECT_URI')  # Debe ser la URL de tu aplicación en Render, por ejemplo: https://spotbotgpt.onrender.com/callback
SCOPE = os.getenv('SCOPE')

# Refresh Token de Spotify
REFRESH_TOKEN = os.getenv('SPOTIPY_REFRESH_TOKEN')

# Token API para autenticación
API_TOKEN = os.getenv("GPT_API_TOKEN")

if not API_TOKEN:
    raise ValueError("GPT_API_TOKEN no está configurado. Por favor, configúralo en tus variables de entorno.")


def require_auth(f):
    def wrapped(*args, **kwargs):
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return jsonify({"error": "Falta el encabezado de autorización"}), 401
        token = auth_header.split(" ")[-1]
        if token != API_TOKEN:
            return jsonify({"error": "No autorizado"}), 401
        return f(*args, **kwargs)

    wrapped.__name__ = f.__name__
    return wrapped


# Verificar que todas las variables de entorno necesarias están presentes
if not CLIENT_ID or not CLIENT_SECRET or not REDIRECT_URI or not SCOPE:
    raise ValueError("Faltan variables de entorno necesarias para la API de Spotify.")

if not REFRESH_TOKEN:
    raise ValueError("SPOTIPY_REFRESH_TOKEN no está configurado. Por favor, configúralo en tus variables de entorno.")

# Inicializar SpotifyOAuth sin caché
sp_oauth = SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope=SCOPE,
    cache_path=None  # Evita usar caché
)


# Función para obtener un nuevo Access Token usando el Refresh Token
def get_access_token():
    try:
        token_info = sp_oauth.refresh_access_token(REFRESH_TOKEN)
        access_token = token_info['access_token']
        expires_at = token_info['expires_at']
        logger.info("Access Token obtenido y actualizado correctamente.")
        return access_token, expires_at
    except Exception as e:
        logger.error(f"Error al refrescar el token de acceso: {str(e)}")
        return None, None


# Inicializar el Access Token
access_token, expires_at = get_access_token()
if not access_token:
    raise RuntimeError("No se pudo obtener el Access Token. Revisa el Refresh Token y las credenciales.")

# Inicializar Spotipy con el Access Token
sp = spotipy.Spotify(auth=access_token)

app = Flask(__name__)


@app.route('/')
def home():
    return "El servidor está funcionando correctamente."


@app.route('/debug', methods=['GET'])
@require_auth
def debug():
    """
    Endpoint de depuración para verificar las variables de entorno y el estado del token.
    """
    try:
        global sp, access_token, expires_at  # Declaración global al inicio

        current_time = datetime.now().timestamp()
        if current_time > expires_at:
            # Token expirado, refrescar
            logger.info("Access Token expirado, refrescando...")
            access_token, expires_at = get_access_token()
            if not access_token:
                return jsonify({"error": "No se pudo refrescar el Access Token."}), 500
            sp = spotipy.Spotify(auth=access_token)

        return jsonify({
            "client_id": CLIENT_ID,
            "client_secret_present": bool(CLIENT_SECRET),
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "access_token": access_token,
            "token_expired": current_time > expires_at
        })
    except Exception as e:
        logger.exception("Error en debug.")
        return jsonify({"error": f"Error en debug: {str(e)}"}), 500


@app.route('/validate_token', methods=['GET'])
@require_auth
def validate_token():
    """
    Valida si el token de Spotify está activo o ha expirado.
    """
    try:
        global sp, access_token, expires_at  # Declaración global al inicio

        current_time = datetime.now().timestamp()
        token_expired = current_time > expires_at

        if token_expired:
            logger.info("Access Token expirado, refrescando...")
            access_token, expires_at = get_access_token()
            if not access_token:
                return jsonify({"error": "No se pudo refrescar el Access Token."}), 500
            sp = spotipy.Spotify(auth=access_token)

        return jsonify({
            "access_token": access_token,
            "token_expired": token_expired
        })
    except Exception as e:
        logger.exception("Error validando el token.")
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
        logger.exception("Error al obtener la letra.")
        return None, f"Error al obtener la letra: {str(e)}"


@app.route('/get_song_lyric', methods=['GET'])
@require_auth
def get_song_lyric():
    """
    Obtiene la letra de una canción específica.
    """
    artist_name = request.args.get('artist_name')
    track_name = request.args.get('track_name')

    if not artist_name or not track_name:
        return jsonify({"error": "Los parámetros 'artist_name' y 'track_name' son obligatorios"}), 400

    lyrics, error = get_single_lyric(artist_name, track_name)
    if error:
        return jsonify({"error": error}), 404

    response_data = OrderedDict([
        ("artist", artist_name),
        ("track", track_name),
        ("lyrics", lyrics)
    ])

    response_json = json.dumps(response_data, ensure_ascii=False, indent=2)
    return Response(response_json, content_type="application/json; charset=utf-8")


@app.route('/get_user_playlists', methods=['GET'])
@require_auth
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
        logger.exception("Error al obtener las playlists.")
        return jsonify({"error": f"Error al obtener las playlists: {str(e)}"}), 500


@app.route('/add_tracks_to_playlist', methods=['POST'])
@require_auth
def add_tracks_to_playlist():
    """
    Añade canciones a una playlist específica utilizando los nombres de las canciones.
    """
    data = request.json
    playlist_id = data.get('playlist_id')
    track_names = data.get('track_names')  # Lista de nombres de canciones
    artist_name = data.get('artist_name', None)  # Opcional: para afinar la búsqueda

    if not playlist_id or not track_names:
        return jsonify({"error": "Los parámetros 'playlist_id' y 'track_names' son obligatorios"}), 400

    try:
        # Buscar URIs de las canciones
        track_uris = []
        for track_name in track_names:
            query = f"track:{track_name}"
            if artist_name:
                query += f" artist:{artist_name}"

            results = sp.search(q=query, type='track', limit=1)
            tracks = results.get('tracks', {}).get('items', [])

            if tracks:
                track_uris.append(tracks[0]['uri'])
            else:
                return jsonify(
                    {"error": f"No se encontró la canción '{track_name}' con el artista '{artist_name}'"}), 404

        # Añadir las canciones a la playlist
        sp.playlist_add_items(playlist_id, track_uris)

        return jsonify({
            "message": f"Se añadieron las canciones a la playlist {playlist_id} correctamente.",
            "added_tracks": track_names
        }), 200

    except Exception as e:
        logger.exception("Error al añadir canciones a la playlist.")
        return jsonify({"error": f"Error al añadir canciones a la playlist: {str(e)}"}), 500


@app.route('/create_playlist', methods=['POST'])
@require_auth
def create_playlist():
    """
    Crea una nueva playlist para el usuario actual.
    """
    data = request.json
    playlist_name = data.get('playlist_name')
    description = data.get('description', "")
    public = data.get('public', True)  # Por defecto, las playlists son públicas

    if not playlist_name:
        return jsonify({"error": "El parámetro 'playlist_name' es obligatorio"}), 400

    try:
        # Obtener información del usuario actual
        user_id = sp.current_user()['id']

        # Crear la playlist
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
        logger.exception("Error al crear la playlist.")
        return jsonify({"error": f"Error al crear la playlist: {str(e)}"}), 500


@app.route('/get_playlist_by_name', methods=['GET'])
@require_auth
def get_playlist_by_name():
    """
    Obtiene los detalles de una playlist específica proporcionada por su nombre.
    """
    playlist_name = request.args.get('playlist_name')

    if not playlist_name:
        return jsonify({"error": "El parámetro 'playlist_name' es obligatorio"}), 400

    try:
        # Obtener todas las playlists del usuario
        playlists = sp.current_user_playlists(limit=50)

        # Buscar la playlist por nombre
        matching_playlists = [
            playlist for playlist in playlists.get('items', [])
            if playlist['name'].lower() == playlist_name.lower()
        ]

        if not matching_playlists:
            return jsonify({"error": f"No se encontró ninguna playlist con el nombre '{playlist_name}'"}), 404

        # Tomar la primera coincidencia
        playlist = matching_playlists[0]
        playlist_id = playlist['id']
        tracks = sp.playlist_items(playlist_id)

        # Obtener los detalles de las canciones
        track_details = [
            {
                "track_name": track['track']['name'],
                "artist": [artist['name'] for artist in track['track']['artists']],
                "album": track['track']['album']['name'],
                "track_url": track['track']['external_urls']['spotify']
            }
            for track in tracks.get('items', [])
        ]

        return jsonify({
            "playlist_name": playlist['name'],
            "playlist_id": playlist['id'],
            "playlist_url": playlist['external_urls']['spotify'],
            "tracks": track_details
        }), 200
    except Exception as e:
        logger.exception("Error al obtener la playlist.")
        return jsonify({"error": f"Error al obtener la playlist: {str(e)}"}), 500
        
@app.route("/get_playlist_by_id")
@require_auth
def get_playlist_by_id():
    pid = request.args.get("playlist_id")
    if not pid:
        return jsonify({"error": "playlist_id obligatorio"}), 400
    try:
        ensure_token()
        pid = _normalize_playlist_id(pid)
        limit  = int(request.args.get("limit", 100))
        offset = int(request.args.get("offset", 0))
        items = sp.playlist_items(pid, limit=limit, offset=offset)
        return jsonify(items)
    except Exception as e:
        logger.exception("get_playlist_by_id")
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    # Configuración para escuchar en el puerto proporcionado por Render
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
