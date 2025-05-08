#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backend Flask ↔ Spotify (sin reproducción)
Autor: 2025-05-08
"""
from __future__ import annotations
import os, json, logging, requests
from datetime import datetime
from functools import wraps
from collections import OrderedDict

from flask import Flask, request, jsonify, Response
from flask.cli import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth

# ---------------------------------------------------------------------------
#  Log & env
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
logger = logging.getLogger("spotbot-backend")

load_dotenv()  # .env de Render o local

CLIENT_ID        = os.getenv("CLIENT_ID")
CLIENT_SECRET    = os.getenv("CLIENT_SECRET")
REDIRECT_URI     = os.getenv("REDIRECT_URI")
SCOPE            = os.getenv("SCOPE")
REFRESH_TOKEN    = os.getenv("SPOTIPY_REFRESH_TOKEN")
API_TOKEN        = os.getenv("GPT_API_TOKEN")          # JWT que usará el plugin

# Validaciones mínimas
for var in ("CLIENT_ID", "CLIENT_SECRET", "REDIRECT_URI", "SCOPE",
            "SPOTIPY_REFRESH_TOKEN", "GPT_API_TOKEN"):
    if not os.getenv(var):
        raise ValueError(f"{var} no está configurada en variables de entorno.")

# ---------------------------------------------------------------------------
#  OAuth helper
# ---------------------------------------------------------------------------
sp_oauth = SpotifyOAuth(
    client_id     = CLIENT_ID,
    client_secret = CLIENT_SECRET,
    redirect_uri  = REDIRECT_URI,
    scope         = SCOPE,
    cache_path    = None          # sin archivo cache
)

def refresh_access_token() -> tuple[str,int]:
    """Devuelve (access_token, expires_at_epoch). Lanza excepción si falla."""
    token_info   = sp_oauth.refresh_access_token(REFRESH_TOKEN)
    logger.info("Access-Token refrescado.")
    return token_info["access_token"], token_info["expires_at"]

# Token global + cliente Spotipy
access_token, expires_at = refresh_access_token()
sp = spotipy.Spotify(auth=access_token)

def ensure_token():
    """Renueva el token si expiró y actualiza el cliente Spotipy global."""
    global sp, access_token, expires_at
    if datetime.now().timestamp() >= expires_at:
        access_token, expires_at = refresh_access_token()
        sp = spotipy.Spotify(auth=access_token)

# ---------------------------------------------------------------------------
#  Decorador JWT propio
# ---------------------------------------------------------------------------
def require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        token = auth.split()[-1] if auth.startswith("Bearer ") else auth
        if token != API_TOKEN:
            return jsonify({"error": "No autorizado"}), 401
        return f(*args, **kwargs)
    return wrapper

# ---------------------------------------------------------------------------
#  App Flask
# ---------------------------------------------------------------------------
app = Flask(__name__)

# ---------------------------------------------------------------------------
#  Utilidades auxiliares
# ---------------------------------------------------------------------------
def track_search_to_uris(track_names:list[str], artist:str|None=None) -> list[str]:
    """Convierte lista de títulos (y artista opcional) en URIs."""
    uris = []
    for name in track_names:
        q = f'track:"{name}"'
        if artist:
            q += f' artist:"{artist}"'
        res = sp.search(q=q, type="track", limit=1)
        items = res.get("tracks", {}).get("items", [])
        if not items:
            raise ValueError(f"No se encontró '{name}'{' de '+artist if artist else ''}")
        uris.append(items[0]["uri"])
    return uris

# ---------------------------------------------------------------------------
#  1. Health & debug
# ---------------------------------------------------------------------------
@app.route("/")
def home():
    return "✅ Servidor activo."

@app.route("/debug")
@require_auth
def debug():
    ensure_token()
    return jsonify({
        "client_id"        : CLIENT_ID,
        "redirect_uri"     : REDIRECT_URI,
        "scope"            : SCOPE,
        "access_token"     : access_token,
        "token_expired"    : datetime.now().timestamp() > expires_at
    })

@app.route("/validate_token")
@require_auth
def validate_token():
    expired = datetime.now().timestamp() > expires_at
    if expired:
        ensure_token()
        expired = False
    return jsonify({"access_token": access_token, "token_expired": expired})

# ---------------------------------------------------------------------------
#  2. Lyrics (servicio externo)
# ---------------------------------------------------------------------------
@app.route("/get_song_lyric")
@require_auth
def get_song_lyric():
    artist = request.args.get("artist_name")
    track  = request.args.get("track_name")
    if not artist or not track:
        return jsonify({"error": "artist_name y track_name son obligatorios"}), 400

    try:
        r = requests.get(f"https://api.lyrics.ovh/v1/{artist}/{track}", timeout=5)
        data = r.json()
        if data.get("error") == "No lyrics found":
            return jsonify({"error": "Letra no encontrada"}), 404
        lyrics = data["lyrics"].replace("\n\n", "\n")
    except Exception as e:
        logger.exception("Lyrics.ovh fallo")
        return jsonify({"error": str(e)}), 500

    return Response(
        json.dumps(OrderedDict(artist=artist, track=track, lyrics=lyrics),
                   ensure_ascii=False, indent=2),
        content_type="application/json; charset=utf-8"
    )

# ---------------------------------------------------------------------------
#  3. Top items / Historial
# ---------------------------------------------------------------------------
@app.route("/me/top/<item_type>")
@require_auth
def get_top_items(item_type):
    if item_type not in ("artists", "tracks"):
        return jsonify({"error": "item_type debe ser artists o tracks"}), 400
    ensure_token()
    try:
        time_range = request.args.get("time_range", "medium_term")
        limit      = int(request.args.get("limit", 20))
        offset     = int(request.args.get("offset", 0))
        data = sp.current_user_top_items(item_type, time_range=time_range,
                                         limit=limit, offset=offset)
        return jsonify(data)
    except Exception as e:
        logger.exception("top items")
        return jsonify({"error": str(e)}), 500

@app.route("/me/player/recently-played")
@require_auth
def recently_played():
    ensure_token()
    try:
        limit = int(request.args.get("limit", 20))
        after = request.args.get("after")  # timestamp ms
        data  = sp.current_user_recently_played(limit=limit, after=after)
        return jsonify(data)
    except Exception as e:
        logger.exception("recently played")
        return jsonify({"error": str(e)}), 500

# ---------------------------------------------------------------------------
#  4. Biblioteca (liked songs / albums)
# ---------------------------------------------------------------------------
@app.route("/me/tracks", methods=["GET", "PUT", "DELETE"])
@require_auth
def saved_tracks():
    ensure_token()
    try:
        if request.method == "GET":
            limit  = int(request.args.get("limit", 20))
            offset = int(request.args.get("offset", 0))
            return jsonify(sp.current_user_saved_tracks(limit=limit, offset=offset))

        ids = request.json.get("ids", [])
        if not ids:
            return jsonify({"error": "ids obligatorio"}), 400
        if request.method == "PUT":
            sp.current_user_saved_tracks_add(ids)
        else:
            sp.current_user_saved_tracks_delete(ids)
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.exception("saved tracks")
        return jsonify({"error": str(e)}), 500

@app.route("/me/albums", methods=["GET", "PUT", "DELETE"])
@require_auth
def saved_albums():
    ensure_token()
    try:
        if request.method == "GET":
            limit  = int(request.args.get("limit", 20))
            offset = int(request.args.get("offset", 0))
            return jsonify(sp.current_user_saved_albums(limit=limit, offset=offset))

        ids = request.json.get("ids", [])
        if not ids:
            return jsonify({"error": "ids obligatorio"}), 400
        if request.method == "PUT":
            sp.current_user_saved_albums_add(ids)
        else:
            sp.current_user_saved_albums_delete(ids)
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.exception("saved albums")
        return jsonify({"error": str(e)}), 500

# ---------------------------------------------------------------------------
#  5. Búsqueda
# ---------------------------------------------------------------------------
@app.route("/search")
@require_auth
def search():
    q    = request.args.get("q")
    type = request.args.get("type")
    if not q or not type:
        return jsonify({"error": "q y type son obligatorios"}), 400
    ensure_token()
    try:
        limit  = int(request.args.get("limit", 20))
        offset = int(request.args.get("offset", 0))
        return jsonify(sp.search(q=q, type=type, limit=limit, offset=offset))
    except Exception as e:
        logger.exception("search")
        return jsonify({"error": str(e)}), 500

# ---------------------------------------------------------------------------
#  6. Artistas
# ---------------------------------------------------------------------------
@app.route("/artists/<artist_id>")
@require_auth
def artist_details(artist_id):
    ensure_token()
    try:
        return jsonify(sp.artist(artist_id))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/artists/<artist_id>/top-tracks")
@require_auth
def artist_top_tracks(artist_id):
    ensure_token()
    try:
        market = request.args.get("market", "ES")
        return jsonify(sp.artist_top_tracks(artist_id, market=market))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------------------------------------------------------------------------
#  7. Audio features / analysis
# ---------------------------------------------------------------------------
@app.route("/audio-features/<track_id>")
@require_auth
def audio_features(track_id):
    ensure_token()
    try:
        return jsonify(sp.audio_features([track_id])[0])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/audio-analysis/<track_id>")
@require_auth
def audio_analysis(track_id):
    ensure_token()
    try:
        return jsonify(sp._get(f"audio-analysis/{track_id}"))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------------------------------------------------------------------------
#  8. Recomendaciones
# ---------------------------------------------------------------------------
@app.route("/recommendations")
@require_auth
def recommendations():
    ensure_token()
    try:
        parms = {k: v for k, v in request.args.items()}
        limit = int(parms.pop("limit", 20))
        data  = sp.recommendations(limit=limit, **parms)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------------------------------------------------------------------------
#  9. Playlists
# ---------------------------------------------------------------------------
@app.route("/me/playlists")
@require_auth
def my_playlists():
    ensure_token()
    try:
        limit  = int(request.args.get("limit", 20))
        offset = int(request.args.get("offset", 0))
        return jsonify(sp.current_user_playlists(limit=limit, offset=offset))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/users/<user_id>/playlists", methods=["POST"])
@require_auth
def create_playlist(user_id):
    data = request.json or {}
    name = data.get("name")
    if not name:
        return jsonify({"error": "name obligatorio"}), 400
    ensure_token()
    try:
        playlist = sp.user_playlist_create(
            user   = user_id,
            name   = name,
            public = data.get("public", False),
            description = data.get("description", "")
        )
        return jsonify(playlist), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/playlists/<playlist_id>", methods=["GET", "PUT"])
@require_auth
def playlist_details_edit(playlist_id):
    ensure_token()
    try:
        if request.method == "GET":
            return jsonify(sp.playlist(playlist_id))
        # PUT
        body = request.json or {}
        sp.playlist_change_details(
            playlist_id,
            name        = body.get("name"),
            public      = body.get("public"),
            description = body.get("description")
        )
        return jsonify({"status": "updated"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/playlists/<playlist_id>/tracks", methods=["GET", "POST", "DELETE", "PUT"])
@require_auth
def playlist_tracks(playlist_id):
    ensure_token()
    try:
        if request.method == "GET":
            limit  = int(request.args.get("limit", 100))
            offset = int(request.args.get("offset", 0))
            return jsonify(sp.playlist_items(playlist_id, limit=limit, offset=offset))

        body = request.json or {}
        if request.method == "POST":
            uris = body.get("uris")
            if not uris:
                return jsonify({"error": "uris obligatorio"}), 400
            pos  = body.get("position")
            res  = sp.playlist_add_items(playlist_id, uris, position=pos)
            return jsonify(res), 201

        if request.method == "DELETE":
            tracks = body.get("tracks")
            if not tracks:
                return jsonify({"error": "tracks obligatorio"}), 400
            snapshot_id = body.get("snapshot_id")
            res = sp.playlist_remove_specific_occurrences_of_items(
                playlist_id, tracks, snapshot_id=snapshot_id
            )
            return jsonify(res)

        # PUT → reorder
        range_start  = body.get("range_start")
        insert_before= body.get("insert_before")
        if range_start is None or insert_before is None:
            return jsonify({"error": "range_start y insert_before obligatorios"}), 400
        res = sp.playlist_reorder_items(
            playlist_id,
            range_start = range_start,
            insert_before=insert_before,
            range_length = body.get("range_length", 1),
            snapshot_id  = body.get("snapshot_id")
        )
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------------------------------------------------------------------------
# 10. Follow / Unfollow
# ---------------------------------------------------------------------------
@app.route("/me/following", methods=["GET", "PUT", "DELETE"])
@require_auth
def following():
    ensure_token()
    type_ = request.args.get("type")
    if type_ not in ("artist", "user"):
        return jsonify({"error": "type debe ser artist o user"}), 400
    ids = request.json.get("ids") if request.method in ("PUT","DELETE") else request.args.get("ids")
    if not ids:
        return jsonify({"error": "ids obligatorio"}), 400
    ids_list = ids.split(",") if isinstance(ids, str) else ids
    try:
        if request.method == "GET":
            if type_ == "artist":
                res = sp.current_user_following_artists(ids_list)
            else:
                # No wrapper para usuarios; llamada directa
                endpoint = f"me/following/contains?type=user&ids={','.join(ids_list)}"
                res = sp._get(endpoint)
            return jsonify(res)

        if type_ == "artist":
            if request.method == "PUT":
                sp.current_user_follow_artists(ids_list)
            else:
                sp.current_user_unfollow_artists(ids_list)
        else:  # users
            endpoint = f"me/following?type=user&ids={','.join(ids_list)}"
            if request.method == "PUT":
                sp._put(endpoint)
            else:
                sp._delete(endpoint)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/playlists/<playlist_id>/followers", methods=["PUT", "DELETE"])
@require_auth
def follow_playlist(playlist_id):
    ensure_token()
    try:
        if request.method == "PUT":
            public = request.json.get("public", False) if request.is_json else False
            sp.current_user_follow_playlist(playlist_id, public=public)
        else:
            sp.current_user_unfollow_playlist(playlist_id)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------------------------------------------------------------------------
# 11. Conveniencia: añadir por nombres (legacy)
# ---------------------------------------------------------------------------
@app.route("/playlists/<playlist_id>/tracks/from-names", methods=["POST"])
@require_auth
def add_tracks_by_name(playlist_id):
    """Mantiene la funcionalidad antigua `/add_tracks_to_playlist`."""
    data = request.json or {}
    names = data.get("track_names")
    artist= data.get("artist_name")
    if not names:
        return jsonify({"error": "track_names obligatorio"}), 400
    try:
        ensure_token()
        uris = track_search_to_uris(names, artist)
        sp.playlist_add_items(playlist_id, uris)
        return jsonify({"added_tracks": names})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
