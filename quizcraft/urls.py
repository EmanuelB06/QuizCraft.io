from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views

from quizzes import views as quiz_views
from quizzes.forms.auth import LoginForm


urlpatterns = [
    path("health/", quiz_views.health, name="health"),
    path("", quiz_views.landing, name="landing"),
    path("dashboard/", quiz_views.dashboard, name="dashboard"),
    path("accounts/signup/", quiz_views.signup, name="signup"),
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(
            template_name="registration/login.html",
            authentication_form=LoginForm,
        ),
        name="login",
    ),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("quizzes/", include("quizzes.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
