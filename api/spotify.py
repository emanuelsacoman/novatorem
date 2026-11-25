"""Spotify now-playing SVG for GitHub profile READMEs.

No Spotify credentials are ever embedded in the image or returned in errors.
"""
from base64 import b64encode
import colorsys
from io import BytesIO
import logging
import os
import re
import time
import unicodedata
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
DEFAULT_PALETTE = {
    "accent": DEFAULT_ACCENT,
    "accent_2": "50cfa4",
    "accent_3": "188e88",
    "accent_4": "91dfc2",
}
TOKEN = {"value": "", "expires_at": 0.0}
IMAGE_HOSTS = {"i.scdn.co", "mosaic.scdn.co", "image-cdn-ak.spotifycdn.com"}


class SpotifyUnavailable(Exception):
    """Safe, non-secret diagnostic for a failed Spotify request."""


def hex_color(value, default):
    return value.lower() if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{6}", value) else default


# Text begins at 204px; a conservative 352px budget keeps native SVG
# typography inside the card across platform font fallbacks.
TEXT_MAX_WIDTH = 352


def normalize_metadata(value):
    """Flatten unsafe whitespace and control characters without losing accents."""
    if not isinstance(value, str):
        return ""
    cleaned = "".join(" " if unicodedata.category(ch) in ("Cc", "Zl", "Zp") else ch for ch in value)
    return " ".join(cleaned.split())


def approximate_text_width(value, font_size, bold=False):
    """Conservative glyph-width estimate without depending on server fonts.

    SVG remains portable to GitHub's proxy; the layout also has a clip
    region as a final safety net for client-side font metric differences.
    """
    units = 0.0
    for ch in value:
        kind = unicodedata.category(ch)
        if kind in ("Mn", "Me", "Cf"):
            continue
        if unicodedata.east_asian_width(ch) in ("F", "W"):
            units += 1.02
        elif ch.isspace():
            units += .33
        elif ch in "ilI|!.,:;'\x60()[]":
            units += .32
        elif ch in "MW@#%&":
            units += .89
        elif ch.isupper():
            units += .73
        elif ch.isdigit():
            units += .61
        else:
            units += .60
    return units * font_size * (1.04 if bold else 1)


def clip_to_width(value, font_size, max_width=TEXT_MAX_WIDTH, bold=False):
    """Append an ellipsis only if shrinking alone cannot fit the copy."""
    if approximate_text_width(value, font_size, bold) <= max_width:
        return value
    ellipsis = "…"
    kept = []
    for ch in value:
        if approximate_text_width("".join(kept) + ch + ellipsis, font_size, bold) > max_width:
            break
        kept.append(ch)
    return "".join(kept).rstrip() + ellipsis


def split_title(value, font_size, max_width=TEXT_MAX_WIDTH):
    """Wrap on a word boundary where possible, otherwise split long tokens."""
    if approximate_text_width(value, font_size, bold=True) <= max_width:
        return (value,)
    end = 0
    for index in range(1, len(value) + 1):
        if approximate_text_width(value[:index], font_size, bold=True) > max_width:
            break
        end = index
    boundary = value.rfind(" ", 0, end + 1)
    if boundary >= max(1, int(end * .55)):
        first, rest = value[:boundary].strip(), value[boundary:].strip()
    else:
        first, rest = value[:end].rstrip(), value[end:].lstrip()
    if not first:
        first, rest = value[:1], value[1:].lstrip()
    return (first, rest) if rest else (first,)


def music_text_layout(title, artist):
    """Prefer original type sizes, then 2 title lines, and truncate only last."""
    title = normalize_metadata(title) or "Unknown track"
    artist = normalize_metadata(artist) or "Unknown artist"

    for size in (29, 27, 25):
        if approximate_text_width(title, size, bold=True) <= TEXT_MAX_WIDTH:
            title_lines = (title,)
            title_font_size = size
            break
    else:
        title_lines = None
        for size in (25, 23, 21):
            candidate = split_title(title, size)
            if len(candidate) == 2 and all(
                approximate_text_width(line, size, bold=True) <= TEXT_MAX_WIDTH
                for line in candidate
            ):
                title_lines = candidate
                title_font_size = size
                break
        if title_lines is None:
            title_font_size = 21
            first, rest = split_title(title, title_font_size)
            title_lines = (first, clip_to_width(rest, title_font_size, bold=True))

    artist_font_size = 14
    for size in (18, 17, 16, 15, 14):
        if approximate_text_width(artist, size) <= TEXT_MAX_WIDTH:
            artist_font_size = size
            break
    return {
        "title_lines": title_lines,
        "title_font_size": title_font_size,
        "artist_display": clip_to_width(artist, artist_font_size),
        "artist_font_size": artist_font_size,
        "artist_y": 145 if len(title_lines) == 2 else 126,
    }


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


