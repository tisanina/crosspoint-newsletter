# CrossPoint Newsletter Server

> **Newsletter-to-EPUB pipeline for e-ink readers.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![CI/CD](https://github.com/tisanina/crosspoint-newsletter/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/tisanina/crosspoint-newsletter/actions/workflows/docker-publish.yml)
[![GHCR](https://img.shields.io/badge/ghcr.io-tisanina%2Fcrosspoint--newsletter-blue?logo=docker&logoColor=white)](https://github.com/tisanina/crosspoint-newsletter/pkgs/container/crosspoint-newsletter)

A self-hosted server that connects to an IMAP mailbox, ingests email newsletters, converts them into optimized EPUB 3.0 books for e-ink displays, and serves them through an integrated OPDS 1.2 catalog and web dashboard. Designed specifically for the CrossPoint e-ink reader (Waveshare ESP32-S3 e-paper) but compatible with any OPDS-capable e-reader or reading app.

---

## Features

- **Automated IMAP Ingestion**: Connects over IMAP/SSL with periodic background polling and zero external broker dependencies.
- **HTML Sanitization**: Cleans bloated email HTML by stripping tracking pixels, advertisement beacons, tracking redirects, scripts, and unsubscribe footers.
- **E-Ink Image Optimization**: Downscales and processes embedded and inline (CID) images into 8-bit grayscale with optional Floyd-Steinberg dithering optimized for e-paper screens (up to 480×800 / 600×800).
- **Procedural Cover Generation**: Automatically generates stylish, readable SVG/PNG book covers with issue numbers, titles, and publication dates.
- **EPUB 3.0 Packaging**: Assembles clean, validated EPUB 3.0 books embedded with Calibre-compatible series metadata and navigation tables.
- **Built-in OPDS 1.2 Catalog**: Complete Atom XML catalog featuring navigation by recent issues, publication series, full-text search, and direct EPUB download.
- **Responsive Web Dashboard & Multi-language (i18n)**: Clean modern WebUI supporting both English 🇬🇧 and Italian 🇮🇹 (extensible to additional languages), configurable via navbar switcher or server settings. Manage subscriptions, review incoming issues, configure credentials, and trigger manual ingestion.
- **Triage Inbox & Discovery**: Automatically detects emails from new or unknown senders and places them in a triage queue for one-click approval or dismissal.
- **Sender Blacklist**: Drop unwanted spam or notifications before processing.
- **Configurable Retention Policies**: Set automated rolling cleanup policies based on issue count or maximum retention age per newsletter series.
- **Zero-Maintenance Storage**: Embedded SQLite database with optional FTS5 search and straightforward filesystem storage—no external database servers required.
- **Docker-First Deployment**: Lightweight container ready for Unraid, Docker Compose, TrueNAS, or standard Linux servers.

---

## Architecture

```text
Email (IMAP/SSL) → Ingest → Transform (HTML→EPUB) → Storage (SQLite + filesystem)
                                                           ↓
                                                 OPDS 1.2 Server + WebUI
                                                           ↓
                                          E-ink Reader (Wi-Fi → microSD → offline reading)
```

The system is organized into four modular components:

1. **Ingest (`ingest/`)**: Connects to the dedicated IMAP mailbox, polls for unread messages, extracts MIME structures, and maps incoming emails to configured newsletter series. Unrecognized senders are saved to a triage queue.
2. **Transform (`transform/`)**: Sanitizes HTML markup, resolves inline images, optimizes media for e-ink displays (grayscale dithering and downscaling), generates covers, and compiles valid EPUB 3.0 files.
3. **Storage (`storage/`)**: Manages the SQLite database for catalog state and the local filesystem storage structure (`conf/`, `epub/`, `raw/`, `covers/`), applying retention rules and consistency audits.
4. **Serve (`serve/`)**: Fast, asynchronous FastAPI application serving the OPDS 1.2 feed, REST API endpoints, and Jinja2-powered administrative web dashboard.

---

## Quick Start — Docker (Recommended)

The fastest way to deploy CrossPoint Newsletter Server is using Docker Compose.

```bash
# 1. Clone the repository
git clone https://github.com/tisanina/crosspoint-newsletter.git
cd crosspoint-newsletter

# 2. Configure environment and subscriptions
cp .env.example .env
# Edit .env with your IMAP credentials and settings
cp config/newsletters.example.yaml config/newsletters.yaml
# Add your newsletter subscriptions

# 3. Build and launch containers
cd docker
docker compose build
docker compose up -d
```

Once running, access the Web Dashboard at:
```text
http://your-server:8400/ui/
```

---

## Quick Start — Local Development

For local development or testing without Docker:

```bash
# 1. Create and activate a Python 3.12+ virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install package in editable mode with development dependencies
pip install -e '.[dev]'

# 3. Initialize configuration files
cp .env.example .env
cp config/newsletters.example.yaml config/newsletters.yaml

# 4. Run linters and tests
ruff check src/
pytest

# 5. Start the server
cn serve
```

---

## Configuration

### Environment Variables

Configuration is loaded from environment variables or the `.env` file located at the project root.

| Variable | Description | Default |
|:---|:---|:---|
| `TZ` / `CN_TIMEZONE` | Timezone for logs, dates, and cover generation | `Europe/Rome` |
| `CN_IMAP_HOST` | IMAP mail server hostname (e.g. `imap.gmail.com`) | *None* |
| `CN_IMAP_PORT` | IMAP SSL port | `993` |
| `CN_IMAP_USER` | Dedicated email address for newsletter subscriptions | *None* |
| `CN_IMAP_PASSWORD` | IMAP password or app-specific password | *None* |
| `CN_IMAP_FOLDER` | Remote IMAP mailbox folder to inspect | `INBOX` |
| `CN_POLL_INTERVAL_MINUTES` | Frequency of background email ingestion checks (in minutes) | `15` |
| `CN_ENABLE_AUTO_POLL` | Whether to automatically poll IMAP in the background | `true` |
| `CN_KEEP_RAW` | Retain original `.eml` files on disk for debugging or triage | `true` |
| `CN_CONF_DIR` | Path to persistent configuration and SQLite database | `/app/conf` or `./data` |
| `CN_DATA_DIR` | Base directory for generated content (EPUBs, covers, raw email) | `/app/data` or `./data` |
| `CN_OPDS_HOST` | Bind address for OPDS feed and WebUI | `0.0.0.0` |
| `CN_OPDS_PORT` | Port for OPDS feed and WebUI | `8400` |
| `CN_OPDS_USERNAME` | Optional HTTP Basic Auth username for OPDS & WebUI | *None* |
| `CN_OPDS_PASSWORD` | Optional HTTP Basic Auth password for OPDS & WebUI | *None* |
| `CN_NEWSLETTERS_CONFIG`| Path to newsletter series definition YAML file | `./config/newsletters.yaml` |

### Newsletter Subscriptions

Newsletters can be managed via the WebUI or declared in `config/newsletters.yaml`:

```yaml
newsletters:
  - name: "The Pragmatic Engineer"
    sender: "newsletter@pragmaticengineer.com"
    enabled: true
    retention:
      max_issues: 10          # Keep only the latest 10 issues
      max_age_days: null      # null = unlimited

  - name: "Platformer"
    sender: "casey@platformer.news"
    enabled: true
    sender_rules:
      - type: "header_from_contains"
        value: "platformer"
    retention:
      max_issues: null
      max_age_days: 30        # Prune issues older than 30 days
```

---

## Unraid Deployment

CrossPoint Newsletter Server is optimized for Unraid and supports tiered storage:

- **Unraid Template**: An Unraid XML template is provided in [`docker/crosspoint-newsletter.xml`](docker/crosspoint-newsletter.xml).
- **Installation**: Copy the template into `/boot/config/plugins/dockerMan/templates-user/` or install it via the Docker tab (Community Applications integration coming soon).
- **Split Volume Architecture**:
  - **Cache SSD (`/app/conf`)**: Stores the active SQLite database (`newsletter.db`) and credentials for fast read/writes without spinning up the main array.
  - **Array (`/app/data`)**: Houses large EPUB archives, generated cover art, and raw `.eml` files.
- Refer to [`docker/crosspoint-newsletter.xml`](docker/crosspoint-newsletter.xml) and the [Operations Guide](docs/OPERATIONS.md) for detailed configuration paths and variables.

---

## OPDS E-Reader Setup

The server publishes a compliant OPDS 1.2 catalog feed compatible with the CrossPoint e-paper reader, KOReader, Moon+ Reader, Foliate, and any OPDS client.

- **OPDS Catalog Endpoint**:
  ```text
  http://<your-server-ip>:8400/opds
  ```
- **Authentication**: Supports standard HTTP Basic Authentication if `CN_OPDS_USERNAME` and `CN_OPDS_PASSWORD` are configured.
- **Offline Reading**: EPUB files downloaded over Wi-Fi are stored on the reader's local storage or microSD card and remain accessible offline without requiring continuous server connectivity.

---

## CLI Reference

The project includes a command-line utility `cn` for local maintenance, ingestion triggers, and storage inspection:

```bash
# Start the web server and OPDS catalog
cn serve [--host 0.0.0.0] [--port 8400] [--reload]

# Run the ingestion and EPUB conversion pipeline manually
cn pipeline run [--limit 10] [--no-mark-seen]
cn pipeline run --local-eml /path/to/email.eml

# Manage unrecognized incoming emails in the triage queue
cn inbox list
cn inbox approve <id> [--name "My Newsletter"] [--slug "my-newsletter"]
cn inbox dismiss <id>

# Inspect and manage newsletter publications
cn newsletter list
cn newsletter add "Tech Weekly" --sender "news@techweekly.com" --max-issues 20
cn newsletter update <slug> --max-age-days 60 --apply-retention

# Inspect issues and storage metrics
cn issue list [--newsletter <slug>] [--status done]
cn issue stats

# Enforce retention rules and prune expired issues
cn retention run [--dry-run] [--newsletter <slug>]

# Audit and verify consistency between SQLite DB and disk storage
cn storage check
```

---

## Project Structure

```text
crosspoint-newsletter/
├── config/
│   └── newsletters.example.yaml   # Newsletter subscription rules template
├── docker/
│   ├── Dockerfile                 # Multi-stage production container
│   ├── docker-compose.yml         # Container orchestration
│   ├── crosspoint-newsletter.xml  # Unraid Community Applications template
│   └── entrypoint.sh              # Container startup script
├── docs/                          # Architecture and operational documentation
│   ├── ARCHITECTURE.md            # Detailed end-to-end architecture
│   ├── DATA_MODEL.md              # Database schemas and entities
│   ├── OPDS_SERVER.md             # OPDS 1.2 feed specification
│   ├── OPERATIONS.md              # Production deployment & operations guide
│   ├── PRIVACY.md                 # Privacy considerations & tracking removal
│   └── WEBUI.md                   # WebUI endpoints & REST API specification
├── src/crosspoint_newsletter/
│   ├── __init__.py
│   ├── cli.py                     # CLI management interface (`cn`)
│   ├── config.py                  # Environment and runtime configuration
│   ├── ingest/                    # IMAP polling, email parsing, sender matching
│   ├── transform/                 # HTML sanitization, e-ink image processing, EPUB 3.0 packaging
│   ├── storage/                   # SQLite database, file store, retention engine
│   └── serve/                     # FastAPI OPDS 1.2 catalog, REST API, Web dashboard
├── tests/                         # Unit and integration test suite
├── .env.example                   # Environment configuration template
├── pyproject.toml                 # Build configuration and dependencies
├── LICENSE                        # MIT license
└── README.md                      # Project documentation
```

---

## Documentation

For in-depth details on design, development, and maintenance, see the documentation in `docs/`:

- [Architecture](docs/ARCHITECTURE.md) — System components, boundaries, and data flow.
- [Data Model](docs/DATA_MODEL.md) — Database schema, SQLite migrations, and entity relations.
- [OPDS Server](docs/OPDS_SERVER.md) — Atom XML catalog endpoints and e-reader contract.
- [Operations Guide](docs/OPERATIONS.md) — Deployment, backup, logging, and troubleshooting.
- [Privacy Policy](docs/PRIVACY.md) — Tracker stripping, telemetry removal, and sanitization rules.
- [WebUI & API](docs/WEBUI.md) — Web interface design and REST API endpoints.

---

## Contributing

Contributions are welcome! Please follow these steps:

1. **Fork** the repository and create a feature branch (`git checkout -b feature/amazing-feature`).
2. **Make your changes** following PEP 8 guidelines and existing architectural patterns.
3. **Run tests and linters** to ensure everything passes:
   ```bash
   ruff check src/
   pytest
   ```
4. **Commit your changes** with descriptive commit messages (`git commit -m "Add support for XYZ"`).
5. **Push to your branch** (`git push origin feature/amazing-feature`) and open a **Pull Request**.

---

## License

This project is licensed under the terms of the [MIT License](LICENSE).
