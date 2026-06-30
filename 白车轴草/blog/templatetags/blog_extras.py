import re

from django import template
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe


register = template.Library()


@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)


@register.filter(name='post_content')
def post_content(value):
    escaped_content = conditional_escape(value or '')
    formatted_content = re.sub(
        r'\*\*([^\n]+?)\*\*',
        r'<strong>\1</strong>',
        str(escaped_content),
    )
    paragraphs = re.split(r'\n{2,}', formatted_content)
    rendered_paragraphs = []
    for paragraph in paragraphs:
        stripped_paragraph = paragraph.strip('\n')
        if not stripped_paragraph.strip():
            continue
        rendered_paragraphs.append(
            f'<p>{stripped_paragraph.replace(chr(10), "<br>")}</p>'
        )
    return mark_safe('\n\n'.join(rendered_paragraphs))
