# Architettura — CrossPoint Newsletter Server

## 1. Flusso End-to-End

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                        CrossPoint Newsletter Server                        │
│                                                                             │
│  ┌──────────┐    ┌─────────────┐    ┌──────────────────┐                   │
│  │  IMAP    │    │  Transform  │    │     Storage      │                   │
│  │  Ingest  │───▶│  HTML→EPUB  │───▶│  SQLite + Files  │                   │
│  │          │    │  + Images   │    │                  │                   │
│  └──────────┘    └─────────────┘    └────────┬─────────┘                   │
│       ▲                                      │                             │
│       │ polling                    ┌─────────┼─────────┐                   │
│       │ periodico                  │         │         │                   │
│                              ┌─────▼───┐ ┌───▼─────┐ ┌▼────────┐          │
│  ┌──────────┐                │ Calibre  │ │  OPDS   │ │  WebUI  │          │
│  │ Casella  │                │ Export   │ │Standalone│ │ Gestione│          │
│  │ Email    │                │         │ │ (FastAPI)│ │         │          │
│  │ Dedicata │                └────┬────┘ └────┬────┘ └─────────┘          │
│  └──────────┘                     │           │                            │
└───────────────────────────────────┼───────────┼────────────────────────────┘
                                    │           │
                              ┌─────▼───┐ ┌────▼────┐
                              │ Calibre │ │  Feed   │
                              │  -Web   │ │  OPDS   │
                              │  OPDS   │ │  :8400  │
                              └────┬────┘ └────┬────┘
                                   │           │
                                   └─────┬─────┘
                                         │
                                    ┌────▼────┐
                                    │CrossPoint│
                                    │ Reader   │
                                    │ (Wi-Fi)  │
                                    └────┬────┘
                                         │
                                    ┌────▼────┐
                                    │ microSD  │
                                    │ (lettura │
                                    │ offline) │
                                    └─────────┘
```

## 2. Contratto con il Firmware CrossPoint

Il Newsletter Server comunica con il reader **esclusivamente** tramite il protocollo OPDS 1.2 standard. Non richiede modifiche al firmware.

| Aspetto | Specifica |
|:---|:---|
| **Formato output** | EPUB 3.0 |
| **Protocollo** | OPDS 1.2 Atom XML |
| **Autenticazione** | HTTP Basic Auth |
| **Metadati OPDS** | `title` = subject email, `author` = nome newsletter, `series` = nome newsletter, `series_index` = progressivo |
| **Copertina** | `<link rel="http://opds-spec.org/image">` — PNG 600×800 |
| **Download** | `<link rel="http://opds-spec.org/acquisition" type="application/epub+zip">` |
| **Resilienza offline** | EPUB scaricati sulla microSD restano leggibili con server spento |

Questo contratto è stato **validato su hardware** nello Step firmware 08:
- OPDS 1.2 Atom XML navigato con successo.
- HTTP Basic Auth funzionante (testato con Calibre-Web su Tower).
- EPUB scaricato, salvato su microSD, letto offline.

## 3. Componenti e Responsabilità

### 3.1 Ingest (`src/crosspoint_newsletter/ingest/`)

| Responsabilità | Dettaglio |
|:---|:---|
| Polling IMAP | Connessione periodica alla casella email dedicata |
| Matching | Associazione email → newsletter per `sender_email` + regole |
| Deduplica | Verifica `Message-ID` contro il database |
| Estrazione | Corpo HTML + immagini inline (CID) |
| Output | Record `Issue` in stato `pending` |

Non fa: conversione, storage permanente, distribuzione.

### 3.2 Transform (`src/crosspoint_newsletter/transform/`)

| Responsabilità | Dettaglio |
|:---|:---|
| Pulizia HTML | Rimozione script, tracking pixel, link unsubscribe |
| Generazione EPUB | EPUB 3.0 con metadati serie (Calibre tags) |
| Ottimizzazione immagini | Grayscale 8-bit, resize ≤800px, compressione |
| Generazione copertina | PNG 600×800 programmatica |
| Quality gate | Validazione EPUB (dimensione, integrità, encoding) |
| Output | File EPUB + Issue in stato `done` o `error` |

Non fa: ingestione email, archiviazione, distribuzione.

### 3.3 Storage (`src/crosspoint_newsletter/storage/`)

| Responsabilità | Dettaglio |
|:---|:---|
| Database | CRUD Newsletter + Issue, query per feed, migrazioni |
| Filesystem | Organizzazione `data/epub/`, `data/raw/`, `data/covers/` |
| Retention | Applicazione policy di cancellazione (max issues, max age) |
| CLI | Comandi `cn` per gestione da terminale |

Non fa: conversione, rete, distribuzione.

### 3.4 Serve (`src/crosspoint_newsletter/serve/`)

| Responsabilità | Dettaglio |
|:---|:---|
| OPDS standalone | Feed OPDS 1.2 via FastAPI (canale primario: root, recenti, serie, download, cover) |
| Calibre export | *(Deprecato/Disabilitato)* — Sostituito dal feed standalone per non sporcare la libreria Calibre |
| WebUI | Dashboard, CRUD newsletter, libreria, impostazioni |
| API REST | Endpoint JSON per WebUI e client esterni |
| Autenticazione | HTTP Basic Auth su OPDS e WebUI |

Non fa: ingestione, conversione, storage diretto.

## 4. Stack Tecnologico

| Componente | Tecnologia |
|:---|:---|
| Linguaggio | Python ≥ 3.12 |
| Web framework | FastAPI + Uvicorn |
| Template engine | Jinja2 (WebUI) |
| Database | SQLite 3 (con FTS5 opzionale) |
| Generazione EPUB | ebooklib |
| Parsing HTML | BeautifulSoup4 + lxml |
| Immagini | Pillow |
| Configurazione | python-dotenv + PyYAML |
| Containerizzazione | Docker (python:3.12-slim) + Docker Compose |
| Deploy target | Unraid Tower (your-unraid-ip) |

## 5. Confini Architetturali

```text
┌──────────────────────────────────────┐
│         Responsabilità SERVER        │
│                                      │
│  • Ingestione email                  │
│  • Conversione HTML → EPUB           │
│  • Catalogazione e storage           │
│  • Feed OPDS + API + WebUI           │
│  • Autenticazione                    │
│  • Backup e retention                │
└──────────────────────────────────────┘
           ▲ OPDS 1.2 (unico punto di contatto)
           │
┌──────────┴───────────────────────────┐
│         Responsabilità FIRMWARE      │
│                                      │
│  • Navigazione catalogo OPDS         │
│  • Download EPUB su microSD          │
│  • Rendering e lettura e-ink         │
│  • Gestione Wi-Fi e credenziali      │
│  • Funzionamento offline             │
└──────────────────────────────────────┘
```

- Il server **non contiene** codice ESP32, driver display o logica firmware.
- Il firmware **non contiene** logica di ingestione, conversione o schedulazione.
- L'unico accoppiamento è il **contratto OPDS 1.2** documentato nella sezione 2.
