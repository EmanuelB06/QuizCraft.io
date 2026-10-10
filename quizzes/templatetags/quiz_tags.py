from django import template

from ..services.grading import format_answer

register = template.Library()


@register.filter
def display_answer(response):
    """Render a Response's stored answer as readable text.

    Stored answers are indexes for multiple choice, which would otherwise
    show as "2" instead of the option text.
    """
    if response is None:
        return "No answer"
    return format_answer(response.question, response.user_answer)
