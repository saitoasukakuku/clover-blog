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
    heading_id_counts = {}
    toc_items = []
    should_build_toc = count_post_headings(escaped_lines) >= 2

    def flush_pending_paragraph():
        if not pending_paragraph_lines:
            return
        paragraph_text = '\n'.join(pending_paragraph_lines).strip('\n')
        if paragraph_text.strip():
            rendered_inline_paragraph = render_post_inline_markdown(paragraph_text)
            rendered_blocks.append(f'<p>{rendered_inline_paragraph.replace(chr(10), "<br>")}</p>')
        pending_paragraph_lines.clear()

    line_index = 0
    while line_index < len(escaped_lines):
        escaped_line = escaped_lines[line_index]
        code_fence_match = re.match(r'^```([A-Za-z0-9_+#.-]*)\s*$', escaped_line.strip())
        if code_fence_match:
            flush_pending_paragraph()
            code_language = code_fence_match.group(1)
            code_lines = []
            line_index += 1
            while line_index < len(escaped_lines):
                if re.match(r'^```\s*$', escaped_lines[line_index].strip()):
                    break
                code_lines.append(escaped_lines[line_index])
                line_index += 1
            rendered_blocks.append(render_post_code_block(code_lines, code_language))
            if line_index < len(escaped_lines):
                line_index += 1
            continue

        if is_post_table_start(escaped_lines, line_index):
            flush_pending_paragraph()
            table_lines = [escaped_line, escaped_lines[line_index + 1]]
            line_index += 2
            while line_index < len(escaped_lines):
                table_line = escaped_lines[line_index]
                if not table_line.strip() or '|' not in table_line:
                    break
                table_lines.append(table_line)
                line_index += 1
            rendered_blocks.append(render_post_markdown_table(table_lines))
            continue

        heading_match = re.match(r'^(#{1,3})\s+(.+)$', escaped_line.strip())
        if heading_match:
            flush_pending_paragraph()
            heading_level = len(heading_match.group(1)) + 1
            escaped_heading_text = heading_match.group(2).strip()
            heading_text = render_post_inline_markdown(escaped_heading_text)
            if should_build_toc:
                plain_heading_text = get_plain_post_heading_text(escaped_heading_text)
                heading_id = build_post_heading_id(plain_heading_text, heading_id_counts)
                toc_items.append({
                    'id': heading_id,
                    'level': heading_level,
                    'text': plain_heading_text,
                })
                rendered_blocks.append(f'<h{heading_level} id="{heading_id}">{heading_text}</h{heading_level}>')
            else:
                rendered_blocks.append(f'<h{heading_level}>{heading_text}</h{heading_level}>')
            line_index += 1
            continue

        if not escaped_line.strip():
            flush_pending_paragraph()
            line_index += 1
            continue

        pending_paragraph_lines.append(escaped_line)
        line_index += 1

    flush_pending_paragraph()
    if toc_items:
        rendered_blocks.insert(0, build_post_content_toc(toc_items))
    return rendered_blocks


def count_post_headings(escaped_lines):
    return sum(
        1
        for escaped_line in escaped_lines
        if re.match(r'^(#{1,3})\s+(.+)$', escaped_line.strip())
    )


def render_post_code_block(code_lines, raw_language):
    code_content = '\n'.join(code_lines)
    code_language = sanitize_post_code_language(raw_language)
    if code_language:
        return f'<pre><code class="language-{code_language}">{code_content}</code></pre>'
    return f'<pre><code>{code_content}</code></pre>'


def sanitize_post_code_language(raw_language):
    code_language = unescape(raw_language or '').strip().lower()
    return re.sub(r'[^a-z0-9_+#.-]', '', code_language)[:40]


def is_post_table_start(escaped_lines, line_index):
    if line_index + 1 >= len(escaped_lines):
        return False
    header_cells = split_post_table_row(escaped_lines[line_index])
    separator_cells = split_post_table_row(escaped_lines[line_index + 1])
    return (
        len(header_cells) >= 2
        and len(separator_cells) >= 2
        and all(re.match(r'^:?-{3,}:?$', cell.strip()) for cell in separator_cells)
    )


