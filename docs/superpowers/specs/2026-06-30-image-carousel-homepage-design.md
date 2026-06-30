# Image carousel homepage design

## Context

The current root URL `/` redirects to `/index/`, and `/index/` is the article
list page. The visual direction approved during brainstorming is to make `/` a
real welcome homepage with an immersive image carousel, while keeping `/index/`
as the full article browsing and filtering page.

The homepage should use images from:

```text
白车轴草/media/index_img/
```

This directory is under `media/`, so it is ignored by Git. The implementation
must work when the folder exists locally or on production, and must degrade
cleanly when it is missing or empty.

## Goals

- Change `/` from a redirect into a real homepage view.
- Keep `/index/` as the existing article list page and preserve its current
  filtering behavior.
- Use images from `MEDIA_ROOT/index_img` as the homepage background carousel.
- Let the homepage theme change with the current carousel image through matching
  color, label, and copy presets.
- Add a first-screen welcome experience with clear entrances to reading,
  archive, tags, and author/profile content.
- Add a below-the-fold section with recent readable posts and exploration
  shortcuts, without duplicating the full `/index/` article list.
- Keep behavior safe for anonymous visitors and logged-in users.

## Non-goals

- Do not move or commit files from `media/index_img`.
- Do not require an image upload UI for homepage backgrounds in this iteration.
- Do not replace `/index/` or remove current article filters.
- Do not add a database model for homepage images yet.
- Do not depend on external image or JavaScript CDNs for the homepage carousel.

## Route and navigation behavior

`/` should map to a new homepage view, named `home`.

`/index/` should continue mapping to the existing `index` view, named `index`.

The top-left brand link should point to `/`, because it is the site entrance.
The navigation should include a clear reading entry such as `阅读` or `进入博客`
that points to `/index/`.

Existing links that intentionally return users to the article list can continue
to use `index`. This avoids changing form redirects, post actions, and current
tests unless a page label is clearly about the site homepage rather than article
browsing.

## Homepage content

The first screen should contain:

- site brand: `白车轴草`;
- large headline that introduces the site as a personal reading space;
- short supporting text;
- primary action to `/index/`;
- secondary action to author/profile information when available;
- optional action to a random or latest readable post;
- mood labels that update with the active carousel slide;
- a glass-style feature card that previews the current image and explains the
  current mood.

The next section should contain:

- recent readable posts, limited to a small number such as 3;
- exploration shortcuts for archive, tags, categories, and reading;
- a short explanation that `/index/` remains the full article list.

## Image discovery

The homepage view should look for image files in `MEDIA_ROOT/index_img`.

Allowed extensions:

- `.jpg`
- `.jpeg`
- `.png`
- `.webp`

The view should sort file names for stable output and cap the number of carousel
items in the first version, for example the first 12 usable images. This avoids
rendering a large payload if the folder contains many files.

Each image passed to the template should include:

- media URL;
- file name;
- theme preset index;
- label text;
- headline text;
- supporting text;
- mood tags.

If the folder is missing, empty, or unreadable, the homepage should render with a
CSS gradient fallback and no broken images.

## Theme changes

The first implementation should use a small set of theme presets instead of
automatic color extraction. The carousel can assign presets by image order or by
simple file grouping.

Example presets:

- clear blue for lake and sky images;
- warm brown for cabin and autumn images;
- red-orange for sunset images;
- quiet gray-blue for snow images.

When the active slide changes, JavaScript should update CSS custom properties,
headline copy, mood chips, and side-card text from the slide data. This achieves
the approved “随图片变化” behavior without adding heavy image analysis logic.

Automatic color extraction can be revisited later if needed, but it is not
required for this iteration.

## Visibility rules

Recent posts on the homepage must use the same readable published-post rule as
the article list:

- anonymous visitors can see `published + public` posts;
- logged-in users can see public published posts and their own published posts.

Drafts must not appear on the homepage recent-post section.

## Implementation notes

Add a small helper in `blog.views` to build homepage carousel slide data from
`settings.MEDIA_ROOT / index_img`. Keep the helper local to `views.py` unless it
starts to grow.

Reuse the existing `get_readable_published_posts` helper for recent posts.

Create a new `home.html` template that extends `base.html`. Keep page-specific
CSS and JavaScript inside template blocks, matching the existing project style.

The carousel JavaScript should be defensive:

- do nothing if there are no slides;
- support manual dot navigation;
- auto-advance every few seconds;
- avoid horizontal overflow on desktop and mobile;
- keep text readable with a consistent overlay.

## Testing

Add focused tests for:

- `/` returns `200 OK`;
- `/` uses the new homepage template;
- `/index/` still returns the article list page;
- anonymous homepage recent posts exclude private posts and drafts;
- logged-in homepage recent posts include the user's own published private posts;
- homepage works when `media/index_img` is missing or empty;
- homepage slide data includes only allowed image extensions;
- the brand link points to `home` and the reading link points to `index`.

Run after implementation:

```powershell
python .\白车轴草\manage.py check
python .\白车轴草\manage.py test blog
```
