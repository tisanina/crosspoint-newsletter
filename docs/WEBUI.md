# WebUI & REST API — CrossPoint Newsletter Server

## Panoramica

A partire dallo **Step N-07**, il server standalone include una WebUI moderna, responsive e dark-themed per il monitoraggio e la gestione completa della pipeline newsletter, unita a un set di API REST JSON.

La WebUI e il server OPDS girano sullo stesso processo FastAPI (`cn serve`), di default su porta `8400`:
- **WebUI (Browser)**: `http://<ip-server>:8400/ui/` (o semplicemente `http://<ip-server>:8400/` con redirect automatico per browser)
- **OPDS Feed (CrossPoint)**: `http://<ip-server>:8400/opds`
- **REST API**: `http://<ip-server>:8400/api/...`

---

## Pagine della WebUI

### 1. Dashboard (`/ui/`)
- **Metriche aggregate**: Numero totale di pubblicazioni gestite, uscite totali, uscite completate (`done`), uscite con errori (`error`).
- **Storage**: Spazio occupato su disco e totale file gestiti.
- **Configurazione e-Reader CrossPoint**: Scheda rapida per configurare `opds.json` del reader con URL (`http://<ip>:8400/opds`) e stato credenziali.
- **Ultime uscite elaborate**: Tabella con le ultime 10 newsletter ricevute, stato e link al dettaglio.

### 2. Gestione Newsletter (`/ui/newsletters`)
- **Elenco pubblicazioni**: Visualizzazione di tutte le newsletter configurate con slug, mittente email, stato (attiva/disattivata), policy di retention e conteggio uscite.
- **Modifica & Retention**: Finestra modale interattiva per modificare al volo nome, indirizzo email mittente e policy di retention (max numeri o max giorni). Include l'opzione *"Applica subito la pulizia retention"* per eliminare all'istante dal disco gli EPUB eccedenti le nuove soglie.
- **Azione Attiva/Disattiva**: Toggle immediato per abilitare o sospendere l'ingestione di una determinata testata.
- **Aggiunta rapida**: Form per censire una nuova testata specificando nome, slug (opzionale), mittente email e limiti di retention (max numeri o max giorni).
- **Eliminazione**: Rimozione sicura con cancellazione a cascata dal DB e dal filesystem di tutti i file EPUB e raw associati.

### 3. Libreria Uscite (`/ui/library`)
- **Esplorazione uscite**: Tabella completa delle newsletter generate con filtri dinamici:
  - Filtro per testata / pubblicazione
  - Filtro per stato (`done`, `pending`, `error`)
  - Ricerca testuale per oggetto / titolo
- **Dettagli immediati**: Visualizzazione stato di conversione, indice serie, data/ora e anteprima dell'errore (se presente).

### 4. Dettaglio Uscita (`/ui/issues/{id}`)
- **Metadati completi**: ID univoco, Message-ID email originale, data ricezione e timestamp elaborazione.
- **Percorsi file**: Posizione del file `.epub` e del file `.eml` raw su disco.
- **Rielaborazione manuale**: Bottone "Riprocessa ora" per forzare la riesecuzione della pipeline Transform (HTML→EPUB) a partire dal file raw originale conservato.

### 5. Impostazioni & Connessioni (`/ui/settings`)
- **Configurazione IMAP Interattiva**: Form per inserire e modificare in qualsiasi momento host server (es. `imap.gmail.com`), porta SSL (es. `993`), account utente, password/app-password e cartella monitorata (`INBOX`).
- **Salvataggio Locale Persistente**: Il pulsante **"💾 Salva Configurazione IMAP"** scrive in modo protetto nel file `.env` locale del server e aggiorna all'istante le variabili di runtime senza dover riavviare il servizio.
- **Test Connessione Live**: Il pulsante **"⚡ Prova Connessione Ora"** verifica in tempo reale sia le credenziali inserite nel form (anche prima di salvarle) sia quelle correnti, mostrando a video un banner verde (conferma con cartella trovata) o rosso (dettaglio errore/autenticazione).
- **Fuso Orario & Localizzazione (Timezone)**: Scheda per visualizzare il fuso attivo (default `Europe/Rome`), l'orario corrente locale calcolato dal server, e un form per selezionare o modificare il fuso IANA con salvataggio persistente in `.env` e aggiornamento a caldo. Tutti i timestamp nella WebUI e nei log vengono calcolati in base a questo fuso.
- **Configurazione OPDS**: Parametri del feed, autenticazione abilitata/disabilitata e credenziali.
- **Storage & Filesystem**: Cartella dati, percorsi DB SQLite ed EPUB, conservazione email raw (`CN_KEEP_RAW`), conteggio file e byte totali occupati.

