# Hitman Widgets

Built for personal use, shared in case it's useful to anyone else.

An Android app with three home screen widgets. **HITMAN only so far.**

- **Elusive Targets**: live countdown to the current or next Elusive Target, with arrows to flip between them
- **Twitch Drops**: every active HITMAN drop as a card with its image and a live countdown, scrollable when there are a lot
- **News**: recent HITMAN news, patch notes and roadmaps with thumbnails, tap a post to open it

## Getting started

Install the APK and open the app. The instructions for adding the widgets are on its main screen, along with a **Refresh now** button and the load status of each widget.

## Data sources

- Elusive Target data fetched from a community project, [HITMAPS](https://www.hitmaps.com)
- Twitch Drops data fetched from a community project, [twitchdrops.app](https://twitchdrops.app)
- News fetched directly from [ioi.dk/hitman/news](https://ioi.dk/hitman/news)
  
## How it works

A script runs on a schedule and writes JSON files, and GitHub Pages serves them as static files. The app fetches those files directly and draws the widgets itself, refreshing every few minutes (renews every 5).
