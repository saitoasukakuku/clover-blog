"""
WSGI config for 白车轴草 project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/wsgi/
"""

import os

from sqlite_compat import patch_sqlite

patch_sqlite()

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', '白车轴草.settings')

application = get_wsgi_application()
