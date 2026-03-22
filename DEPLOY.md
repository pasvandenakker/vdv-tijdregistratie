# Deployment op PythonAnywhere

## Eerste keer opzetten

### 1. GitHub repository aanmaken

Maak een repository aan op GitHub (bijv. `VD-Vleuten-Inkloksysteem`).

Push je code lokaal:
```bash
cd "G:\Mijn Drive\.Zelfstandige onderneming\VD Vleuten - Inkloksysteem"
git init
git add -A
git commit -m "Initial commit"
git remote add origin https://github.com/JOUW_ACCOUNT/VD-Vleuten-Inkloksysteem.git
git push -u origin main
```

### 2. Op PythonAnywhere: repo clonen

Open een **Bash console** op PythonAnywhere en voer uit:
```bash
git clone https://github.com/JOUW_ACCOUNT/VD-Vleuten-Inkloksysteem.git
cd VD-Vleuten-Inkloksysteem
```

### 3. Virtualenv aanmaken

```bash
python3.10 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install openpyxl Pillow
```

### 4. Database initialiseren

```bash
python init_db.py
```

### 5. .env bestand aanmaken

```bash
echo "SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')" > .env
```

### 6. Web app configureren (Web tab)

Ga naar de **Web** tab op PythonAnywhere:

1. Klik **Add a new web app**
2. Kies **Manual configuration** (niet Flask!)
3. Kies Python 3.10

Stel in:
- **Source code**: `/home/JOUW_USERNAME/VD-Vleuten-Inkloksysteem`
- **Virtualenv**: `/home/JOUW_USERNAME/VD-Vleuten-Inkloksysteem/venv`
- **Static files**: URL `/static/` → Directory `/home/JOUW_USERNAME/VD-Vleuten-Inkloksysteem/static`

### 7. WSGI configureren

Klik op het **WSGI configuration file** linkje op de Web tab.
Vervang de inhoud door:

```python
import sys
import os

project_home = '/home/JOUW_USERNAME/VD-Vleuten-Inkloksysteem'
if project_home not in sys.path:
    sys.path.insert(0, project_home)

os.chdir(project_home)

from app import app as application
```

### 8. Reload en test

Klik **Reload** op de Web tab. Je app is live op:
```
https://JOUW_USERNAME.pythonanywhere.com
```

---

## Updates deployen

Na het pushen van wijzigingen naar GitHub:

### Optie A: Deploy script (aanbevolen)

Open een Bash console op PythonAnywhere:
```bash
bash ~/VD-Vleuten-Inkloksysteem/deploy.sh
```

### Optie B: Handmatig

```bash
cd ~/VD-Vleuten-Inkloksysteem
git pull origin main
source venv/bin/activate
pip install -r requirements.txt
python init_db.py
touch /var/www/JOUW_USERNAME_pythonanywhere_com_wsgi.py
```

---

## Belangrijk

- **Elke 3 maanden** moet je op PythonAnywhere de app verlengen (klik op "Run until 3 months from today" op de Web tab)
- **SQLite database** staat op PythonAnywhere, niet in GitHub. De `.gitignore` sluit `timeclock.db` uit
- **Het .env bestand** staat ook niet in GitHub. Maak dit apart aan op PythonAnywhere
- **Backup**: Download regelmatig een backup via de admin interface (Sidebar > DB Backup)
