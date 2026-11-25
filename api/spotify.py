"""Spotify now-playing SVG for GitHub profile READMEs.

No Spotify credentials are ever embedded in the image or returned in errors.
"""
from base64 import b64encode
from io import BytesIO
import logging
import os
import re
import time
from urllib.parse import urlparse

import requests
from colorthief import ColorThief
from dotenv import load_dotenv
from flask import Flask, Response, render_template, request

load_dotenv()
app = Flask(__name__)
LOGGER = logging.getLogger(__name__)

TOKEN_URL = "https://accounts.spotify.com/api/token"
CURRENT_URL = "https://api.spotify.com/v1/me/player/currently-playing"
RECENT_URL = "https://api.spotify.com/v1/me/player/recently-played?limit=1"
DEFAULT_BG = "0d1117"
DEFAULT_BORDER = "30363d"
DEFAULT_ACCENT = "1ed760"
TOKEN = {"value": "", "expires_at": 0.0}
IMAGE_HOSTS = {"i.scdn.co", "mosaic.scdn.co", "image-cdn-ak.spotifycdn.com"}


class SpotifyUnavailable(Exception):
    """Safe, non-secret diagnostic for a failed Spotify request."""


def hex_color(value, default):
    return value.lower() if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{6}", value) else default


def shorten(value, limit=34):
    value = value if isinstance(value, str) else ""
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def spotify_url(url, kind):
    """Allow only Spotify track/artist links, never arbitrary SVG links."""
    if not isinstance(url, str):
        return ""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "open.spotify.com":
        return ""
    if not re.fullmatch(rf"/{kind}/[A-Za-z0-9]+", parsed.path):
        return ""
    return f"https://open.spotify.com{parsed.path}"


def access_token(force=False):
    if not force and TOKEN["value"] and TOKEN["expires_at"] > time.monotonic():
        return TOKEN["value"]

    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_SECRET_ID")
    refresh_token = os.getenv("SPOTIFY_REFRESH_TOKEN")
    if not all((client_id, client_secret, refresh_token)):
        raise SpotifyUnavailable("Spotify environment variables are missing")

    try:
        response = requests.post(
            TOKEN_URL,
            auth=(client_id, client_secret),
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            timeout=5,
        )
        if response.status_code != 200:
            raise SpotifyUnavailable(f"Spotify token endpoint returned HTTP {response.status_code}")
        payload = response.json()
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise SpotifyUnavailable("Spotify token endpoint returned no access token")
        TOKEN["value"] = token
        TOKEN["expires_at"] = time.monotonic() + max(0, int(payload.get("expires_in", 3600)) - 60)
        return token
    except requests.RequestException as exc:
        raise SpotifyUnavailable("Spotify token endpoint unavailable") from exc
    except (ValueError, TypeError) as exc:
        raise SpotifyUnavailable("Spotify token response malformed") from exc


def spotify_get(url):
    for attempt in range(2):
        token = access_token(force=attempt == 1)
        try:
            response = requests.get(
                url, headers={"Authorization": f"Bearer {token}"}, timeout=5
            )
        except requests.RequestException as exc:
            raise SpotifyUnavailable("Spotify playback endpoint unavailable") from exc

        if response.status_code == 401:
            TOKEN["value"] = ""
            TOKEN["expires_at"] = 0
            continue
        if response.status_code == 204:
            return None
        if response.status_code != 200:
            raise SpotifyUnavailable(f"Spotify playback returned HTTP {response.status_code}")
        try:
            result = response.json()
        except ValueError as exc:
            raise SpotifyUnavailable("Spotify playback response malformed") from exc
        if not isinstance(result, dict):
            raise SpotifyUnavailable("Spotify playback response must be an object")
        return result
    raise SpotifyUnavailable("Spotify authentication rejected after retry")


def valid_track(value):
    return isinstance(value, dict) and value.get("type") in (None, "track") and isinstance(value.get("name"), str) and isinstance(value.get("artists"), list)


