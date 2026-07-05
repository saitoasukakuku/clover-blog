import re
from html import unescape
from urllib.parse import urlparse

from django import template
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe


register = template.Library()


@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)


@register.filter(name='post_content')
def post_content(value):
    escaped_content = str(conditional_escape(value or '')).replace('\r\n', '\n').replace('\r', '\n')
    rendered_blocks = render_post_markdown_lines(escaped_content.split('\n'))
    return mark_safe('\n\n'.join(rendered_blocks))


def render_post_markdown_lines(escaped_lines):
    rendered_blocks = []
    pending_paragraph_lines = []

    def flush_pending_paragraph():
        if not pending_paragraph_lines:
            return
        paragraph_text = '\n'.join(pending_paragraph_lines).strip('\n')
        if paragraph_text.strip():
            rendered_inline_paragraph = render_post_inline_markdown(paragraph_text)
            rendered_blocks.append(f'<p>{rendered_inline_paragraph.replace(chr(10), "<br>")}</p>')
        pending_paragraph_lines.clear()

    for escaped_line in escaped_lines:
        heading_match = re.match(r'^(#{1,3})\s+(.+)$', escaped_line.strip())
        if heading_match:
            flush_pending_paragraph()
            heading_level = len(heading_match.group(1)) + 1
            heading_text = render_post_inline_markdown(heading_match.group(2).strip())
            rendered_blocks.append(f'<h{heading_level}>{heading_text}</h{heading_level}>')
            continue

        if not escaped_line.strip():
            flush_pending_paragraph()
            continue

        pending_paragraph_lines.append(escaped_line)

    flush_pending_paragraph()
    return rendered_blocks


def render_post_inline_markdown(escaped_text):
    rendered_text = re.sub(
        r'!\[([^\]\n]*)\]\(([^)\s]+)\)',
        render_post_markdown_image,
        escaped_text,
    )
    rendered_text = re.sub(
        r'\[([^\]\n]+)\]\(([^)\s]+)\)',
        render_post_markdown_link,
        rendered_text,
    )
    return re.sub(
        r'\*\*([^\n]+?)\*\*',
        r'<strong>\1</strong>',
        rendered_text,
    )


def render_post_markdown_link(match):
    link_text = match.group(1)
    raw_url = unescape(match.group(2))
    if not is_safe_post_link_url(raw_url):
        return match.group(0)
    return f'<a href="{conditional_escape(raw_url)}">{link_text}</a>'


def render_post_markdown_image(match):
    image_alt = match.group(1)
    raw_url = unescape(match.group(2))
    if not is_safe_post_image_url(raw_url):
        return match.group(0)
    return f'<img src="{conditional_escape(raw_url)}" alt="{image_alt}" loading="lazy">'


def is_safe_post_link_url(raw_url):
    parsed_url = urlparse(raw_url)
    if parsed_url.scheme in {'http', 'https', 'mailto'}:
        return True
    return not parsed_url.scheme and raw_url.startswith('/') and not raw_url.startswith('//')


def is_safe_post_image_url(raw_url):
    parsed_url = urlparse(raw_url)
    if parsed_url.scheme in {'http', 'https'}:
        return True
    return not parsed_url.scheme and raw_url.startswith('/media/') and not raw_url.startswith('//')
