from django.conf import settings


def app_settings(request):
    """Expose a few config values so templates can adapt (e.g. upload limit)."""
    return {
        "MAX_UPLOAD_MB": settings.MAX_UPLOAD_MB,
        "GEMINI_CONFIGURED": bool(settings.GEMINI_API_KEY),
        "GEMINI_MODEL": settings.GEMINI_MODEL,
    }
