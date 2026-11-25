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
        self.assertEqual((image, accent), ("", spotify.DEFAULT_PALETTE))
        mock_get.assert_not_called()

    def test_palette_is_extracted_from_album_once_and_changes_with_art(self):
        sample = track("Chromatic")
        sample["album"]["images"] = [{"url": "https://i.scdn.co/image/cover"}]
        image_response = Mock(status_code=200, content=b"fake-image-bytes")
        image_response.headers = {"Content-Type": "image/jpeg"}

        red = [(220, 40, 55), (235, 100, 45), (170, 50, 75), (255, 180, 130)]
        blue = [(45, 65, 220), (50, 140, 230), (90, 50, 190), (100, 155, 240)]
        with patch.object(spotify.requests, "get", return_value=image_response) as mock_get:
            with patch.object(spotify, "ColorThief") as color_thief:
                color_thief.return_value.get_palette.side_effect = [red, blue]
                red_image, red_palette = spotify.album_artwork(sample)
                blue_image, blue_palette = spotify.album_artwork(sample)

        self.assertTrue(red_image.startswith("data:image/jpeg;base64,"))
        self.assertEqual(red_image, blue_image)
        self.assertNotEqual(red_palette["accent"], blue_palette["accent"])
        self.assertNotEqual(red_palette["accent_2"], blue_palette["accent_2"])
        self.assertEqual(len(red_palette), 4)
        self.assertEqual(color_thief.return_value.get_palette.call_count, 2)
        self.assertEqual(mock_get.call_count, 2)

    def test_gradient_effects_take_colors_from_album_palette(self):
        sample = track("Color test")
        custom = {
            "accent": "ca3456",
            "accent_2": "de8654",
            "accent_3": "98458a",
            "accent_4": "f3b467",
        }
        with patch.object(spotify, "spotify_get", return_value={"is_playing": True, "item": sample}):
            with patch.object(spotify, "album_artwork", return_value=("", custom)):
                response = spotify.app.test_client().get("/")
        svg = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('stop-color="#de8654"', svg)
        self.assertIn('stop-color="#98458a"', svg)
        self.assertIn('stop-color="#f3b467"', svg)
        self.assertIn('stroke="#98458a"', svg)
        self.assertIn('fill="url(#ambient-secondary)"', svg)
        self.assertIn('fill="url(#bars-gradient)"', svg)

    def test_album_tinted_text_is_readable_and_changes_by_cover(self):
        colors = [
            {"accent": "bb1020", "accent_2": "c53050", "accent_3": "882244", "accent_4": "e64567"},
            {"accent": "104ea2", "accent_2": "236bd6", "accent_3": "124289", "accent_4": "35a7ee"},
        ]
        outputs = []
        with patch.object(spotify, "spotify_get", return_value={"is_playing": True, "item": track()}):
            for palette in colors:
                with patch.object(spotify, "album_artwork", return_value=("", palette)):
                    response = spotify.app.test_client().get("/")
                svg = response.get_data(as_text=True)
                # Verify actual SVG rather than using the synthetic view's fallback colors.
                import re
                title_color = re.search(r'fill="#([0-9a-f]{6})">Sample Song</text>', svg).group(1)
                artist_color = re.search(r'fill="#([0-9a-f]{6})">Sample Artist</text>', svg).group(1)
                background = spotify.relative_luminance("26343d")
                self.assertGreaterEqual((spotify.relative_luminance(title_color) + .05) / (background + .05), 7)
                self.assertGreaterEqual((spotify.relative_luminance(artist_color) + .05) / (background + .05), 4.5)
                outputs.append((title_color, artist_color))
        self.assertNotEqual(outputs[0], outputs[1])

    def test_equalizer_has_one_shared_horizontal_moving_palette(self):
        import xml.etree.ElementTree as ET

        with patch.object(spotify, "spotify_get", return_value={
            "is_playing": True, "item": track()
        }):
            with patch.object(spotify, "album_artwork", return_value=("", {
                "accent": "8e325a", "accent_2": "e56b82",
                "accent_3": "863b80", "accent_4": "fdc77d"
            })):
                svg = spotify.app.test_client().get("/").get_data(as_text=True)

        ns = "{http://www.w3.org/2000/svg}"
        root = ET.fromstring(svg)
        gradient = root.find(f".//{ns}linearGradient[@id='bars-gradient']")
        self.assertIsNotNone(gradient)
        self.assertEqual(gradient.get("gradientUnits"), "userSpaceOnUse")
        self.assertEqual(gradient.get("spreadMethod"), "repeat")
        self.assertEqual((gradient.get("x1"), gradient.get("y1")), ("0", "0"))
        self.assertEqual((gradient.get("x2"), gradient.get("y2")), ("600", "0"))
        stops = gradient.findall(f"{ns}stop")
        self.assertEqual([item.get("stop-color") for item in stops], [
            "#fdc77d", "#e56b82", "#863b80", "#8e325a", "#fdc77d"
        ])
        self.assertEqual(stops[0].get("stop-opacity"), stops[-1].get("stop-opacity"))
        moving = gradient.find(f"{ns}animateTransform")
        self.assertIsNotNone(moving)
        self.assertEqual(moving.get("attributeName"), "gradientTransform")
        self.assertEqual(moving.get("type"), "translate")
        self.assertEqual(moving.get("from"), "0 0")
        self.assertEqual(moving.get("to"), "600 0")
        self.assertEqual(moving.get("repeatCount"), "indefinite")

        bars = root.find(f".//{ns}g[@id='equalizer-wave']")
        self.assertEqual(bars.get("fill"), "url(#bars-gradient)")
        self.assertEqual(len(bars.findall(f"{ns}rect")), 50)
        self.assertTrue(all(bar.get("fill") is None for bar in bars.findall(f"{ns}rect")))

    def test_shared_gradient_still_moves_when_bars_are_static(self):
        import xml.etree.ElementTree as ET

        with patch.object(spotify, "spotify_get", side_effect=[
            None, {"items": []}
        ]):
            svg = spotify.app.test_client().get("/").get_data(as_text=True)
        ns = "{http://www.w3.org/2000/svg}"
        root = ET.fromstring(svg)
        self.assertIsNotNone(root.find(
            f".//{ns}linearGradient[@id='bars-gradient']/{ns}animateTransform"
        ))
        equalizer = root.find(f".//{ns}g[@id='equalizer-wave']")
        self.assertEqual(len(equalizer.findall(f"{ns}rect")), 50)
        self.assertEqual(len(equalizer.findall(f".//{ns}animate")), 0)

    def test_monochrome_cover_stays_monochrome(self):
        with patch.object(spotify, "ColorThief") as color_thief:
            color_thief.return_value.get_palette.return_value = [
                (35, 35, 35), (175, 175, 175), (90, 90, 90), (220, 220, 220)
            ]
            colors = spotify.palette_from_cover(b"sample")
        for color in colors.values():
            self.assertEqual(color[0:2], color[2:4])
            self.assertEqual(color[2:4], color[4:6])

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
        self.assertIn("NOW PLAYING", body)  # Accessible SVG description only
        self.assertNotIn(">NOW PLAYING</text>", body)
        self.assertIn("url(#bars-gradient)", body)
        self.assertIn("url(#ambient-secondary)", body)
        self.assertIn("&lt;script&gt;", body)
        self.assertNotIn("<script>", body)
        self.assertIn('fill="#0d1117"', body)
        self.assertIn("s-maxage=60", response.headers["Cache-Control"])

    def test_clean_card_contains_only_title_and_artist_as_visible_copy(self):
        with patch.object(spotify, "spotify_get", return_value={"is_playing": True, "item": track("Sample Song")}):
            response = spotify.app.test_client().get("/")
        body = response.get_data(as_text=True)
        self.assertIn(">Sample Song</text>", body)
        self.assertIn(">Sample Artist</text>", body)
        self.assertNotIn(">Spotify API", body)
        self.assertNotIn(">NOW PLAYING</text>", body)
        self.assertIn('width="600" height="190"', body)

    def test_adaptive_title_keeps_short_metadata_large_and_single_line(self):
        data = spotify.music_text_layout("November", "TWO LANES")
        self.assertEqual(data["title_lines"], ("November",))
        self.assertEqual(data["title_font_size"], 29)
        self.assertEqual(data["artist_display"], "TWO LANES")
        self.assertEqual(data["artist_font_size"], 18)
        self.assertEqual(data["artist_y"], 126)

    def test_long_title_wraps_to_two_lines_without_losing_information(self):
        long_title = "The Incredible Adventures of an Astronaut Walking Through the Stars"
        data = spotify.music_text_layout(long_title, "An Artist")
        self.assertEqual(len(data["title_lines"]), 2)
        self.assertEqual(" ".join(data["title_lines"]), long_title)
        self.assertLessEqual(data["title_font_size"], 25)
        self.assertEqual(data["artist_y"], 145)
        for line in data["title_lines"]:
            self.assertLessEqual(spotify.approximate_text_width(
                line, data["title_font_size"], bold=True
            ), spotify.TEXT_MAX_WIDTH)

    def test_very_long_metadata_is_trimmed_only_after_adapting(self):
        long_title = "GIGANTIC " + "SUPERCALIFRAGILISTICEXPIALIDOCIOUS" * 18
        long_artist = "International Orchestra of Seriously Unreasonably Long Names " * 9
        data = spotify.music_text_layout(long_title, long_artist)
        self.assertEqual(len(data["title_lines"]), 2)
        self.assertTrue(data["title_lines"][1].endswith("…"))
        self.assertTrue(data["artist_display"].endswith("…"))
        self.assertEqual(data["artist_font_size"], 14)
        self.assertLessEqual(spotify.approximate_text_width(
            data["title_lines"][1], data["title_font_size"], bold=True
        ), spotify.TEXT_MAX_WIDTH)
        self.assertLessEqual(spotify.approximate_text_width(
            data["artist_display"], data["artist_font_size"]
        ), spotify.TEXT_MAX_WIDTH)

    def test_long_names_render_inside_metadata_clip_with_accessible_full_title(self):
        import xml.etree.ElementTree as ET
        long_title = "The Incredible Adventures of an Astronaut Walking Through the Stars"
        sample = track(long_title)
        sample["artists"] = [{"name": "A Remarkably Long Ensemble of Artists from Around the Universe" * 4}]
        with patch.object(spotify, "spotify_get", return_value={
            "is_playing": True, "item": sample
        }):
            response = spotify.app.test_client().get("/")
        root = ET.fromstring(response.get_data(as_text=True))
        ns = "{http://www.w3.org/2000/svg}"
        metadata = root.find(f".//{ns}g[@id='metadata-text']")
        self.assertEqual(metadata.get("clip-path"), "url(#metadata-clip)")
        clip = root.find(f".//{ns}clipPath[@id='metadata-clip']/{ns}rect")
        self.assertIsNotNone(clip)
        self.assertEqual(clip.get("width"), "368")
        titles = metadata.findall(f"{ns}text[@data-role='track-title']")
        artist = metadata.find(f"{ns}text[@data-role='track-artist']")
        self.assertEqual(len(titles), 2)
        self.assertEqual(artist.get("y"), "145")
        self.assertEqual(artist.get("font-size"), "14")
        self.assertTrue(artist.text.endswith("…"))
        self.assertIn(long_title, root.find(f"{ns}title").text)

    def test_metadata_normalizes_newlines_and_handles_wide_unicode(self):
        title = "  Música\n  特别长的歌名大世界星辰世界世界世界    "
        artist = "  Uma   Banda\r\ncom  vários   artistas  "
        data = spotify.music_text_layout(title, artist)
        self.assertTrue(data["title_lines"][0].startswith("Música"))
        self.assertNotIn("\n", " ".join(data["title_lines"]))
        self.assertNotIn("\r", data["artist_display"])
        self.assertFalse("  " in data["artist_display"])
        self.assertTrue(all(
            spotify.approximate_text_width(line, data["title_font_size"], bold=True)
            <= spotify.TEXT_MAX_WIDTH for line in data["title_lines"]
        ))

    def test_paused_track_survives_recent_history_error(self):
        current = {"is_playing": False, "item": track("Paused Song")}
        with patch.object(spotify, "spotify_get", side_effect=[
            current, spotify.SpotifyUnavailable("recent history denied")
        ]):
            response = spotify.app.test_client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Widget-Status"], "ok")
        self.assertIn("PAUSED", response.get_data(as_text=True))

    def test_recently_played_bars_animate_decoratively(self):
        with patch.object(spotify, "spotify_get", side_effect=[
            None, {"items": [{"track": track("November")}]}
        ]):
            response = spotify.app.test_client().get("/")
        body = response.get_data(as_text=True)
        self.assertIn("LAST PLAYED", body)
        self.assertEqual(body.count('attributeName="height"'), 50)
        self.assertEqual(body.count('attributeName="y"'), 50)
        self.assertGreaterEqual(body.count('attributeName="opacity"'), 53)  # Includes ambient wave opacity
        self.assertIn('repeatCount="indefinite"', body)

    def test_equalizer_uses_main_style_independent_pulses(self):
        import xml.etree.ElementTree as ET

        with patch.object(spotify, "spotify_get", side_effect=[
            None, {"items": [{"track": track("November")}]}
        ]):
            svg = spotify.app.test_client().get("/").get_data(as_text=True)
        root = ET.fromstring(svg)
        ns = "{http://www.w3.org/2000/svg}"
        equalizer = root.find(f".//{ns}g[@id='equalizer-wave']")
        self.assertIsNotNone(equalizer)
        bars = equalizer.findall(f"{ns}rect")
        self.assertEqual(len(bars), 50)
        durations, starts, peaks, splines = [], [], [], []
        for i, bar in enumerate(bars):
            self.assertAlmostEqual(float(bar.get("x")), .5 + i * 12, places=2)
            self.assertAlmostEqual(float(bar.get("width")), 11.0, places=2)
            self.assertAlmostEqual(float(bar.get("rx")), 4.2, places=2)
            self.assertAlmostEqual(float(bar.get("ry")), 4.2, places=2)
            if i > 0:
                self.assertAlmostEqual(float(bar.get("x")) - (.5 + (i - 1) * 12 + 11), 1.0, places=2)
            height = bar.find(f"{ns}animate[@attributeName='height']")
            vertical = bar.find(f"{ns}animate[@attributeName='y']")
            opacity = bar.find(f"{ns}animate[@attributeName='opacity']")
            self.assertIsNotNone(height)
            self.assertIsNotNone(vertical)
            self.assertIsNotNone(opacity)
            duration = int(height.get("dur").removesuffix("ms"))
            durations.append(duration)
            starts.append(height.get("begin"))
            splines.append(height.get("keySplines"))
            self.assertGreaterEqual(duration, 800)
            self.assertLessEqual(duration, 1600)
            self.assertEqual(height.get("calcMode"), "spline")
            self.assertEqual(height.get("keyTimes"), "0;0.5;1")
            self.assertEqual(len(height.get("keySplines").split(";")), 2)
            self.assertEqual(opacity.get("values"), ".35;.95;.35")
            self.assertEqual(height.get("begin"), vertical.get("begin"))
            self.assertEqual(height.get("begin"), opacity.get("begin"))
            self.assertEqual(height.get("dur"), vertical.get("dur"))
            self.assertEqual(height.get("dur"), opacity.get("dur"))
            heights = [float(v) for v in height.get("values").split(";")]
            positions = [float(v) for v in vertical.get("values").split(";")]
            self.assertEqual(len(heights), 3)
            self.assertEqual(heights[0], heights[2])
            self.assertGreater(heights[1], heights[0] + 17)
            peaks.append(heights[1])
            self.assertTrue(all(abs(h + y - 189) < .01 for h, y in zip(heights, positions)))
        self.assertGreater(len(set(durations)), 30)
        self.assertGreater(len(set(starts)), 30)
        self.assertGreater(len(set(splines)), 30)
        self.assertGreater(len(set(peaks)), 25)

    def test_playing_uses_original_main_duration_range(self):
        import xml.etree.ElementTree as ET
        with patch.object(spotify, "spotify_get", return_value={
            "is_playing": True, "item": track()
        }):
            svg = spotify.app.test_client().get("/").get_data(as_text=True)
        root = ET.fromstring(svg)
        ns = "{http://www.w3.org/2000/svg}"
        equalizer = root.find(f".//{ns}g[@id='equalizer-wave']")
        durations = [
            int(rect.find(f"{ns}animate[@attributeName='height']").get("dur").removesuffix("ms"))
            for rect in equalizer.findall(f"{ns}rect")
        ]
        self.assertEqual(len(durations), 50)
        self.assertTrue(all(500 <= value <= 1000 for value in durations))
        self.assertGreater(len(set(durations)), 30)
        self.assertNotIn('transform="translate(549,17)"', svg)

    def test_animated_background_keeps_album_colors_and_moves_even_when_idle(self):
        import xml.etree.ElementTree as ET

        with patch.object(spotify, "spotify_get", side_effect=[
            None, {"items": []}
        ]):
            svg = spotify.app.test_client().get("/").get_data(as_text=True)

        root = ET.fromstring(svg)
        ns = "{http://www.w3.org/2000/svg}"
        gradients = {
            node.get("id"): node
            for node in root.findall(f".//{ns}radialGradient")
        }
        for name in ("ambient-album", "ambient-secondary"):
            self.assertIn(name, gradients)
            animated_attributes = {
                anim.get("attributeName")
                for anim in gradients[name].findall(f"{ns}animate")
            }
            self.assertIn("cx", animated_attributes)
            self.assertIn("cy", animated_attributes)

        animated_lights = [
            node for node in root.findall(f".//{ns}rect")
            if node.get("fill") in ("url(#ambient-album)", "url(#ambient-secondary)")
        ]
        self.assertEqual(len(animated_lights), 2)
        for rect in animated_lights:
            animation = rect.find(f"{ns}animate[@attributeName='opacity']")
            self.assertIsNotNone(animation)
            self.assertEqual(animation.get("repeatCount"), "indefinite")
        equalizer = root.find(f".//{ns}g[@id='equalizer-wave']")
        self.assertEqual(len(equalizer.findall(f"{ns}rect")), 50)
        self.assertEqual(len(equalizer.findall(f".//{ns}animate")), 0)

    def test_background_glows_and_trails_animate_independently_of_playback(self):
        import xml.etree.ElementTree as ET

        with patch.object(spotify, "spotify_get", side_effect=[
            None, {"items": []}
        ]):
            svg = spotify.app.test_client().get("/").get_data(as_text=True)
        ns = "{http://www.w3.org/2000/svg}"
        root = ET.fromstring(svg)
        effects = root.find(f".//{ns}g[@id='background-effects']")
        self.assertIsNotNone(effects)
        orbs = effects.find(f"{ns}g[@id='ambient-glow-orbs']")
        self.assertIsNotNone(orbs)

        for name in ("glow-orb-primary", "glow-orb-secondary"):
            ellipse = orbs.find(f"{ns}ellipse[@id='{name}']")
            self.assertIsNotNone(ellipse)
            self.assertTrue(ellipse.get("fill").startswith("url(#glow-orb-"))
            animations = {
                anim.get("attributeName"): anim
                for anim in ellipse.findall(f"{ns}animate")
            }
            self.assertEqual(set(animations), {"cx", "cy", "opacity"})
            for animation in animations.values():
                self.assertEqual(animation.get("repeatCount"), "indefinite")
                values = animation.get("values").split(";")
                self.assertEqual(values[0], values[-1])
                self.assertGreater(len(set(values)), 2)

        wave = root.find(f".//{ns}g[@id='background-wave-trails']")
        self.assertIsNotNone(wave)
        motion = wave.find(f"{ns}animateTransform")
        self.assertEqual(motion.get("attributeName"), "transform")
        self.assertEqual(motion.get("type"), "translate")
        self.assertEqual(motion.get("repeatCount"), "indefinite")
        self.assertEqual(motion.get("values").split(";")[0],
                         motion.get("values").split(";")[-1])
        self.assertGreaterEqual(len(wave.findall(f"{ns}path")), 3)

        particles = root.find(f".//{ns}g[@id='background-particles']")
        self.assertIsNotNone(particles.find(f"{ns}animateTransform"))

        clipped_content = root.find(f"{ns}g[@clip-path='url(#card-clip)']")
        children = list(clipped_content)
        self.assertLess(children.index(effects),
                        children.index(root.find(f".//{ns}g[@id='equalizer-wave']")))
        self.assertEqual(len(root.findall(f".//{ns}g[@id='equalizer-wave']/{ns}rect")), 50)
        self.assertEqual(len(root.findall(f".//{ns}g[@id='equalizer-wave']//{ns}animate")), 0)

    def test_background_glow_colors_follow_the_cover(self):
        import xml.etree.ElementTree as ET

        palette = {
            "accent": "dd3355", "accent_2": "ee6655",
            "accent_3": "ba4477", "accent_4": "ffbb66"
        }
        with patch.object(spotify, "spotify_get", return_value={
            "is_playing": True, "item": track()
        }), patch.object(spotify, "album_artwork", return_value=("", palette)):
            svg = spotify.app.test_client().get("/").get_data(as_text=True)

        root = ET.fromstring(svg)
        ns = "{http://www.w3.org/2000/svg}"
        primary = root.find(f".//{ns}radialGradient[@id='glow-orb-primary']")
        secondary = root.find(f".//{ns}radialGradient[@id='glow-orb-secondary']")
        self.assertEqual(
            [stop.get("stop-color") for stop in primary.findall(f"{ns}stop")],
            ["#dd3355", "#ffbb66", "#ffbb66"],
        )
        self.assertEqual(
            [stop.get("stop-color") for stop in secondary.findall(f"{ns}stop")],
            ["#ee6655", "#ba4477", "#ba4477"],
        )

    def test_empty_state_has_static_equalizer(self):
        with patch.object(spotify, "spotify_get", side_effect=[
            None, {"items": []}
        ]):
            response = spotify.app.test_client().get("/")
        body = response.get_data(as_text=True)
        self.assertIn("NOT PLAYING", body)
        self.assertNotIn('attributeName="height"', body)

    def test_offline_fallback_keeps_svg_available(self):
        with patch.object(spotify, "spotify_get", side_effect=spotify.SpotifyUnavailable("API error")):
            response = spotify.app.test_client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Widget-Status"], "degraded")
        self.assertIn("SPOTIFY UNAVAILABLE", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
