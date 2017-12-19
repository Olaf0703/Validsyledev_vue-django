from django.contrib.auth import login, authenticate, REDIRECT_FIELD_NAME
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import PasswordResetView as BasePasswordResetView, SuccessURLAllowedHostsMixin
from django.shortcuts import get_object_or_404, resolve_url
from django.utils.decorators import method_decorator
from django.utils.http import is_safe_url
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from django.utils.translation import gettext_lazy as _
from django.views.generic import RedirectView
from django.views.generic.edit import FormView
from django.conf import settings

from .utils import (
    get_login_form, send_activation_email, get_password_reset_form, send_reset_password_email,
    send_activation_change_email
)
from .forms import SignUpForm, ReSendActivationCodeForm, ProfileEditForm, ChangeEmailForm
from .models import Activation


class SuccessRedirectView(SuccessURLAllowedHostsMixin, FormView):
    redirect_field_name = REDIRECT_FIELD_NAME

    def get_success_url(self):
        url = self.get_redirect_url()
        return url or resolve_url(settings.LOGIN_REDIRECT_URL)

    def get_redirect_url(self):
        redirect_to = self.request.POST.get(
            self.redirect_field_name,
            self.request.GET.get(self.redirect_field_name, '')
        )
        url_is_safe = is_safe_url(
            url=redirect_to,
            allowed_hosts=self.get_success_url_allowed_hosts(),
            require_https=self.request.is_secure(),
        )
        return redirect_to if url_is_safe else ''

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['request'] = self.request
        return kwargs


class SignInView(SuccessRedirectView):
    template_name = 'accounts/login.html'
    form_class = get_login_form()
    success_url = '/'

    @method_decorator(sensitive_post_parameters('password'))
    @method_decorator(csrf_protect)
    @method_decorator(never_cache)
    def dispatch(self, request, *args, **kwargs):
        # Sets a test cookie to make sure the user has cookies enabled
        request.session.set_test_cookie()

        return super(SignInView, self).dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        # If the test cookie worked, go ahead and
        # delete it since its no longer needed
        if self.request.session.test_cookie_worked():
            self.request.session.delete_test_cookie()

        login(self.request, form.get_user())

        return super(SignInView, self).form_valid(form)


class SignUpView(FormView):
    template_name = 'accounts/register.html'
    form_class = SignUpForm
    success_url = '/'

    def form_valid(self, form):
        if settings.ENABLE_USER_ACTIVATION:
            user = form.save(commit=False)
            user.is_active = False
            user.save()

            send_activation_email(self.request, user)

            messages.add_message(self.request, messages.SUCCESS,
                                 _('You are registered. To activate the account, follow the link sent to the mail.'))
        else:
            form.save()

            username = form.cleaned_data.get('username')
            raw_password = form.cleaned_data.get('password1')

            user = authenticate(username=username, password=raw_password)
            login(self.request, user)

            messages.add_message(self.request, messages.SUCCESS, _('You are successfully registered!'))

        return super(SignUpView, self).form_valid(form)


class ActivateView(RedirectView):
    permanent = False
    query_string = True
    pattern_name = 'index'

    def get_redirect_url(self, *args, **kwargs):
        assert 'code' in kwargs

        act = get_object_or_404(Activation, code=kwargs['code'])

        # Activate user's profile
        user = act.user
        user.is_active = True
        user.save()

        # Remove activation record, it is unneeded
        act.delete()

        messages.add_message(self.request, messages.SUCCESS, _('You have successfully activated your account!'))
        login(self.request, user)

        return super(ActivateView, self).get_redirect_url()


class ReSendActivationCodeView(SuccessRedirectView):
    template_name = 'accounts/resend_activation_code.html'
    form_class = ReSendActivationCodeForm
    success_url = '/'

    def form_valid(self, form):
        user = form.get_user()

        activation = user.activation_set.get()
        activation.delete()

        send_activation_email(self.request, user)

        messages.add_message(self.request, messages.SUCCESS, _('A new activation code has been sent to your e-mail.'))

        return super(ReSendActivationCodeView, self).form_valid(form)


class PasswordResetView(BasePasswordResetView):
    form_class = get_password_reset_form()

    def form_valid(self, form):
        send_reset_password_email(self.request, form.get_user())

        return super(PasswordResetView, self).form_valid(form)


class ProfileEditView(LoginRequiredMixin, FormView):
    template_name = 'accounts/profile/edit.html'

    form_class = ProfileEditForm
    success_url = '/accounts/profile/edit/'

    def get_initial(self):
        initial = super(ProfileEditView, self).get_initial()

        user = self.request.user

        initial['first_name'] = user.first_name
        initial['last_name'] = user.last_name

        return initial

    def form_valid(self, form):
        user = self.request.user

        user.first_name = form.cleaned_data.get('first_name')
        user.last_name = form.cleaned_data.get('last_name')
        user.save()

        messages.add_message(self.request, messages.SUCCESS, _('Profile data has been successfully updated.'))

        return super(ProfileEditView, self).form_valid(form)


class ChangeEmailView(LoginRequiredMixin, FormView):
    template_name = 'accounts/profile/change_email.html'

    form_class = ChangeEmailForm
    success_url = '/accounts/change/email/'

    def get_form_kwargs(self):
        kwargs = super(ChangeEmailView, self).get_form_kwargs()
        kwargs['user'] = self.request.user

        return kwargs

    def get_initial(self):
        initial = super(ChangeEmailView, self).get_initial()

        user = self.request.user

        initial['email'] = user.email

        return initial

    def form_valid(self, form):
        user = self.request.user

        email = form.cleaned_data.get('email')
        email = email.lower()

        if hasattr(settings, 'EMAIL_ACTIVATION_AFTER_CHANGING') and settings.EMAIL_ACTIVATION_AFTER_CHANGING:
            send_activation_change_email(self.request, user, email)

            messages.add_message(self.request, messages.SUCCESS,
                                 _('To complete the change of mail, click on the link sent to it.'))
        else:
            user.email = email
            user.save()

            messages.add_message(self.request, messages.SUCCESS, _('Email successfully changed.'))

        return super(ChangeEmailView, self).form_valid(form)


class ChangeEmailActivateView(RedirectView):
    permanent = False
    query_string = True
    pattern_name = 'change_email'

    def get_redirect_url(self, *args, **kwargs):
        assert 'code' in kwargs

        act = get_object_or_404(Activation, code=kwargs['code'])

        # Change user's email
        user = act.user
        user.email = act.email
        user.save()

        # Remove activation record, it is unneeded
        act.delete()

        messages.add_message(self.request, messages.SUCCESS, _('You have successfully changed your email!'))

        return super(ChangeEmailActivateView, self).get_redirect_url()