def select_playback(current, recent):
    """Prefer playing tracks; otherwise the newest recent track, never a random one."""
    if isinstance(current, dict):
        item = current.get("item")
        if current.get("is_playing") is True and valid_track(item):
            return item, "NOW PLAYING", "playing"

    if isinstance(recent, dict):
        for entry in recent.get("items") or []:
            track = entry.get("track") if isinstance(entry, dict) else None
            if valid_track(track):
                return track, "LAST PLAYED", "recent"

    if isinstance(current, dict) and valid_track(current.get("item")):
        return current["item"], "PAUSED", "paused"

    return None, "NOT PLAYING", "idle"


def album_artwork(track):
    """Download small Spotify-CDN images only; never follow redirects."""
    if not track:
        return "", DEFAULT_ACCENT
    album = track.get("album") if isinstance(track.get("album"), dict) else {}
    images = album.get("images") if isinstance(album.get("images"), list) else []
    url = next((image.get("url") for image in images if isinstance(image, dict) and isinstance(image.get("url"), str)), "")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in IMAGE_HOSTS:
        return "", DEFAULT_ACCENT
    try:
        response = requests.get(url, timeout=5, allow_redirects=False)
        if response.status_code != 200:
            return "", DEFAULT_ACCENT
        data = response.content
        if not data or len(data) > 750_000:
            return "", DEFAULT_ACCENT
        content_type = response.headers.get("Content-Type", "").split(";")[0].lower()
        if content_type not in ("image/jpeg", "image/png", "image/webp"):
            return "", DEFAULT_ACCENT
        mime = content_type
        try:
            color = ColorThief(BytesIO(data)).get_color(quality=5)
            accent = "%02x%02x%02x" % color[:3]
        except (OSError, ValueError, TypeError, IndexError):
            accent = DEFAULT_ACCENT
        return f"data:{mime};base64,{b64encode(data).decode('ascii')}", accent
    except requests.RequestException:
        return "", DEFAULT_ACCENT


def build_view(track, label, state):
    if not track:
        return {
            "title": "Nothing playing right now",
            "artist": "Check back for new music",
            "album_image": "",
            "accent": DEFAULT_ACCENT,
            "track_url": "",
            "state": state,
            "label": label,
        }

    artist = next(
        (person.get("name") for person in track.get("artists", []) if isinstance(person, dict) and isinstance(person.get("name"), str)),
        "Unknown artist",
    )
    image, accent = album_artwork(track)
    return {
        "title": shorten(track["name"]),
        "artist": shorten(artist, 40),
        "album_image": image,
        "accent": accent,
        "track_url": spotify_url((track.get("external_urls") or {}).get("spotify"), "track"),
        "state": state,
        "label": label,
    }


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def catch_all(path):
    background_color = hex_color(request.args.get("background_color"), DEFAULT_BG)
    border_color = hex_color(request.args.get("border_color"), DEFAULT_BORDER)
    status = "ok"
    try:
        current = spotify_get(CURRENT_URL)
        # An active song doesn't need a second Spotify request.
        recent = None
        if not (isinstance(current, dict) and current.get("is_playing") is True and valid_track(current.get("item"))):
            try:
                recent = spotify_get(RECENT_URL)
            except SpotifyUnavailable:
                # A paused current track can still be shown if history is unavailable.
                if not (isinstance(current, dict) and valid_track(current.get("item"))):
                    raise
        track, label, state = select_playback(current, recent)
        view = build_view(track, label, state)
    except (SpotifyUnavailable, KeyError, TypeError, ValueError) as exc:
        # Never return a broken image; keep the diagnostics non-secret.
        LOGGER.warning("Spotify widget degraded: %s", type(exc).__name__)
        view = build_view(None, "SPOTIFY UNAVAILABLE", "unavailable")
        status = "degraded"

    svg = render_template(
        "spotify-v2.svg.j2",
        **view,
        background_color=background_color,
        border_color=border_color,
    )
    response = Response(svg, mimetype="image/svg+xml")
    response.headers["Cache-Control"] = "public, max-age=30, s-maxage=60, stale-while-revalidate=180"
    response.headers["X-Widget-Status"] = status
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


if __name__ == "__main__":
    app.run(port=int(os.getenv("PORT", "5000")), debug=False)
