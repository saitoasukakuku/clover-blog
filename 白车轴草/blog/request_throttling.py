import ipaddress
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from blog.models import RateLimitState


def normalize_ip_address(raw_address):
    try:
        return str(ipaddress.ip_address((raw_address or '').strip()))
    except ValueError:
        return 'unknown'


def get_client_ip(request):
    remote_address = normalize_ip_address(request.META.get('REMOTE_ADDR'))
    if remote_address not in settings.TRUSTED_PROXY_IPS:
        return remote_address

    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if not forwarded_for:
        return remote_address

    # Nginx appends the direct client to the right side of this chain.
    forwarded_address = forwarded_for.rsplit(',', 1)[-1]
    return normalize_ip_address(forwarded_address)


def get_client_identity(request):
    request_user = getattr(request, 'user', None)
    if request_user is not None and request_user.is_authenticated:
        return f'user:{request_user.pk}'
    return f'ip:{get_client_ip(request)}'


def hash_rate_limit_identity(action, identity):
    return salted_hmac(
        f'blog.rate-limit.{action}',
        identity,
        algorithm='sha256',
    ).hexdigest()


def consume_rate_limit(request, action, *, limit, window_seconds, block_seconds):
    identity = get_client_identity(request)
    key_hash = hash_rate_limit_identity(action, identity)
    now = timezone.now()
    window_duration = timedelta(seconds=window_seconds)

    for attempt_index in range(2):
        try:
            with transaction.atomic():
                rate_state, _ = RateLimitState.objects.select_for_update().get_or_create(
                    action=action,
                    key_hash=key_hash,
                    defaults={
                        'window_started_at': now,
                        'request_count': 0,
                    },
                )
                if rate_state.blocked_until and rate_state.blocked_until > now:
                    return max(1, int((rate_state.blocked_until - now).total_seconds()))

                if now - rate_state.window_started_at >= window_duration:
                    rate_state.window_started_at = now
                    rate_state.request_count = 0
                    rate_state.blocked_until = None

                rate_state.request_count += 1
                if rate_state.request_count > limit:
                    rate_state.blocked_until = now + timedelta(seconds=block_seconds)
                    retry_after = block_seconds
                else:
                    retry_after = 0
                rate_state.save(
                    update_fields=[
                        'window_started_at',
                        'request_count',
                        'blocked_until',
                        'updated_at',
                    ]
                )
                return retry_after
        except IntegrityError:
            if attempt_index:
                raise
    return block_seconds
