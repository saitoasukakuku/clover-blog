# Archive and tags page design

## Context

The blog homepage already supports filtering published posts by keyword, date,
category, and author. It also shows recent posts and category counts. After
enabling the daily AI post timer, the number of published posts will grow
steadily, so the site needs two lightweight browsing pages:

- an archive page for browsing posts by month;
- a tag page for browsing tags and finding posts with a selected tag.

The current `Post.tags` field is a comma-separated string, not a normalized tag
model. This design keeps that storage model and avoids a database migration.

## Goals

- Add an `/archive/` page grouped by year and month.
- Add a `/tags/` page listing tags with post counts.
- Add navigation links for both pages in the existing top navigation.
- Reuse the existing homepage search flow when a visitor clicks a tag.
- Preserve current visibility rules for visitors and logged-in users.
- Avoid changing the `Post` data model.

## Non-goals

- Do not introduce a `Tag` model.
- Do not migrate existing comma-separated tag strings into a new table.
- Do not redesign the homepage.
- Do not change article creation or editing behavior.

## Visibility rules

Archive and tag data must use the same readable published-post scope as the
homepage:

- anonymous visitors can see `published + public` posts;
- logged-in users can see `published + public` posts and their own published
  posts.

Drafts must not appear on the archive page or tag page.

## Routes

Add two URL patterns:

- `/archive/` mapped to `views.archive_view`, named `archive`;
- `/tags/` mapped to `views.tags_view`, named `tags`.

## Archive page behavior

The archive page groups readable published posts by `(year, month)`, sorted from
newest month to oldest month. Inside each month, posts are sorted by
`created_at` descending.

Each post row should show:

- title linked to the article detail page;
- publication date;
- category label;
- author display name;
- optional view count.

The page should show a simple empty state if there are no readable published
posts.

## Tags page behavior

The tags page derives tags by splitting each readable post's `tags` string on
commas. It trims whitespace, ignores empty tag values, and counts each tag once
per post. Tags are sorted by descending count, then by tag text for stable
display.

Each tag item should show:

- the tag text;
- the number of readable posts containing that tag;
- a link to the homepage with `?q=<tag>`, reusing the existing keyword search.

This is intentionally approximate because the current storage is plain text.
For example, a homepage search for a tag also searches title, content, category,
and author metadata. That is acceptable for this lightweight feature.

## Navigation

Add `归档` and `标签` links to the existing base navigation. The active state is
optional; the first version only needs reliable links.

## Implementation notes

Create a shared helper in `blog.views` for readable published posts so `index`,
`archive_view`, and `tags_view` can use the same visibility rule. Keep the helper
small and local to `views.py` to avoid over-structuring this feature.

Archive grouping can be built in Python from the filtered queryset. The expected
post count is small, and this avoids backend-specific SQL date truncation
behavior between SQLite and MySQL.

Tag counting can also be built in Python from the filtered queryset. The current
`tags` field is capped at 200 characters, so the processing cost is low.

## Testing

Add regression tests that verify:

- anonymous archive view only includes public published posts;
- logged-in archive view includes the user's own published private posts;
- drafts are excluded from archive data;
- tags view splits comma-separated tags, trims whitespace, ignores empty tags,
  and counts each tag once per post;
- tag links point to the homepage search query;
- `/archive/` and `/tags/` return `200 OK`.

Run the existing blog test suite after implementation.
