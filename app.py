from flask import Flask, request, Response, jsonify
import requests
import spotipy
from flask.cli import load_dotenv
from spotipy.oauth2 import SpotifyOAuth
from collections import OrderedDict
import json
import os

load_dotenv()

CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
REDIRECT_URI = os.getenv('REDIRECT_URI')
SCOPE = os.getenv('SCOPE')

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
    return jsonify({
        "client_id": os.getenv("CLIENT_ID"),
        "client_secret_present": bool(os.getenv("CLIENT_SECRET")),
        "redirect_uri": os.getenv("REDIRECT_URI"),
        "scope": os.getenv("SCOPE")
    })

def get_single_lyric(artist_name, track_name):
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
def get_user_playlists():
    """
    Obtiene las playlists creadas por el usuario actual.
    """
    try:
        # Obtener información del usuario actual
        current_user = sp.current_user()
        me = current_user["display_name"]

        # Obtener todas las playlists del usuario
        playlists = sp.current_user_playlists(limit=50)
        playlist_details = []

        for playlist in playlists['items']:
            playlist_id = playlist['id']
            playlist_name = playlist['name']

            # Verificar si 'owner' está presente y filtrar por el usuario actual
            user_playlist = playlist.get('owner', {}).get('display_name', "Desconocido")
            if user_playlist != me:
                continue  # Saltar playlists que no sean del usuario actual

            # Obtener las canciones de la playlist
            tracks = sp.playlist_items(playlist_id)
            track_names = [track['track']['name'] for track in tracks['items']]

            # Añadir la playlist a los detalles
            playlist_details.append({
                "id": playlist_id,
                "name": playlist_name,
                "tracks": track_names
            })

        return jsonify({"playlists": playlist_details}), 200

    except Exception as e:
        return jsonify({"error": f"Error al obtener las playlists: {str(e)}"}), 500


@app.route('/add_tracks_to_playlist', methods=['POST'])
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
                return jsonify({"error": f"No se encontró la canción '{track_name}' con el artista '{artist_name}'"}), 404

        # Añadir las canciones a la playlist
        sp.playlist_add_items(playlist_id, track_uris)

        return jsonify({
            "message": f"Se añadieron las canciones a la playlist {playlist_id} correctamente.",
            "added_tracks": track_names
        }), 200

    except Exception as e:
        return jsonify({"error": f"Error al añadir canciones a la playlist: {str(e)}"}), 500


@app.route('/create_playlist', methods=['POST'])
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
        return jsonify({"error": f"Error al crear la playlist: {str(e)}"}), 500


@app.route('/get_playlist_by_name', methods=['GET'])
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
            playlist for playlist in playlists['items']
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
            for track in tracks['items']
        ]

        return jsonify({
            "playlist_name": playlist['name'],
            "playlist_id": playlist['id'],
            "playlist_url": playlist['external_urls']['spotify'],
            "tracks": track_details
        }), 200
    except Exception as e:
        return jsonify({"error": f"Error al obtener la playlist: {str(e)}"}), 500


if __name__ == '__main__':
    app.run(debug=True)