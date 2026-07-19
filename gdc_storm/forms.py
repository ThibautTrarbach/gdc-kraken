from django import forms
from django.contrib.auth.models import User

from .models import Mission


class MissionUploadForm(forms.ModelForm):
    class Meta:
        model = Mission
        fields = ['name', 'user', 'authors', 'min_players', 'max_players', 'type']
        labels = {
            'name': 'Nom',
            'user': 'Propriétaire',
            'authors': 'Auteurs',
            'min_players': 'Nombre de joueurs minimum',
            'max_players': 'Nombre de joueurs maximum',
            'type': 'Type',
        }


class MissionStatusForm(forms.ModelForm):
    class Meta:
        model = Mission
        fields = ['status']
        widgets = {
            'status': forms.Select(attrs={'class': 'form-control'})
        }


class MissionOwnerForm(forms.ModelForm):
    user = forms.ModelChoiceField(
        queryset=User.objects.filter(is_active=True).order_by('username'),
        required=False,
        empty_label='— Aucun —',
        label='Propriétaire',
        widget=forms.Select(attrs={'class': 'form-control'}),
    )

    class Meta:
        model = Mission
        fields = ['user']