### 6. Da Approvare / Inbox Discovery & Blacklist (`/ui/inbox`)
- **Scoperta automatica mittenti**: Tutte le email ricevute via IMAP da indirizzi non ancora censiti nel database non vengono scartate ma parcheggiate nella coda di triage con il file `.eml` originale conservato.
- **Badge di notifica**: Il menu superiore e la dashboard mostrano dinamicamente un badge con il conteggio delle newsletter in attesa di approvazione.
- **Approva con 1-Click**: Censimento istantaneo della pubblicazione con estrazione automatica del nome mittente, generazione dello slug, conversione retroattiva dell'email in EPUB e pubblicazione immediata su OPDS.
- **Scarta**: Rimozione immediata della singola email dalla coda con cancellazione del file raw su disco.
- **🚫 Blocca Mittente (Blacklist)**: Rimozione dell'email e inserimento permanente dell'indirizzo mittente nella blacklist: le future email inviate da quell'indirizzo non verranno mai più proposte né salvate.
- **Gestione Blacklist**: Tabella con l'elenco completo dei mittenti bloccati, data di inserimento, motivo e pulsante per **sbloccare** con 1-click (oltre alla possibilità di aggiungere mittenti a mano).

---

## Endpoint REST API

Oltre alle pagine HTML, sono disponibili endpoint JSON per automazioni o dashboard esterne:

### `GET /api/newsletters`
Restituisce la lista di tutte le newsletter configurate.
```json
[
  {
    "id": "c1f76d49-...",
    "name": "The Bull",
    "slug": "the-bull",
    "sender_email": "thebull@substack.com",
    "enabled": true,
    "retention_max_issues": 50,
    "retention_max_age_days": null
  }
]
```

### `POST /api/newsletters`
Crea una nuova newsletter.
**Payload JSON:**
```json
{
  "name": "Morning Brief",
  "slug": "morning-brief",
  "sender_email": "brief@example.com",
  "retention_max_issues": 30,
  "retention_max_age_days": 60,
  "enabled": true
}
```

### `GET /api/newsletters/{id}`
Restituisce il dettaglio di una singola newsletter per ID o per slug.

### `DELETE /api/newsletters/{id}`
Elimina una newsletter e tutti i suoi file associati.

### `GET /api/issues`
Elenco uscite con parametri di query opzionali:
- `newsletter_id`: Filtra per ID newsletter
- `status`: Filtra per stato (`done`, `pending`, `error`)
- `search`: Ricerca per testo nel subject
- `limit`: Numero massimo di risultati (default 50, max 200)
- `offset`: Paginazione

### `POST /api/issues/{id}/reprocess`
Riprocessa una determinata uscita a partire dal messaggio raw originale.

### `POST /api/settings/imap`
Salva la configurazione IMAP su file `.env` e aggiorna il server in memoria.
**Payload JSON:**
```json
{
  "host": "imap.example.com",
  "port": 993,
  "user": "newsletters@example.com",
  "password": "mysecretpassword",
  "folder": "INBOX"
}
```

### `POST /api/imap/test`
Esegue un test di connessione immediato verso la casella IMAP. Accetta opzionalmente parametri specifici nel body oppure usa quelli salvati:
```json
{
  "success": true,
  "message": "Connessione IMAP a imap.example.com:993 riuscita (cartella 'INBOX' trovata)"
}
```

### `GET /api/inbox`
Restituisce la lista delle email in attesa di approvazione.
```json
[
  {
    "id": "c1f76d49-...",
    "email_message_id": "<...>",
    "sender_header": "John Doe <john@example.com>",
    "sender_email": "john@example.com",
    "sender_name": "John Doe",
    "subject": "Issue #42",
    "received_at": "2026-09-23T10:00:00Z",
    "raw_eml_path": "data/raw/inbox/....eml",
    "created_at": "2026-09-23T10:05:00Z"
  }
]
```

### `POST /api/inbox/{id}/approve`
Approva un'email in inbox, crea o riusa la testata newsletter, converte il raw EML in EPUB e rimuove l'email dalla coda.

### `POST /api/inbox/{id}/block`
Scarta l'email dall'inbox e inserisce permanentemente il mittente nella blacklist.

### `DELETE /api/inbox/{id}`
Rimuove ed elimina l'email dalla coda inbox cancellando l'eventuale file `.eml` raw associato.
Supporta il parametro opzionale `?blacklist=true` per inserire contestualmente il mittente nella blacklist.

### `GET /api/blacklist`
Restituisce l'elenco di tutti i mittenti bloccati.
```json
[
  {
    "id": "b182f0...",
    "sender_email": "spammer@bad.com",
    "sender_name": "Spammer",
    "reason": "Rifiutato da Inbox",
    "created_at": "2026-09-25T14:30:00+00:00"
  }
]
```

### `POST /api/blacklist`
Aggiunge manualmente un indirizzo email alla blacklist:
```json
{
  "email": "unwanted@newsletter.com",
  "name": "Unwanted Newsletter",
  "reason": "Non gradita"
}
```

### `DELETE /api/blacklist/{id_or_email}`
Rimuove e sblocca un mittente dalla blacklist per ID o per indirizzo email.

---


## Avvio del Servizio

```bash
# Avvio in ascolto su tutte le interfacce per accesso da LAN ed e-reader:
cn serve --host 0.0.0.0 --port 8400
```
