# Manuale Operativo — CrossPoint Newsletter Server

Questo documento riassume le operazioni di gestione, manutenzione ordinaria, ispezione dei log e aggiornamento del server CrossPoint Newsletter in produzione.

---

## 🧭 Comandi Docker Rapidi

Tutti i comandi vanno eseguiti all'interno della cartella principale del repository:

| Azione | Comando |
| :--- | :--- |
| **Avvio in background** | `docker compose -f docker/docker-compose.prod.yml up -d` |
| **Arresto del servizio** | `docker compose -f docker/docker-compose.prod.yml down` |
| **Riavvio del servizio** | `docker compose -f docker/docker-compose.prod.yml restart` |
| **Visualizzazione Log in tempo reale** | `docker compose -f docker/docker-compose.prod.yml logs -f --tail 100` |
| **Stato Healthcheck container** | `docker inspect --format='{{json .State.Health}}' crosspoint-newsletter` |

---

## ⚡ Trigger Manuale del Polling Posta

Oltre al ciclo periodico automatico (default: ogni 15 minuti), è possibile forzare immediatamente la verifica della casella email e la conversione in EPUB in tre modi:

1. **Dalla WebUI**: cliccando sul pulsante **"⚡ Controlla Posta Ora"** presente nell'intestazione della Dashboard (`http://<ip-server>:8400/ui/`).
2. **Via REST API**:
   ```bash
   curl -X POST http://<ip-server>:8400/api/poll/run
   ```
3. **Da Terminale all'interno del container**:
   ```bash
   docker exec -it crosspoint-newsletter cn pipeline run
   ```

---

## 📊 Monitoraggio & Healthcheck (`/api/status`)

L'endpoint `GET /api/status` è pubblico e accessibile senza autenticazione per consentire a sistemi di monitoraggio e all'healthcheck di Docker (configurato con frequenza di **30 minuti**) di verificare la salute del server:

```bash
curl -s http://localhost:8400/api/status | jq .
```

I log del server e gli access log HTTP includono timestamp e data (`YYYY-MM-DD HH:MM:SS`) per facilitare il tracciamento e il debug.


Esempio di output:
```json
{
  "status": "healthy",
  "service": "crosspoint-newsletter",
  "version": "0.1.1",
  "uptime_seconds": 3600,
  "newsletters_count": 4,
  "total_issues": 12,
  "done_issues": 12,
  "error_issues": 0,
  "storage_files": 12,
  "storage_mb": 14.8,
  "volumes": {
    "conf": "/app/conf",
    "data": "/app/data",
    "db_path": "/app/conf/newsletter.db",
    "epub_dir": "/app/data/epub",
    "raw_dir": "/app/data/raw",
    "covers_dir": "/app/data/covers"
  },
  "polling": {
    "enabled": true,
    "running": true,
    "is_polling": false,
    "interval_minutes": 15,
    "last_poll_at": "2026-09-23T15:30:00+00:00",
    "next_poll_at": "2026-09-23T15:45:00+00:00",
    "total_polls_run": 8
  },
  "imap": {
    "configured": true,
    "host": "imap.gmail.com",
    "port": 993,
    "folder": "INBOX"
  }
}
```

---

## 🔄 Procedura di Aggiornamento Software

Quando vengono rilasciati nuovi aggiornamenti sul repository Git:

```bash
# 1. Entra nella cartella di installazione
cd /mnt/user/appdata/crosspoint-newsletter

# 2. Scarica le modifiche
git pull origin main

# 3. Ricostruisci l'immagine Docker e riavvia senza downtime per i volumi
docker compose -f docker/docker-compose.prod.yml build
docker compose -f docker/docker-compose.prod.yml up -d

# 4. Verifica lo stato dopo il riavvio
curl -s http://localhost:8400/api/status
```

*Nota*: Tutti i dati del database SQLite (sul volume `conf`) e gli archivi EPUB (sul volume `data`) rimangono al 100% intatti durante l'aggiornamento.

---

## 🛠️ Risoluzione Problemi Comuni

- **Errore di connessione IMAP nei log**:
  Verifica le credenziali in `.env.prod`. Con account Google/Gmail è obbligatorio usare una **Password per le app** a 16 caratteri e non la password principale dell'account Google.
- **Porta già in uso su Unraid**:
  Se la porta `8400` o `8080` è già occupata da un altro servizio su Tower, modifica `HOST_PORT` o `HOST_OPDS_PORT` nel file `.env.prod` (es. `HOST_PORT=8450`) e riesegui `docker compose -f docker/docker-compose.prod.yml up -d`.
- **E-Reader non vede i nuovi numeri**:
  Verifica nella WebUI che la testata sia "Attiva" e che il reader punti all'indirizzo IP corretto di Tower (`http://your-server-ip:8400/opds`).
