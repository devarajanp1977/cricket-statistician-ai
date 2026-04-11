# Cricket Statistician AI

An AI-powered cricket statistician that answers natural-language questions about cricket using real historical data and GPT-4.1 (via GitHub Models API). Built on two comprehensive data sources covering ball-by-ball match data and structured Test match scorecards.

## Data Sources

### 1. Cricsheet.org — Ball-by-Ball Match Data

- **21,380+ matches** across all major formats: Tests (900), ODIs (3,100), T20Is (5,102), IPL (1,169), and 40+ other domestic/international competitions
- **Ball-by-ball granularity**: every delivery records batter, bowler, non-striker, runs (batter/extras/total), extras breakdown (wides/noballs/byes/legbyes), wickets (dismissal kind, fielders), reviews, and player replacements
- **Match metadata**: dates, venue, city, toss, outcome (winner, margin, method), officials, player of match, event/series info, powerplays, season
- **Player registry**: 17,834 people with cross-referenced IDs from 12 sources (ESPNcricinfo, CricketArchive, BCCI, Pulse, etc.)
- **Formats available**: JSON (primary, v1.1.0), CSV ("Ashwin" format), YAML (deprecated), XML (experimental)
- **License**: Open Data Commons Attribution License
- **Size**: ~99 MB (all matches, JSON)

### 2. Kaggle — Test Cricket Matches Dataset (1877–2024)

- **Comprehensive Test match history** from 1877 to 2024 across 6 structured CSV files (~20.7 MB, 100 columns):
  - `test_Matches_Data.csv` — match date, location, venue (stadium/city/country), result, margin of victory, toss details, umpires, match referees, player of the match
  - `test_Batting_Card.csv` — batting scorecards
  - `test_Bowling_Card.csv` — bowling scorecards
  - `test_Fow_Card.csv` — fall of wickets
  - `test_Partnership_Card.csv` — partnership data
  - `players_info.csv` — player details and roles
- **License**: Apache 2.0

### How the Datasets Complement Each Other

| Aspect | Cricsheet | Kaggle Test Dataset |
|---|---|---|
| Scope | All formats (Tests, ODIs, T20Is, leagues) | Tests only (1877–2024) |
| Granularity | Ball-by-ball deliveries | Scorecard-level (batting/bowling cards) |
| Unique data | Reviews, replacements, powerplays, ball-level extras | Partnerships, fall of wickets, structured player info |
| Best for | Detailed match analysis, bowling/batting patterns | Historical Test statistics, career aggregates |

## Tech Stack

- **LLM**: GPT-4.1 (via GitHub Models API)
- **Database**: DuckDB (fast analytical queries on local data)
- **Data**: Cricsheet JSON + Kaggle CSVs → normalized into DuckDB tables
- **Backend**: Python + FastAPI
- **Frontend**: TBD

## Project Structure

```
scripts/
  download_data.py    # Download Cricsheet JSON + Kaggle CSVs
  load_cricsheet.py   # Parse JSON → DuckDB (matches, innings, deliveries, wickets, players)
  load_kaggle.py      # Load CSVs → DuckDB (kaggle_matches, batting, bowling, fow, partnerships)
  refresh.py          # One-command: download + load everything
data/                 # (git-ignored)
  raw/cricsheet/      # Downloaded JSON match files + register CSVs
  raw/kaggle/         # Downloaded Kaggle CSVs
  db/cricket.duckdb   # Unified database
```

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Download data + build database (first run takes a few minutes)
cd scripts
python refresh.py

# Or step-by-step:
python download_data.py --source cricsheet   # ~99 MB download
python download_data.py --source kaggle      # requires kaggle CLI
python load_cricsheet.py                     # parse JSON → DuckDB
python load_kaggle.py                        # load CSVs → DuckDB
```

### Refresh data (get latest matches)

```bash
python scripts/refresh.py --force   # re-downloads + reloads everything
python scripts/refresh.py           # incremental (only loads new matches)
```

## Expose It On Your Own Domain

If you want this app reachable on your own domain while it still runs on your laptop, the practical setup is:

- GoDaddy DNS points your domain to your home public IP
- Your router forwards ports 80 and 443 to the laptop
- Caddy runs on the laptop for HTTPS and reverse proxying
- Uvicorn runs locally on port 8080
- Optional: a small GoDaddy DDNS script updates the A record if your home IP changes

This repo now supports running the UI behind a URL prefix such as `/cricstats` by setting `APP_BASE_PATH=/cricstats` before starting the app.

### Recommended topology

- Public URL: `https://devarajan.in/cricstats`
- Caddy on laptop: listens on `80/443`
- FastAPI on laptop: listens on `127.0.0.1:8080`

### 1. Start the app with a base path

PowerShell:

```powershell
$env:APP_BASE_PATH = "/cricstats"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

### 2. Put Caddy in front of it

Use the sample config in [deploy/Caddyfile](deploy/Caddyfile).

That config serves the app at `/cricstats` and terminates HTTPS automatically.

### 3. Point GoDaddy DNS to your laptop's home IP

If `devarajan.in` is only for this laptop-hosted app:

- Create or update the `A` record for `@`
- Point it to your current public IPv4 address

If your home IP changes often, use [scripts/update_godaddy_dns.py](scripts/update_godaddy_dns.py) with a scheduled task.

### 4. Configure router port forwarding

Forward these TCP ports to the laptop's LAN IP:

- `80 -> laptop:80`
- `443 -> laptop:443`

Reserve the laptop's LAN IP in your router if possible, so forwarding does not break after reconnects.

### 5. Start Caddy

Once DNS and router forwarding are correct, run Caddy with the sample config and it will request the TLS certificate automatically.

### Important note

Path-based hosting like `devarajan.in/cricstats` works only if your apex domain is pointing at this laptop. If `devarajan.in` is already hosted somewhere else, use a subdomain instead, such as `cricstats.devarajan.in`.

## DuckDB Tables

### From Cricsheet (ball-by-ball)
| Table | Description | Key columns |
|---|---|---|
| `matches` | One row per match | match_id, match_type, teams, venue, outcome, toss, dates |
| `innings` | One row per innings | match_id, innings_num, batting_team, target, declared |
| `deliveries` | One row per ball | match_id, innings_num, over, ball, batter, bowler, runs, extras |
| `wickets` | One row per dismissal | match_id, over, ball, player_out, kind, fielders |
| `players` | Cricsheet register | cricsheet_id, name, key_cricinfo, key_cricketarchive |

### From Kaggle (Test scorecards)
| Table | Description |
|---|---|
| `kaggle_matches` | Test match details (1877–2024) |
| `kaggle_batting` | Batting scorecards |
| `kaggle_bowling` | Bowling scorecards |
| `kaggle_fow` | Fall of wickets |
| `kaggle_partnerships` | Partnership data |
| `kaggle_players` | Player information |

## Status

🚧 **Phase 2 complete** — data pipeline + GPT-4.1 query engine + chat UI with ECharts.
