"""
WSGI entry point voor PythonAnywhere.

Kopieer de inhoud van dit bestand naar het WSGI-configuratiebestand
op PythonAnywhere (te vinden via Web tab > WSGI configuration file).
Pas de paden aan naar je eigen gebruikersnaam.
"""
import sys
import os

# ── Pas dit pad aan naar je PythonAnywhere project directory ──
project_home = '/home/JOUW_USERNAME/VD-Vleuten-Inkloksysteem'

if project_home not in sys.path:
    sys.path.insert(0, project_home)

# Zorg dat .env wordt geladen
os.chdir(project_home)

from app import app as application
