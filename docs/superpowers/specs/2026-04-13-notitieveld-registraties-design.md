# Notitieveld bij tijdregistraties — Design

**Datum:** 2026-04-13
**Project:** VD Vleuten Inkloksysteem
**Status:** Goedgekeurd

## Doel

Admins kunnen bij elke tijdregistratie een vrij notitieveld toevoegen en bewerken. De notitie is zichtbaar in het bewerk-scherm, de logs-lijst en in alle exports (CSV + Excel). Het bestaande `reason`-veld (automatisch gevuld bij te laat inklokken) blijft onaangeroerd naast dit nieuwe veld.

## Scope

### In scope
- Nieuwe kolom `note` in `time_entries`
- Notitieveld in bewerk- en toevoeg-scherm
- Notitie tonen in logs-tabel
- Notitie in CSV-export
- Notitie als aparte kolom in Excel-export
- Audit-log registreert notitie-wijzigingen
- Bestaande registraties kunnen achteraf een notitie krijgen via bewerk-scherm

### Buiten scope
- Kiosk toont/bewerkt geen notities (admin-only)
- Geen rich text, geen bijlagen
- Geen aparte notitie-geschiedenis (audit-log volstaat)
- Geen notities op employees, schedules of settings

## Database

**Migratie** in `init_db.py` (veilig, idempotent):

```python
try: c.execute("ALTER TABLE time_entries ADD COLUMN note TEXT")
except: pass
```

Toegevoegd naast de bestaande `reason`-migratie. Geen default-waarde; `NULL` = geen notitie.

## Backend (`app.py`)

### Uitgebreide SELECTs
Alle queries die `time_entries` uitlezen voor weergave/export moeten `note` meenemen:
- `get_entry_by_id` (regel ~215)
- `logs` (regel ~877)
- `export_csv` (regel ~1154)
- Excel-export query in `export_excel`

### `edit_entry` (regel ~1002)
- Lees `note = request.form.get("note", "").strip() or None` (max 500 tekens, afgekapt).
- `UPDATE time_entries SET action=?, timestamp=?, note=? WHERE id=?`
- Audit-log `new_val` wordt `"{action} {timestamp} | note: {note or '-'}"` zodat notitiewijzigingen terugkomen.
- Bij GET: huidige `note` meegeven aan template.

### `add_entry` (regel ~1038)
- Zelfde `note`-veld uit form.
- Opgenomen in `INSERT`.

### Validatie
- Max lengte: 500 tekens, server-side afkapping (`note[:500]`).
- Lege string → `NULL`.
- Geen HTML-sanitatie nodig: Jinja auto-escape rendert veilig in templates; CSV/Excel zijn geen render-contexten.

## Templates

### `edit_entry.html` + `add_entry.html`
Nieuw form-group onder het tijdstip-veld:

```html
<div class="form-group">
  <label class="form-label" for="note">Notitie (optioneel)</label>
  <textarea name="note" id="note" class="form-input" rows="3" maxlength="500"
            placeholder="Bijv. 'Ziek naar huis', 'Extra uren op verzoek klant'">{{ entry['note'] or '' }}</textarea>
</div>
```

### `logs.html`
- Nieuwe `<th>Notitie</th>` tussen **Reden** en actieknoppen.
- Nieuwe `<td>` met truncated-styling (zelfde als reden-kolom).
- `colspan` van de lege-state-rij: 7 → 8.

## Exports

### CSV (`export_csv`, regel ~1168)
- Header: voeg `"Notitie"` toe aan einde.
- Row: voeg `row["note"] or ""` toe.

### Excel (`excel_export.py`)
- `DAY_HEADERS` (regel 374) wijzigen:
  `["Datum", "Dag", "Inklok", "Uitklok", "Gewerkt", "Pauze", "Netto (dec)", "Reden", "Notitie"]`
- Per dag-rij: `first_reason` blijft zoals hij is; extra kolom haalt notitie op uit de eerste `in`-registratie van die dag (`first_in_row["note"]`).
- Kolombreedtes en styling uitbreiden naar de nieuwe laatste kolom.

## Audit-log

`edit_entry` bouwt `old_val` en `new_val` zo op dat notitie-wijzigingen leesbaar zijn:

```python
old_val = f"{entry['action']} {entry['timestamp']} | note: {entry['note'] or '-'}"
new_val = f"{action} {timestamp} | note: {note or '-'}"
```

## Testen (handmatig)

1. Migratie op bestaande DB draaien → `ALTER TABLE` mag niet crashen als kolom al bestaat.
2. Nieuwe registratie toevoegen met notitie → verschijnt in logs en exports.
3. Bestaande registratie bewerken, notitie toevoegen → opgeslagen, audit-log bevat wijziging.
4. Lege notitie → `NULL` in DB, lege cel in export.
5. Notitie met 600 tekens → afgekapt op 500.
6. CSV + Excel export controleren op correcte kolommen.
7. Logs-pagina: `colspan` van lege-state mag niet afbreken.

## Risico's

- **Oude DB zonder migratie:** zonder `init_db.py` draaien na update crasht `SELECT ... note`. Mitigatie: migratie uitvoeren bij deploy.
- **Excel-kolom-shift:** bestaande rapporten veranderen van indeling. Mitigatie: communicatie naar VD Vleuten bij release.
