"""Offline unit tests: no Spotify credentials or API requests required."""
import os
import time
import unittest
from unittest.mock import Mock, patch

from api import spotify


def track(name="Sample Song"):
    return {
        "type": "track",
        "name": name,
        "artists": [{"name": "Sample Artist"}],
        "album": {"images": []},
        "external_urls": {"spotify": "https://open.spotify.com/track/123ABC"},
    }


class SpotifyWidgetTests(unittest.TestCase):
    def setUp(self):
        spotify.TOKEN["value"] = ""
        spotify.TOKEN["expires_at"] = 0

    def test_color_validation(self):
        self.assertEqual(spotify.hex_color("A1b2C3", "ffffff"), "a1b2c3")
        self.assertEqual(spotify.hex_color("ffffff;stroke:red", "111111"), "111111")
        self.assertEqual(spotify.hex_color(None, "111111"), "111111")

    def test_spotify_links_are_restricted(self):
        self.assertEqual(spotify.spotify_url("https://open.spotify.com/track/123ABC", "track"), "https://open.spotify.com/track/123ABC")
        self.assertEqual(spotify.spotify_url("javascript:alert(1)", "track"), "")
        self.assertEqual(spotify.spotify_url("https://evil.example/track/123", "track"), "")

    def test_currently_playing_takes_priority(self):
        recent = {"items": [{"track": track("Old Song")}]}
        result, label, state = spotify.select_playback(
            {"is_playing": True, "item": track("New Song")}, recent
        )
        self.assertEqual((result["name"], label, state), ("New Song", "NOW PLAYING", "playing"))

    def test_recent_is_newest_not_random(self):
        recent = {"items": [{"track": track("Newest")}, {"track": track("Older")}]}
        result, label, state = spotify.select_playback(None, recent)
        self.assertEqual((result["name"], label, state), ("Newest", "LAST PLAYED", "recent"))

    def test_paused_track_is_not_mislabeled_as_playing(self):
        result, label, state = spotify.select_playback(
            {"is_playing": False, "item": track()}, None
        )
        self.assertEqual((label, state), ("PAUSED", "paused"))

    def test_missing_history_is_not_an_exception(self):
        self.assertEqual(spotify.select_playback(None, {"items": []}), (None, "NOT PLAYING", "idle"))

    def test_untrusted_image_host_never_downloaded(self):
        sample = track()
        sample["album"]["images"] = [{"url": "https://evil.example/cover.png"}]
        with patch.object(spotify.requests, "get") as mock_get:
            image, accent = spotify.album_artwork(sample)
        self.assertEqual((image, accent), ("", spotify.DEFAULT_ACCENT))
        mock_get.assert_not_called()

    def test_refresh_token_failure_never_exposes_secrets(self):
        response = Mock(status_code=400)
        with patch.dict(os.environ, {
            "SPOTIFY_CLIENT_ID": "client-id",
            "SPOTIFY_SECRET_ID": "private-secret",
            "SPOTIFY_REFRESH_TOKEN": "private-refresh",
        }), patch.object(spotify.requests, "post", return_value=response):
            with self.assertRaises(spotify.SpotifyUnavailable) as err:
                spotify.access_token(force=True)
        self.assertNotIn("private-secret", str(err.exception))
        self.assertNotIn("private-refresh", str(err.exception))

    def test_retry_once_on_401(self):
        spotify.TOKEN.update(value="old", expires_at=time.monotonic() + 100)
        unauthorized = Mock(status_code=401)
        success = Mock(status_code=200)
        success.json.return_value = {"items": []}
        token_response = Mock(status_code=200)
        token_response.json.return_value = {"access_token": "new", "expires_in": 3600}
        with patch.dict(os.environ, {
            "SPOTIFY_CLIENT_ID": "test-id",
            "SPOTIFY_SECRET_ID": "test-secret",
            "SPOTIFY_REFRESH_TOKEN": "test-refresh",
        }), patch.object(spotify.requests, "get", side_effect=[unauthorized, success]) as mock_get, patch.object(spotify.requests, "post", return_value=token_response) as mock_post:
            data = spotify.spotify_get(spotify.CURRENT_URL)
        self.assertEqual(data, {"items": []})
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(mock_post.call_count, 1)

    def test_no_content_is_normal(self):
        spotify.TOKEN.update(value="valid", expires_at=time.monotonic() + 100)
        with patch.object(spotify.requests, "get", return_value=Mock(status_code=204)):
            self.assertIsNone(spotify.spotify_get(spotify.CURRENT_URL))

    def test_svg_rendering_and_escaping(self):
        payload = track("<script>alert('x')</script>")
        with patch.object(spotify, "spotify_get", return_value={"is_playing": True, "item": payload}):
            response = spotify.app.test_client().get("/?background_color=BAD")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("image/svg+xml", response.content_type)
        self.assertIn("NOW PLAYING", body)
        self.assertIn("&lt;script&gt;", body)
        self.assertNotIn("<script>", body)
        self.assertIn('fill="#0d1117"', body)
        self.assertIn("s-maxage=60", response.headers["Cache-Control"])

    def test_paused_track_survives_recent_history_error(self):
        current = {"is_playing": False, "item": track("Paused Song")}
        with patch.object(spotify, "spotify_get", side_effect=[
            current, spotify.SpotifyUnavailable("recent history denied")
        ]):
            response = spotify.app.test_client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Widget-Status"], "ok")
        self.assertIn("PAUSED", response.get_data(as_text=True))

    def test_offline_fallback_keeps_svg_available(self):
        with patch.object(spotify, "spotify_get", side_effect=spotify.SpotifyUnavailable("API error")):
            response = spotify.app.test_client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Widget-Status"], "degraded")
        self.assertIn("SPOTIFY UNAVAILABLE", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
