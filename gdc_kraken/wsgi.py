"""
WSGI config for gdc_kraken project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import json
import os
import sys
import site
from django.core.wsgi import get_wsgi_application
from pathlib import Path


# Get paths from config files
config_file_path = os.path.join(Path(__file__).resolve().parent.parent, "config.json")
if not os.path.exists(config_file_path):
    raise Exception(f"Missing config.json ({config_file_path})")
with open(config_file_path, 'r') as file:
    config_data = json.load(file)

wsgi_config = config_data.get("WSGI") or {}

if config_data["PLATFORM"] == "PROD":
    # Add python site packages (legacy Apache/virtualenv hosting)
    site_packages = wsgi_config.get("PATH_SITE_PACKAGES")
    if site_packages:
        site.addsitedir(site_packages)

# Add the app's directory to the PYTHONPATH when provided (legacy hosting)
path_kraken = wsgi_config.get("PATH_GDC_KRAKEN")
path_storm = wsgi_config.get("PATH_GDC_STORM")
if path_kraken:
    sys.path.append(path_kraken)
if path_storm:
    sys.path.append(path_storm)

os.environ['DJANGO_SETTINGS_MODULE'] = 'gdc_kraken.settings'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gdc_kraken.settings')

application = get_wsgi_application()
