from django.contrib import admin
from .models import Mission, MapName, Player, GameSession, GameSessionPlayer, ApiToken
from .models import LegacyRole, LegacyMission, LegacyImportError, LegacyGameSession, LegacyMapNames, LegacyGameSessionPlayerRole, LegacyPlayers
from django.contrib.auth.models import User, Group
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django import forms
import secrets

@admin.register(Mission)
class MissionAdmin(admin.ModelAdmin):
    list_display = ('name', 'version', 'map', 'status', 'pbo_missing', 'user')
    list_filter = ('status', 'pbo_missing', 'type')
    search_fields = ('name', 'authors', 'map')


admin.site.register(Player)
admin.site.register(GameSession)
admin.site.register(GameSessionPlayer)

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

# Ajout du groupe Mission Maker à la création d'utilisateur
class CustomUserAdmin(BaseUserAdmin):
    list_display = BaseUserAdmin.list_display + ('get_role',)

    def get_role(self, obj):
        if obj.is_superuser:
            return "Admin"
        elif obj.groups.filter(name="Mission Maker").exists():
            return "Mission Maker"
        return "Utilisateur"
    get_role.short_description = "Rôle"

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
        # 'key' est readonly dans le formulaire, 'created_at' toujours
        return self.readonly_fields
