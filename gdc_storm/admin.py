from django.contrib import admin
from django.contrib.auth.models import User, Group
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django import forms
from django.contrib import messages

from .models import Mission, MapName, Player, GameSession, GameSessionPlayer, RoleCategory, ApiToken
from .models import LegacyRole, LegacyMission, LegacyImportError, LegacyGameSession, LegacyMapNames, LegacyGameSessionPlayerRole, LegacyPlayers

admin.site.register(Mission)
admin.site.register(Player)
admin.site.register(GameSession)
admin.site.register(GameSessionPlayer)


@admin.register(RoleCategory)
class RoleCategoryAdmin(admin.ModelAdmin):
    list_display = ('role_name', 'category')
    list_editable = ('category',)
    search_fields = ('role_name', 'category')
    ordering = ('category', 'role_name')

# Register legacy data
admin.site.register(LegacyMission)
admin.site.register(LegacyImportError)
admin.site.register(LegacyRole)
admin.site.register(LegacyGameSession)
admin.site.register(LegacyMapNames)
admin.site.register(LegacyGameSessionPlayerRole)
admin.site.register(LegacyPlayers)


@admin.register(MapName)
class MapNameAdmin(admin.ModelAdmin):
    list_display = ('code_name', 'display_name')


class PendingApprovalFilter(admin.SimpleListFilter):
    title = "Validation"
    parameter_name = "pending"

    def lookups(self, request, model_admin):
        return (
            ("pending", "En attente"),
            ("active", "Actifs"),
        )

    def queryset(self, request, queryset):
        if self.value() == "pending":
            return queryset.filter(is_active=False)
        if self.value() == "active":
            return queryset.filter(is_active=True)
        return queryset


class CustomUserAdmin(BaseUserAdmin):
    list_display = BaseUserAdmin.list_display + ('get_role', 'get_social_providers', 'is_active')
    list_filter = BaseUserAdmin.list_filter + (PendingApprovalFilter,)
    actions = ('approve_accounts',)

    def get_role(self, obj):
        if obj.is_superuser:
            return "Admin"
        elif obj.groups.filter(name="Mission Maker").exists():
            return "Mission Maker"
        return "Utilisateur"
    get_role.short_description = "Rôle"

    def get_social_providers(self, obj):
        try:
            providers = list(
                obj.socialaccount_set.values_list('provider', flat=True)
            )
        except Exception:
            return "—"
        if not providers:
            return "—"
        return ", ".join(sorted(providers))
    get_social_providers.short_description = "Social"

    @admin.action(description="Approuver les comptes sélectionnés")
    def approve_accounts(self, request, queryset):
        updated = queryset.filter(is_active=False).update(is_active=True)
        self.message_user(
            request,
            f"{updated} compte(s) approuvé(s).",
            messages.SUCCESS,
        )


admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)


class ApiTokenForm(forms.ModelForm):
    class Meta:
        model = ApiToken
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            import secrets
            self.fields['key'].initial = secrets.token_hex(32)
        self.fields['key'].widget.attrs['readonly'] = True


@admin.register(ApiToken)
class ApiTokenAdmin(admin.ModelAdmin):
    form = ApiTokenForm
    list_display = ("name", "key", "is_active", "created_at")
    readonly_fields = ("created_at",)
    search_fields = ("name", "key")
    list_filter = ("is_active",)

    def save_model(self, request, obj, form, change):
        if not obj.key:
            obj.key = form.cleaned_data['key']
        super().save_model(request, obj, form, change)

    def get_readonly_fields(self, request, obj=None):
        return self.readonly_fields
