from django.db.models import Q

from blog.models import FriendRequest, PrivateMessage
from blog.site_owner import get_site_owner_profile


def footer_social(request):
    owner, profile = get_site_owner_profile()
    can_configure_social = (
        request.user.is_authenticated
        and owner is not None
        and request.user.pk == owner.pk
    )

    return {
        'footer_profile': profile,
        'footer_email': owner.email if owner else '',
        'footer_can_configure_social': can_configure_social,
    }


def social_counts(request):
    if not request.user.is_authenticated:
        return {
            'pending_friend_request_count': 0,
            'unread_private_message_count': 0,
        }

    return {
        'pending_friend_request_count': FriendRequest.objects.filter(
            receiver=request.user,
            status='pending',
        ).count(),
        'unread_private_message_count': PrivateMessage.objects.filter(
            recipient=request.user,
            is_read=False,
        ).filter(
            Q(sender__friendships_as_high__user_low=request.user)
            | Q(sender__friendships_as_low__user_high=request.user)
        ).distinct().count(),
    }
