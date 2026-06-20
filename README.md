# Hitman Widgets

Built for personal use, shared in case it's useful to anyone else.

Three Android home screen widgets, built with KWGT (Kustom Widget Maker):

- **Elusive Targets** — ongoing and incoming targets, any number of each
- **Twitch Drops** — active drop campaigns for HITMAN World of Assassination and 007 First Light, separate widgets per game, handles multiple drop batches running at once
- **News** — recent IO Interactive press releases with images, links straight to the article

No login, no tracking, no ads. Just JSON files that refresh on a timer and widgets that read them.

## Data sources

- Elusive Target data fetched from a community project, [HITMAPS](https://www.hitmaps.com)
- Twitch Drops data fetched from a community project, [twitchdrops.app](https://twitchdrops.app)
- News fetched directly from [ioi.dk/press](https://ioi.dk/press)

None of this is official IOI, HITMAPS, or twitchdrops.app tooling. It just reads public pages they already publish.

## How it works

A script runs on a schedule, writes three JSON files, and GitHub Pages serves them as static files. KWGT on your phone fetches those files directly and renders the widgets. There's no app to install beyond KWGT itself, and no server to maintain beyond what GitHub already runs for free.

Setup steps are in `SETUP.md`.
