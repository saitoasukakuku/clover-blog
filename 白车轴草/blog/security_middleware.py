import secrets

from django.conf import settings
from django.utils.cache import patch_cache_control


SENSITIVE_PATH_PREFIXES = (
    '/admin/',
    '/dashboard/',
    '/drafts/',
    '/edit_post/',
    '/favorites/',
    '/index/create_post/',
    '/login/',
    '/media-manager/',
    '/messages/',
    '/notifications/',
    '/register/',
    '/registration-requests/',
    '/user_center/',
)

FORWARDED_HEADERS = (
    'HTTP_FORWARDED',
    'HTTP_X_FORWARDED_FOR',
    'HTTP_X_FORWARDED_HOST',
    'HTTP_X_FORWARDED_PORT',
    'HTTP_X_FORWARDED_PROTO',
    'HTTP_X_REAL_IP',
)


class TrustedProxyHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        remote_address = (request.META.get('REMOTE_ADDR') or '').strip()
        if remote_address not in settings.TRUSTED_PROXY_IPS:
            for header_name in FORWARDED_HEADERS:
                request.META.pop(header_name, None)
        return self.get_response(request)


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.csp_nonce = secrets.token_urlsafe(18)
        response = self.get_response(request)

        script_sources = ["'self'", f"'nonce-{request.csp_nonce}'", 'https://cdnjs.cloudflare.com']
        directives = [
            "default-src 'self'",
            "base-uri 'self'",
            "connect-src 'self'",
            "font-src 'self' data:",
            "form-action 'self'",
            "frame-ancestors 'none'",
            "img-src 'self' data: blob: https:",
            "manifest-src 'self'",
            "media-src 'self'",
            "object-src 'none'",
            f"script-src {' '.join(script_sources)}",
            "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com",
            "worker-src 'self'",
        ]
        if not settings.DEBUG:
            directives.append('upgrade-insecure-requests')
        response['Content-Security-Policy'] = '; '.join(directives)
        response['Permissions-Policy'] = (
            'camera=(), geolocation=(), microphone=(), payment=(), usb=()'
        )

        is_sensitive_path = request.path.startswith(SENSITIVE_PATH_PREFIXES)
        if request.user.is_authenticated or is_sensitive_path:
            patch_cache_control(response, private=True, no_store=True, max_age=0)
        return response
