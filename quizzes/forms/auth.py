"""Login and signup forms.

Neither of these adds validation. They are the stock ``AuthenticationForm`` and
``UserCreationForm`` -- so every existing error message and the username
uniqueness check are untouched -- with the presentation moved out of the
templates and onto the widgets.
"""

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm


class BootstrapStyledForm:
    """Mixin putting Bootstrap's classes on every visible widget.

    Two gaps this closes. Django's widget rendering emits no ``class`` at all, so
    ``{{ field }}`` on the signup form produced unstyled native inputs, and
    Django never adds ``is-invalid`` because that is a Bootstrap idea rather than
    a Django one. Reading ``self.errors`` here is safe on a bound form: it runs
    the same validation ``is_valid()`` would and caches the result.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.HiddenInput):
                continue
            classes = [c for c in field.widget.attrs.get("class", "").split() if c]
            if "form-control" not in classes:
                classes.insert(0, "form-control")
            if self.is_bound and self.errors.get(name):
                classes.append("is-invalid")
            field.widget.attrs["class"] = " ".join(classes)


class LoginForm(BootstrapStyledForm, AuthenticationForm):
    """``AuthenticationForm`` with the autocomplete hints kept.

    ``login.html`` used to hand-write its ``<input>`` tags and so silently drop
    the ``autocomplete`` and ``autocapitalize`` attributes Django sets here.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update(
            {"autocapitalize": "none", "autocomplete": "username"}
        )
        self.fields["password"].widget.attrs.update({"autocomplete": "current-password"})


class SignupForm(BootstrapStyledForm, UserCreationForm):
    """``UserCreationForm`` with the same treatment.

    ``UserCreationForm`` already sets ``autocomplete="username"`` and
    ``new-password`` on the right fields, so only the styling is added.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update({"autocapitalize": "none"})
