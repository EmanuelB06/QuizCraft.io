"""Login and signup forms.

Neither of these adds validation. They are the stock ``AuthenticationForm`` and
``UserCreationForm`` -- so every existing error message and the username
uniqueness check are untouched -- with the presentation moved out of the
templates and onto the widgets.

``FirestoreSignupForm`` is the one deliberate exception. When accounts live in
Firestore there is no ORM row to validate against (``UserCreationForm``'s
``_post_clean`` queries the empty `users` table), so the Firestore signup form
is a plain ``Form`` whose uniqueness check goes through the account store
instead. It keeps the same field names so the template is shared.
"""

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator


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
    ``new-password`` on the right fields, so only the styling is added. Its
    ``Meta.model`` hardcodes ``django.contrib.auth.models.User`` though, which
    is a proxy for nothing once ``AUTH_USER_MODEL`` is swapped, so it is
    re-pointed at the actual account model here.
    """

    class Meta(UserCreationForm.Meta):
        model = get_user_model()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update({"autocapitalize": "none"})


class FirestoreSignupForm(BootstrapStyledForm, forms.Form):
    """The signup form used when accounts live on Firestore.

    Field names match ``SignupForm`` so the template needs no branch, but the
    stock ``UserCreationForm`` ``_post_clean`` queries the ORM `users` table
    for uniqueness, which would miss accounts that were written to Firestore.
    The uniqueness check and ``save`` go through the account store instead,
    and password validation runs against a stitched-together ``User``.
    """

    error_messages = {"unique": "A user with that username already exists."}

    username = forms.CharField(
        max_length=150,
        help_text=(
            "Required. 150 characters or fewer. Letters, digits and "
            "@/./+/-/_ only."
        ),
        validators=[UnicodeUsernameValidator()],
        widget=forms.TextInput(
            attrs={"autocapitalize": "none", "autocomplete": "username"}
        ),
    )
    password1 = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="Password confirmation",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Enter the same password as before, for verification.",
    )

    def clean_username(self):
        username = self.cleaned_data.get("username") or ""
        try:
            get_user_model().objects.get(username=username)
        except get_user_model().DoesNotExist:
            return username
        raise forms.ValidationError(self.error_messages["unique"], code="unique")

    def clean_password2(self):
        password1 = self.cleaned_data.get("password1")
        password2 = self.cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError("The two password fields didn't match.")
        return password2

    def _post_clean(self):
        password = self.cleaned_data.get("password2")
        if password:
            user = get_user_model()(username=self.cleaned_data.get("username"))
            try:
                validate_password(password, user)
            except forms.ValidationError as error:
                self.add_error("password2", error)

    def save(self):
        return get_user_model().objects.create_user(
            username=self.cleaned_data["username"],
            password=self.cleaned_data["password1"],
        )