def split_post_table_row(escaped_line):
    table_row = escaped_line.strip()
    if '|' not in table_row:
        return []
    if table_row.startswith('|'):
        table_row = table_row[1:]
    if table_row.endswith('|'):
        table_row = table_row[:-1]
    return [cell.strip() for cell in table_row.split('|')]


def render_post_markdown_table(table_lines):
    header_cells = split_post_table_row(table_lines[0])
    body_rows = [
        split_post_table_row(table_line)
        for table_line in table_lines[2:]
    ]
    header_html = ''.join(
        f'<th>{render_post_inline_markdown(cell)}</th>'
        for cell in header_cells
    )
    body_html = ''.join(
        '<tr>'
        + ''.join(
            f'<td>{render_post_inline_markdown(cell)}</td>'
            for cell in normalize_post_table_row(row_cells, len(header_cells))
        )
        + '</tr>'
        for row_cells in body_rows
        if any(cell.strip() for cell in row_cells)
    )
    return f'<table><thead><tr>{header_html}</tr></thead><tbody>{body_html}</tbody></table>'


def normalize_post_table_row(row_cells, column_count):
    normalized_cells = list(row_cells[:column_count])
    while len(normalized_cells) < column_count:
        normalized_cells.append('')
    return normalized_cells


def get_plain_post_heading_text(escaped_heading_text):
    plain_heading_text = unescape(escaped_heading_text)
    plain_heading_text = re.sub(r'!\[([^\]\n]*)\]\([^)]+\)', r'\1', plain_heading_text)
    plain_heading_text = re.sub(r'\[([^\]\n]+)\]\([^)]+\)', r'\1', plain_heading_text)
    plain_heading_text = re.sub(r'[*_`]+', '', plain_heading_text)
    return plain_heading_text.strip() or '小节'


def build_post_heading_id(plain_heading_text, heading_id_counts):
    heading_slug = re.sub(r'[^\w\u4e00-\u9fff-]+', '-', plain_heading_text.lower()).strip('-')
    heading_slug = heading_slug or 'section'
    heading_id_counts[heading_slug] = heading_id_counts.get(heading_slug, 0) + 1
    if heading_id_counts[heading_slug] == 1:
        return f'post-section-{heading_slug}'
    return f'post-section-{heading_slug}-{heading_id_counts[heading_slug]}'


def build_post_content_toc(toc_items):
    toc_links = ''.join(
        '<li class="toc-level-{level}"><a href="#{id}">{text}</a></li>'.format(
            level=toc_item['level'],
            id=toc_item['id'],
            text=conditional_escape(toc_item['text']),
        )
        for toc_item in toc_items
    )
    return (
        '<nav class="post-content-toc" aria-label="文章目录">'
        '<div class="post-content-toc-title">目录</div>'
        f'<ol>{toc_links}</ol>'
        '</nav>'
    )


def render_post_inline_markdown(escaped_text):
    inline_code_segments = []

    def stash_inline_code(match):
        inline_code_segments.append(f'<code>{match.group(1)}</code>')
        return f'\x00INLINE_CODE_{len(inline_code_segments) - 1}\x00'

    rendered_text = re.sub(
        r'`([^`\n]+)`',
        stash_inline_code,
        escaped_text,
    )
    rendered_text = re.sub(
        r'!\[([^\]\n]*)\]\(([^)\s]+)\)',
        render_post_markdown_image,
        rendered_text,
    )
    rendered_text = re.sub(
        r'\[([^\]\n]+)\]\(([^)\s]+)\)',
        render_post_markdown_link,
        rendered_text,
    )
    rendered_text = re.sub(
        r'\*\*([^\n]+?)\*\*',
        r'<strong>\1</strong>',
        rendered_text,
    )
    for code_index, code_html in enumerate(inline_code_segments):
        rendered_text = rendered_text.replace(f'\x00INLINE_CODE_{code_index}\x00', code_html)
    return rendered_text


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
