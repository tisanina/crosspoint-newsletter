# CrossPoint Newsletter Server — Server OPDS Standalone

## 1. Panoramica

Il **Server OPDS Standalone** è il canale di distribuzione primario ed esclusivo per la distribuzione delle newsletter convertite in EPUB verso il reader e-ink **CrossPoint**.

Basato su **FastAPI** e conforme allo standard **OPDS 1.2 (Atom XML)**, permette all'e-reader di navigare il catalogo delle serie, visualizzare le copertine ottimizzate per e-ink a 480×800, e scaricare direttamente i file EPUB sulla scheda microSD via Wi-Fi.

Questa soluzione autonoma garantisce una separazione netta rispetto a Calibre-Web, preservando la biblioteca personale di ebook da newsletter periodiche ed effimere.

---

## 2. Specifiche degli Endpoint

Tutti i feed restituiscono contenuti con header:
`Content-Type: application/atom+xml;profile=opds-catalog;charset=utf-8`

| Metodo | Percorso | Descrizione | Formato Risposta |
|:---|:---|:---|:---|
| `GET` | `/` | Reindirizzamento automatico a `/opds` | `302 / 307 Redirect` |
| `GET` | `/opds` | Catalogo di navigazione principale (root feed) | Atom XML OPDS 1.2 |
| `GET` | `/opds/recent` | Feed di acquisizione con le newsletter più recenti | Atom XML OPDS 1.2 |
| `GET` | `/opds/newsletter/{slug}` | Feed di acquisizione per specifica serie (es. `the-bull`) | Atom XML OPDS 1.2 |
| `GET` | `/opds/download/{issue_id}` | Download del file EPUB (`Accept-Ranges: bytes`) | `application/epub+zip` |
| `GET` | `/opds/cover/{issue_id}` | Copertina PNG (da disco o generata al volo) | `image/png` |
| `GET` | `/opds/search?q={query}` | Ricerca per parole chiave nel titolo | Atom XML OPDS 1.2 |
| `GET` | `/api/status` | Healthcheck e diagnostica di sistema | `application/json` |

---

## 3. Autenticazione e Sicurezza

Il server supporta **HTTP Basic Auth** standard:
- Configurato tramite le variabili d'ambiente `CN_OPDS_USERNAME` e `CN_OPDS_PASSWORD`.
- In caso di credenziali assenti o errate, le rotte protette `/opds*` rispondono con `401 Unauthorized` e l'header standard:
  ```http
  WWW-Authenticate: Basic realm="CrossPoint OPDS"
  ```
- **Rotte pubbliche**: L'endpoint `/api/status` (healthcheck) e l'endpoint di anteprima copertine `/opds/cover/{issue_id}` sono esenti da autenticazione per consentire il monitoraggio e il rendering fluido delle miniature sia nei reader OPDS che nella WebUI.
- Se `CN_OPDS_USERNAME` o `CN_OPDS_PASSWORD` non sono impostati nel file `.env`, l'autenticazione è disabilitata (utile per sviluppo locale).


---

## 4. Configurazione Variabili d'Ambiente

Nel file `.env`:

```ini
# --- OPDS server ---
CN_OPDS_HOST=0.0.0.0
CN_OPDS_PORT=8400
CN_OPDS_USERNAME=reader
CN_OPDS_PASSWORD=supersecret
```

---

## 5. Utilizzo e Avvio

### 5.1 Tramite CLI (`cn`)

Avviare il server in ascolto sulla porta predefinita (8400):
```bash
cn serve
```

Specificare parametri personalizzati o abilitare il live-reload in sviluppo:
```bash
cn serve --host 0.0.0.0 --port 8400 --reload
```

### 5.2 Tramite Docker Compose

Nel container Docker, il server è esposto sulla porta configurata (`8400` di default):
```bash
docker compose up -d newsletter
```

---

## 6. Configurazione su CrossPoint Waveshare

Sul reader e-ink CrossPoint, configurare il catalogo remoto in `/.crosspoint/opds.json` sulla microSD:

```json
{
  "name": "CrossPoint Newsletters",
  "url": "http://your-server-ip:8400/opds",
  "username": "reader",
  "password": "supersecret"
}
```

Il reader sarà in grado di:
1. Connettersi via Wi-Fi all'indirizzo IP del server (es. Tower su `your-server-ip:8400`).
2. Sfogliare la sezione **"Ultime Uscite"** o navigare per singola serie (es. *The Bull*, *Storto*).
3. Scaricare il numero desiderato con un solo tocco; l'EPUB viene salvato sulla microSD per la lettura offline.
