# Spotify Now Playing — Novatorem v2

A Spotify music card for [Emanuel's GitHub profile](https://github.com/emanuelsacoman), powered by Flask and deployed with Vercel.

## Visual design

A clean 600 × 190 px cinematic card containing only cover art, track title and artist — **no Spotify logo, playback label or footer**. Colors are sampled from each album cover with ColorThief (as in the original `main` implementation), then used for **all ambient gradients, animated bars, light trails and glowing frame details**. There is no fixed green branding in the card: all colored effects follow the current album. The title and artist now receive lighter tints from the cover palette, with contrast checks against the dark-glass background. Monochrome covers produce monochrome lighting.

Playback status is still preserved in the SVG accessibility description:
- **playing**: energetic equalizer animation;
- **recent**: gentler animation on the latest recently played track;
- **paused / idle / unavailable**: static equalizer background.

The typography adapts to each song: compact titles keep the prominent 29 px style; longer song names shrink slightly or wrap onto two lines. Artists also scale down as needed. Only exceptionally long text gets an ellipsis, and the SVG title keeps the complete song and artist names for accessibility. A clipping boundary prevents overflow even with different GitHub/browser fonts.

The background equalizer now has **50 columns**, 11 px wide with **exactly 1 px of empty space between neighbors** (and a 0.5 px margin at either edge). A **single SVG gradient**, measured in global card coordinates rather than per-bar coordinates, spans the entire equalizer. Album palette colors sweep continuously from left to right in a seamless 10-second loop, even if the bars are static. Independently animated 500–1,000 ms pulses still follow the original `main` style; recently played tracks pulse more slowly. The backdrop now has additional visible animation: **two large album-colored glow orbs drift across the card**, while luminous ribbon trails and small light particles move at different speeds. These effects remain behind the cover and labels and continue even when no track is playing. Only the bars stop in idle states. SVG-native animations replace the original CSS/`foreignObject`, keeping the GitHub-README-friendly renderer. Motion is decorative and **not** derived from an audio stream. The widget still returns a safe SVG fallback if Spotify is unavailable.

## Embed in your profile

```md
### 🎧 Listening to Spotify

<div align="center">

[![Spotify](https://novatorem-bice-omega.vercel.app/api/spotify?background_color=0d1117&border_color=30363d)](https://open.spotify.com/user/31ikb6rcqitw6twbnfim7cmjsuvm)

</div>
```

The query parameters `background_color` and `border_color` accept six-digit hex colors without `#`. GitHub caches remote images, so updates may not appear immediately.

## Environment variables

Set these **only in Vercel's environment settings**, never in Git or public files.

| Name | Purpose |
| --- | --- |
| `SPOTIFY_CLIENT_ID` | Spotify developer app ID |
| `SPOTIFY_SECRET_ID` | Spotify developer app secret |
| `SPOTIFY_REFRESH_TOKEN` | OAuth token with playback-reading scopes |

The OAuth user must authorize the scopes `user-read-currently-playing` and `user-read-recently-played`.

**Important:** Developer Dashboard refresh tokens expire after six months under Spotify's 2026 policy. The server refreshes short-lived **access tokens**, but the user must complete OAuth again to replace an expired **refresh token**. After changing Vercel environment variables, redeploy the project.

## Run locally

Use Python 3.12:

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python api/spotify.py
```

Create a local `.env` that is excluded from version control, then visit `http://localhost:5000/`. Without credentials the endpoint intentionally renders an unavailable-state SVG.

## Run tests

```bash
python -m unittest discover -s tests -v
python -m py_compile api/spotify.py
```

Tests use mocks and don't require Spotify credentials. GitHub Actions runs them on pull requests to `main`.

## Security and reliability

- URL allowlist for album images (Spotify CDN only) and Spotify track links.
- Album download size cap and network timeouts.
- Single retry on a `401` response, with cached access tokens.
- No secrets in SVG responses or error logs.
- Fallback states keep GitHub's image from becoming a broken icon.
- CDN caching of 60 seconds, although GitHub's image proxy can cache longer.

Originally inspired by [novatorem/novatorem](https://github.com/novatorem/novatorem).
