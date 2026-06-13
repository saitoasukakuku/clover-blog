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
