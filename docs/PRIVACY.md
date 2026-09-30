# Policy di Privacy — CrossPoint Newsletter Server

## 1. Principio guida

Il Newsletter Server gestisce **esclusivamente** newsletter per cui l'utente ha esplicito diritto di ricezione. Non raccoglie, analizza o distribuisce dati personali di terzi.

---

## 2. Dati conservati

### 2.1 Email originali (raw)

- **Default: conservate** (`CN_KEEP_RAW=true`).
- Salvate come file `.eml` nella cartella `data/raw/<newsletter-slug>/`.
- Utili per debugging, ri-processamento e audit.
- Disattivabile da configurazione: se `CN_KEEP_RAW=false`, le email vengono eliminate dopo la conversione.

### 2.2 EPUB generati

- File EPUB archiviati in `data/epub/<newsletter-slug>/`.
- Conservati secondo la retention policy della newsletter.

### 2.3 Metadati

- Titolo (subject email), nome newsletter, data ricezione, stato elaborazione.
- **Nessun dato personale di terzi** viene estratto o indicizzato dal corpo delle email.
- I metadati servono esclusivamente per catalogazione e distribuzione OPDS.

### 2.4 Copertine

- Immagini PNG generate programmaticamente (nome newsletter, titolo, data).
- Non contengono dati personali.

---

## 3. Retention policy

### 3.1 Default: illimitata

Per default la retention è **illimitata** — tutti gli EPUB e le email raw vengono conservati a tempo indeterminato.

### 3.2 Override per newsletter

Ogni newsletter può avere una retention personalizzata:

```yaml
# Esempio in newsletters.yaml
newsletters:
  - name: "The Bull"
    sender: "newsletter@thebull.example.com"
    retention:
      max_issues: null     # null = illimitato (default)
      max_age_days: null   # null = illimitato (default)
```

Quando configurata, la retention elimina automaticamente:
- Le issue più vecchie oltre `max_issues` (es. conserva solo le ultime 100).
- Le issue più vecchie di `max_age_days` (es. conserva solo gli ultimi 365 giorni).

La retention viene applicata via **comando manuale** (`cn retention run`) o **scheduled job**.

### 3.3 Cosa viene eliminato dalla retention

- Record `Issue` dal database.
- File EPUB dalla cartella `data/epub/`.
- Email raw dalla cartella `data/raw/` (se conservate).
- Copertina dalla cartella `data/covers/`.

---

## 4. Diritto alla cancellazione

### 4.1 Cancellazione newsletter completa

Comando per eliminare una newsletter e **tutti** i dati associati:

```bash
cn newsletter delete "The Bull" --confirm
```

Vengono rimossi:
- Tutti i record `Issue` della serie dal database.
- Tutti i file EPUB della serie.
- Tutte le email raw della serie.
- Tutte le copertine della serie.
- Il record `Newsletter` stesso.
- L'entry corrispondente dal catalogo Calibre-Web (se sincronizzata).

L'operazione è **irreversibile** (salvo backup preesistente).

### 4.2 Cancellazione singola issue

```bash
cn issue delete <issue-id> --confirm
```

Rimuove: record DB, file EPUB, email raw e copertina di quella specifica issue.

---

## 5. Minimizzazione

- **Nessun tracking**: il server non traccia comportamenti di lettura, aperture o click.
- **Nessuna analisi**: non vengono estratti contatti, link affiliati, pixel di tracciamento o dati comportamentali dal corpo delle email.
- **Nessuna indicizzazione di dati personali**: il full-text search (se attivato) opera solo su titolo e nome newsletter, non sul contenuto delle email.
- **Nessun dato inviato a terzi**: il server opera esclusivamente in rete locale (LAN).

---

## 6. Gestione segreti

| Segreto | Dove risiede | Mai presente in |
|:---|:---|:---|
| Credenziali IMAP (host, user, password) | `.env` locale | Repository Git |
| Credenziali OPDS (username, password) | `.env` locale | Repository Git |
| Token/credenziali Calibre-Web | `.env` locale | Repository Git |
| Credenziali Wi-Fi del reader | microSD del dispositivo | Repository Git, server |

### Separazione segreti

- I segreti del server (IMAP, OPDS, Calibre) sono **completamente separati** dai segreti del dispositivo (Wi-Fi, credenziali OPDS lato reader).
- Il file `.env` è in `.gitignore` e non viene mai committato.
- Il file `.env.example` contiene solo chiavi con valori fittizi.

---

## 7. Backup e ripristino

- I backup includono: database SQLite (`data/db/newsletter.db`) e file EPUB (`data/epub/`).
- Le email raw sono incluse nel backup solo se `CN_KEEP_RAW=true`.
- Procedura di backup documentata nello Step N-09 (Operations).
- Il ripristino da backup ricostruisce lo stato completo del server.

---

## 8. Visibilità del repository

- Il repository è disponibile su [GitHub](https://github.com/tisanina/crosspoint-newsletter).
- Il codice sorgente è pubblico. Nessun segreto o dato personale è presente nel repository.
