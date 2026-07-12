from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, build_opener


DEFAULT_TEXT_RESPONSE_LIMIT = 4 * 1024 * 1024
ERROR_RESPONSE_LIMIT = 64 * 1024


def validate_restricted_https_url(url, allowed_hosts):
    parsed_url = urlparse(url)
    normalized_allowed_hosts = {
        allowed_host.casefold()
        for allowed_host in allowed_hosts
    }
    if (
        parsed_url.scheme != 'https'
        or not parsed_url.hostname
        or parsed_url.hostname.casefold() not in normalized_allowed_hosts
    ):
        raise ValueError('远程地址不在允许范围内。')
    return url


class RestrictedRedirectHandler(HTTPRedirectHandler):
    def __init__(self, allowed_hosts):
        super().__init__()
        self.allowed_hosts = frozenset(allowed_hosts)

    def redirect_request(self, request, response, code, message, headers, new_url):
        absolute_url = urljoin(request.full_url, new_url)
        validate_restricted_https_url(absolute_url, self.allowed_hosts)
        return super().redirect_request(
            request,
            response,
            code,
            message,
            headers,
            absolute_url,
        )


def open_restricted_https_url(request, allowed_hosts, timeout):
    validate_restricted_https_url(request.full_url, allowed_hosts)
    opener = build_opener(RestrictedRedirectHandler(allowed_hosts))
    return opener.open(request, timeout=timeout)


def read_limited_text_response(
    response,
    maximum_bytes=DEFAULT_TEXT_RESPONSE_LIMIT,
    *,
    errors='strict',
):
    response_headers = getattr(response, 'headers', None) or {}
    content_length = response_headers.get('Content-Length')
    if content_length:
        try:
            declared_length = int(content_length)
        except (TypeError, ValueError):
            declared_length = None
        if declared_length is not None and declared_length > maximum_bytes:
            raise ValueError('远程文本响应超过允许大小。')

    response_bytes = response.read(maximum_bytes + 1)
    if len(response_bytes) > maximum_bytes:
        raise ValueError('远程文本响应超过允许大小。')
    return response_bytes.decode('utf-8', errors=errors)


def read_limited_error_response(response):
    try:
        return read_limited_text_response(
            response,
            ERROR_RESPONSE_LIMIT,
            errors='replace',
        )
    except ValueError:
        return '[response body omitted because it exceeded 64KB]'
