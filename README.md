# Spotify Now Playing — Novatorem v2

A Spotify music card for [Emanuel's GitHub profile](https://github.com/emanuelsacoman), powered by Flask and deployed with Vercel.

## What it shows

- **NOW PLAYING** — an active track on Spotify.
- **LAST PLAYED** — the newest recently played track when playback isn't active.
- **PAUSED** — the paused track when recent history is unavailable.
- **NOT PLAYING** — no track data available.
- **SPOTIFY UNAVAILABLE** — safe SVG fallback if the API, credentials, or album artwork fails.

The card is a **native SVG** (no HTML \`foreignObject\`), designed for GitHub READMEs. The colored bars are decorative, not a real audio analyzer.

## Embed in your profile

\`\`\`md
### 🎧 Listening to Spotify

<div align="center">

[![Spotify](https://novatorem-bice-omega.vercel.app/api/spotify?background_color=0d1117&border_color=30363d)](https://open.spotify.com/user/31ikb6rcqitw6twbnfim7cmjsuvm)

</div>
\`\`\`

The query parameters \`background_color\` and \`border_color\` accept six-digit hex colors without \`#\`. GitHub caches remote images, so updates may not appear immediately.

## Environment variables

Set these **only in Vercel's environment settings**, never in Git or public files.

| Name | Purpose |
| --- | --- |
| \`SPOTIFY_CLIENT_ID\` | Spotify developer app ID |
| \`SPOTIFY_SECRET_ID\` | Spotify developer app secret |
| \`SPOTIFY_REFRESH_TOKEN\` | OAuth token with playback-reading scopes |

The OAuth user must authorize the scopes \`user-read-currently-playing\` and \`user-read-recently-played\`.

**Important:** Developer Dashboard refresh tokens expire after six months under Spotify's 2026 policy. The server refreshes short-lived **access tokens**, but the user must complete OAuth again to replace an expired **refresh token**. After changing Vercel environment variables, redeploy the project.

## Run locally

Use Python 3.12:

\`\`\`bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python api/spotify.py
\`\`\`

Create a local \`.env\` that is excluded from version control, then visit \`http://localhost:5000/\`. Without credentials the endpoint intentionally renders an unavailable-state SVG.

## Run tests

\`\`\`bash
python -m unittest discover -s tests -v
python -m py_compile api/spotify.py
\`\`\`

Tests use mocks and don't require Spotify credentials. GitHub Actions runs them on pull requests to \`main\`.

## Security and reliability

- URL allowlist for album images (Spotify CDN only) and Spotify track links.
- Album download size cap and network timeouts.
- Single retry on a \`401\` response, with cached access tokens.
- No secrets in SVG responses or error logs.
- Fallback states keep GitHub's image from becoming a broken icon.
- CDN caching of 60 seconds, although GitHub's image proxy can cache longer.

Originally inspired by [novatorem/novatorem](https://github.com/novatorem/novatorem).
