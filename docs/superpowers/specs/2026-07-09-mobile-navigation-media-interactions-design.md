# Mobile Navigation, Media, and Interaction Design

## Goals

- Replace low-frequency archive and tag shortcuts with account and writing actions.
- Return users to the immersive homepage after logout.
- Make music uploads understandable and usable on mobile.
- Let administrators edit a music track by selecting its resource row.
- Make favorite and like actions update in place with immediate animation.
- Replace full-width Django alerts with compact, timed toasts.
- Preserve the unread notification badge in desktop and mobile navigation.

## Navigation

The homepage hero uses account-aware actions. Guests see login and article browsing.
Authenticated users see create-post and favorites actions. Desktop shortcuts use reading,
writing, favorites, and notifications; guests instead see login and registration.

The mobile bar contains five high-frequency authenticated actions: home, reading, writing,
favorites, and notifications. Guests see home, reading, login, and registration. The
notification action keeps its unread count badge. Logout redirects to the homepage.

## Article Interactions

Favorite and like endpoints keep their existing POST-and-redirect behavior as a no-JavaScript
fallback. Requests marked as XMLHttpRequest receive JSON containing the active state, updated
label, count where relevant, and a short message. The article page intercepts only these two
forms, applies a click animation immediately, submits with `fetch`, updates the button without
replacing the page, and shows a toast.

## Toasts

Django messages are rendered into a hidden queue instead of visible Bootstrap alerts. A global
toast region displays messages at the lower-right edge. Each toast has a close button and a
progress bar that reaches zero before automatic dismissal. On mobile, the region stays above
the bottom navigation.

## Media Manager

The music tab persists after music operations. The upload form shows selected file information,
upload progress, and a busy state. XMLHttpRequest is used for progress reporting, while the
normal multipart submission remains the fallback.

Music resources are presented as selectable rows on desktop and compact cards on mobile.
Selecting one opens an edit modal. The administrator can rename the track stem, replace or
remove the same-name cover, upload or directly edit lyrics, and remove lyrics. All source names
are matched against the current media inventory; destination names are sanitized and checked
for collisions before any move.

## Verification

Automated tests cover navigation labels, logout destination, JSON interaction responses, toast
markup, music-tab persistence, mobile upload hooks, and safe music-resource updates. Browser
verification covers 599px mobile and desktop layouts, modal opening, responsive navigation,
and in-place article interaction behavior.