def palette_from_cover(data):
    """Use the same ColorThief palette approach as main, once per cover.

    Moderate brightness improves visibility on a dark SVG without changing
    the hue: monochrome covers stay monochrome, colorful covers stay colorful.
    """
    try:
        raw = ColorThief(BytesIO(data)).get_palette(color_count=5, quality=5)
        swatches = []
        for rgb in raw:
            if len(rgb) < 3:
                continue
            h, saturation, value = colorsys.rgb_to_hsv(*(int(channel) / 255 for channel in rgb[:3]))
            value = max(.55, min(.91, value))
            if saturation >= .10:
                saturation = min(.96, max(.22, saturation * 1.08))
            else:
                saturation = 0.0
            corrected = colorsys.hsv_to_rgb(h, saturation, value)
            color = "".join(f"{round(channel * 255):02x}" for channel in corrected)
            if color not in swatches:
                swatches.append(color)
        if not swatches:
            return DEFAULT_PALETTE.copy()
        colors = [swatches[index % len(swatches)] for index in range(4)]
        return dict(zip(DEFAULT_PALETTE, colors))
    except (OSError, ValueError, TypeError, IndexError, ZeroDivisionError):
        return DEFAULT_PALETTE.copy()


def relative_luminance(hex_rgb):
    """WCAG relative luminance for a validated RGB hex value."""
    channels = [int(hex_rgb[index:index + 2], 16) / 255 for index in (0, 2, 4)]
    linear = [channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4 for channel in channels]
    return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2]


def readable_album_tint(color, minimum_contrast):
    """Keep the album's hue while lightening it enough for dark-glass text."""
    background = "26343d"  # Conservative, brighter than our text contrast veil.
    original = [int(color[index:index + 2], 16) for index in (0, 2, 4)]
    for amount in range(9, 101):
        fraction = amount / 100
        mixed = [round(component + (255 - component) * fraction) for component in original]
        candidate = "".join(f"{component:02x}" for component in mixed)
        ratio = (relative_luminance(candidate) + .05) / (relative_luminance(background) + .05)
        if ratio >= minimum_contrast:
            return candidate
    return "ffffff"


def album_artwork(track):
    """Fetch Spotify-CDN art once; derive the full effect palette from its pixels."""
    if not track:
        return "", DEFAULT_PALETTE.copy()
    album = track.get("album") if isinstance(track.get("album"), dict) else {}
    images = album.get("images") if isinstance(album.get("images"), list) else []
    url = next((image.get("url") for image in images if isinstance(image, dict) and isinstance(image.get("url"), str)), "")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in IMAGE_HOSTS:
        return "", DEFAULT_PALETTE.copy()
    try:
        response = requests.get(url, timeout=5, allow_redirects=False)
        if response.status_code != 200:
            return "", DEFAULT_PALETTE.copy()
        data = response.content
        if not data or len(data) > 750_000:
            return "", DEFAULT_PALETTE.copy()
        content_type = response.headers.get("Content-Type", "").split(";")[0].lower()
        if content_type not in ("image/jpeg", "image/png", "image/webp"):
            return "", DEFAULT_PALETTE.copy()
        palette = palette_from_cover(data)
        return f"data:{content_type};base64,{b64encode(data).decode('ascii')}", palette
    except requests.RequestException:
        return "", DEFAULT_PALETTE.copy()


def build_view(track, label, state):
    if not track:
        return {
            "title": "Nothing playing right now",
            "artist": "Check back for new music",
            **music_text_layout("Nothing playing right now", "Check back for new music"),
            "album_image": "",
            **DEFAULT_PALETTE,
            "title_color": readable_album_tint(DEFAULT_PALETTE["accent_4"], 7),
            "artist_color": readable_album_tint(DEFAULT_PALETTE["accent_2"], 4.5),
            "track_url": "",
            "state": state,
            "label": label,
        }

    artist = next(
        (person.get("name") for person in track.get("artists", []) if isinstance(person, dict) and isinstance(person.get("name"), str)),
        "Unknown artist",
    )
    image, palette = album_artwork(track)
    full_title = normalize_metadata(track["name"]) or "Unknown track"
    full_artist = normalize_metadata(artist) or "Unknown artist"
    return {
        "title": full_title,
        "artist": full_artist,
        **music_text_layout(full_title, full_artist),
        "album_image": image,
        **palette,
        "title_color": readable_album_tint(palette["accent_4"], 7),
        "artist_color": readable_album_tint(palette["accent_2"], 4.5),
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
