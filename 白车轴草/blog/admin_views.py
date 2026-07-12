from django.contrib import admin
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from blog.request_throttling import consume_rate_limit


@sensitive_post_parameters()
@csrf_protect
@require_http_methods(['GET', 'POST'])
def rate_limited_admin_login(request):
    if request.method == 'POST':
        retry_after = consume_rate_limit(
            request,
            'admin-login',
            limit=20,
            window_seconds=900,
            block_seconds=900,
        )
        if retry_after:
            response = HttpResponse(
                '管理员登录尝试过多，请稍后重试。',
                status=429,
                content_type='text/plain; charset=utf-8',
            )
            response['Retry-After'] = str(retry_after)
            return response
    return admin.site.login(request)
