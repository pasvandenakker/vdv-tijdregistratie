#!/bin/bash
# ══════════════════════════════════════════════════════════════
# VD Vleuten Inkloksysteem — PythonAnywhere Deploy Script
# ══════════════════════════════════════════════════════════════
# Gebruik: bash ~/VD-Vleuten-Inkloksysteem/deploy.sh
#
# Dit script:
#  1. Haalt de laatste code op van GitHub
#  2. Installeert nieuwe dependencies
#  3. Draait database migraties
#  4. Herlaadt de web app
# ══════════════════════════════════════════════════════════════

set -e

# ── Configuratie (pas aan naar jouw situatie) ────────────────
USERNAME="pasvandenakker"
PROJECT_DIR="/home/$USERNAME/vdv-tijdregistratie"
VENV_DIR="$PROJECT_DIR/venv"
WSGI_FILE="/var/www/${USERNAME}_pythonanywhere_com_wsgi.py"

# ── Kleuren ──────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}═══ VD Vleuten Inkloksysteem Deploy ═══${NC}"
echo ""

# ── 1. Ga naar project directory ─────────────────────────────
cd "$PROJECT_DIR"
echo -e "${YELLOW}▸ Project directory: $PROJECT_DIR${NC}"

# ── 2. Pull laatste code ─────────────────────────────────────
echo -e "${YELLOW}▸ Code ophalen van GitHub...${NC}"
git pull origin main
echo ""

# ── 3. Activeer virtualenv en installeer dependencies ────────
echo -e "${YELLOW}▸ Dependencies installeren...${NC}"
source "$VENV_DIR/bin/activate"
pip install -q -r requirements.txt
pip install -q openpyxl Pillow
echo ""

# ── 4. Database migraties ────────────────────────────────────
echo -e "${YELLOW}▸ Database migraties draaien...${NC}"
python init_db.py
echo ""

# ── 5. Herlaad de web app ────────────────────────────────────
echo -e "${YELLOW}▸ Web app herladen...${NC}"
touch "$WSGI_FILE"
echo ""

echo -e "${GREEN}═══ Deploy voltooid! ═══${NC}"
echo "App is bereikbaar op: https://${USERNAME}.pythonanywhere.com"
