
import re
from django.utils import timezone
from django.db import models
from django.contrib.auth.models import User
from django.db.models import JSONField

# Create your models here.

class Mission(models.Model):
    CO = 'CO'
    TVT = 'TVT'
    GM = 'GM'
    HC = 'HC'
    TRAINING = 'TRAINING'
    COM = 'COM'
    TYPE_CHOICES = [
        (CO, 'COOP'),
        (TVT, 'TvT'),
        (GM, 'GM'),
        (HC, 'HiCom'),
        (TRAINING, 'Training'),
        (COM, 'COM'),
    ]

    DEFAULT_NOT_PROVIDED = 'Non renseigné'

    STATUS_JOUABLE = 'JOUABLE'
    STATUS_NON_JOUABLE = 'NON_JOUABLE'
    STATUS_INCONNU = 'INCONNU'
    STATUS_SUPPRIMEE = 'SUPPRIMEE'
    STATUS_CHOICES = [
        (STATUS_JOUABLE, 'Jouable'),
        (STATUS_NON_JOUABLE, 'Non jouable'),
        (STATUS_INCONNU, 'Inconnu'),
        (STATUS_SUPPRIMEE, 'Supprimée'),
    ]

    name = models.CharField(max_length=255, verbose_name='Nom')
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, verbose_name='Utilisateur propriétaire', related_name='missions')
    authors = models.CharField(max_length=255, verbose_name='Auteurs')
    min_players = models.PositiveIntegerField(null=True, blank=True, verbose_name='Nombre de joueurs minimum')
    max_players = models.PositiveIntegerField(verbose_name='Nombre de joueurs maximum')
    type = models.CharField(max_length=16, choices=TYPE_CHOICES, verbose_name='Type')
    version = models.CharField(max_length=50, verbose_name='Version')
    map = models.CharField(max_length=100, verbose_name='Map')
    publication_date = models.DateTimeField(auto_now_add=True, verbose_name='Date de publication')
    onLoadMission = models.TextField(blank=True, default=DEFAULT_NOT_PROVIDED, verbose_name="onLoadMission (texte écran de chargement, sous l'image)")
    overviewText = models.TextField(blank=True, default=DEFAULT_NOT_PROVIDED, verbose_name="overviewText (texte page de lobby)")
    loadScreen = models.ImageField(upload_to='missions/', blank=True, null=True, verbose_name="loadScreen (image écran de chargement)")
    briefing = JSONField(blank=True, null=True, default=list, verbose_name="Briefing (éléments extraits du fichier briefing.sqf)")
    briefing_images = models.JSONField(default=list, blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_JOUABLE,
        verbose_name='Statut de la mission'
    )
    last_status_update = models.DateTimeField(null=True, blank=True, verbose_name='Dernier changement de statut')
    pbo_missing = models.BooleanField(
        default=False,
        verbose_name='PBO manquant sur le serveur',
    )

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Vérifie que le nom est bien au format complet CPC-YY[XX]-MissionName
        # skip_name_check : recovery temporaire (noms hors convention)
        skip_name_check = kwargs.pop('skip_name_check', False) or getattr(self, '_skip_name_check', False)
        if not skip_name_check:
            allowed_types = '|'.join([choice[0] for choice in Mission.TYPE_CHOICES])
            pattern = rf"^CPC-(?:{allowed_types})\[\d{{2,3}}\]-[\w\d\s\-\_\(\)@#%&'éèàùâêîôûäëïöüçÉÈÀÙÂÊÎÔÛÄËÏÖÜÇ]+$"
            if not re.match(pattern, self.name):
                raise ValueError("Le champ 'name' doit être au format complet : CPC-YY[XX]-NomMission")
        if self.pk is not None:
            update_fields = kwargs.get('update_fields')
            if update_fields is None or 'status' in update_fields:
                orig_status = (
                    Mission.objects.filter(pk=self.pk)
                    .values_list('status', flat=True)
                    .first()
                )
                if orig_status is not None and orig_status != self.status:
                    self.last_status_update = timezone.now()
                    # Si update_fields est fourni, Django n'écrit que ces colonnes :
                    # inclure last_status_update sinon le timestamp est perdu.
                    if update_fields is not None:
                        kwargs['update_fields'] = list(
                            dict.fromkeys([*update_fields, 'last_status_update'])
                        )
        else:
            self.last_status_update = timezone.now()
            update_fields = kwargs.get('update_fields')
            if update_fields is not None and 'last_status_update' not in update_fields:
                kwargs['update_fields'] = list(
                    dict.fromkeys([*update_fields, 'last_status_update'])
                )
        super().save(*args, **kwargs)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['name', 'map', 'max_players'],
                name='mission_name_map_max_players_uniq',
            ),
        ]
        indexes = [
            models.Index(fields=['map', 'name'], name='mission_map_name_idx'),
            models.Index(fields=['map'], name='mission_map_idx'),
            models.Index(fields=['name'], name='mission_name_idx'),
            models.Index(fields=['pbo_missing'], name='mission_pbo_missing_idx'),
        ]


class MapName(models.Model):
    code_name = models.CharField(max_length=100, unique=True, verbose_name='Nom de la carte (code)')
    display_name = models.CharField(max_length=255, verbose_name='Nom affiché')

    def __str__(self):
        return self.display_name

class Player(models.Model):
    name = models.CharField(max_length=255, unique=True, verbose_name='Nom du joueur')
    # Update this after legacy importation!
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Date de création')
    #created_at = models.DateTimeField(verbose_name='Date de création')
    users = models.ManyToManyField(User, related_name='players', blank=True, verbose_name='Utilisateurs liés')

    def __str__(self):
        return self.name

class GameSession(models.Model):
    mission = models.ForeignKey('Mission', null=True, blank=True, on_delete=models.SET_NULL, related_name='game_sessions')
    name = models.CharField(max_length=255, verbose_name='Nom de la mission')
    map = models.CharField(max_length=100, verbose_name='Carte')
    version = models.CharField(max_length=50, verbose_name='Version de la mission')
    start_time = models.DateTimeField(verbose_name='Début de la mission')
    end_time = models.DateTimeField(null=True, blank=True, verbose_name='Fin de la mission')
    VERDICT_INCONNU = 'INCONNU'
    VERDICT_EFFACER = '@EFFACER'
    VERDICT_SUCCES = 'SUCCES'
    VERDICT_ECHEC = 'ECHEC'
    VERDICT_PVP = 'PvP'
    VERDICT_TRAINING = 'TRAINING'
    VERDICT_CHOICES = [
        (VERDICT_INCONNU, 'Inconnu'),
        (VERDICT_EFFACER, '@Effacer'),
        (VERDICT_SUCCES, 'Succès'),
        (VERDICT_ECHEC, 'Echec'),
        (VERDICT_PVP, 'PvP'),
        (VERDICT_TRAINING, 'Training'),
    ]
    verdict = models.CharField(
        max_length=16,
        choices=VERDICT_CHOICES,
        default=VERDICT_INCONNU,
        verbose_name='Verdict de la session'
    )

    def __str__(self):
        return f"Session {self.name or self.mission} ({self.start_time})"

    class Meta:
        indexes = [
            models.Index(fields=['mission', 'start_time'], name='gs_mission_start_idx'),
            models.Index(fields=['map', 'start_time'], name='gs_map_start_idx'),
            models.Index(fields=['start_time'], name='gs_start_time_idx'),
        ]

class GameSessionPlayer(models.Model):
    STATUS_CHOICES = [
        ('VIVANT', 'Vivant'),
        ('MORT', 'Mort'),
    ]
    session = models.ForeignKey(GameSession, on_delete=models.CASCADE, related_name='players')
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='game_sessions')  # player obligatoire, non-nullable
    role = models.CharField(max_length=100, db_index=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='VIVANT')

    def __str__(self):
        player_name = self.player.name if self.player else 'Joueur inconnu'
        return f"{player_name} ({self.role}) - {self.status}"


class RoleCategory(models.Model):
    """Table de correspondance : rôle brut (GameSessionPlayer.role) → catégorie normalisée."""
    role_name = models.CharField(max_length=100, unique=True, verbose_name='Nom du rôle (brut)')
    category = models.CharField(max_length=100, verbose_name='Catégorie de rôle')

    def __str__(self):
        return f"{self.role_name} -> {self.category}"

    class Meta:
        verbose_name = 'Catégorie de rôle'
        verbose_name_plural = 'Catégories de rôles'
        ordering = ['category', 'role_name']


class ApiToken(models.Model):
    key = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({'actif' if self.is_active else 'inactif'})"

##########################################################################
# Legacy models (temporary)
##########################################################################

class LegacyMission(models.Model):
    name = models.CharField(max_length=255, verbose_name='Nom')
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, verbose_name='Utilisateur propriétaire', related_name='legacy_missions')
    authors = models.CharField(max_length=255, verbose_name='Auteurs', blank=True, default='')
    min_players = models.PositiveIntegerField(null=True, blank=True, verbose_name='Nombre de joueurs minimum')
    max_players = models.PositiveIntegerField(verbose_name='Nombre de joueurs maximum')
    type = models.CharField(max_length=16, verbose_name='Type')
    pbo_file = models.FileField(upload_to='legacy_missions/', verbose_name='Fichier de mission (.pbo)')
    version = models.CharField(max_length=50, verbose_name='Version')
    map = models.CharField(max_length=100, verbose_name='Map')
    upload_date = models.DateTimeField(auto_now_add=True, verbose_name='Date d\'upload')
    onLoadMission = models.TextField(blank=True, default='', verbose_name="onLoadMission (texte écran de chargement, sous l'image)")
    overviewText = models.TextField(blank=True, default='', verbose_name="overviewText (texte page de lobby)")
    loadScreen = models.ImageField(upload_to='legacy_missions/', blank=True, null=True, verbose_name="loadScreen (image écran de chargement)")
    briefing = JSONField(blank=True, null=True, default=list, verbose_name="Briefing (éléments extraits du fichier briefing.sqf)")
    briefing_images = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, blank=True, default='', verbose_name='Statut de la mission')
    linkedUser = models.CharField(max_length=255, verbose_name='Utilisateur lié', blank=True, default='')

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.linkedUser:
            self.linkedUser = self.authors
            if self.linkedUser.strip() == 'Non renseigné':
                if self.name in ['CPC-COM[33]-Cache_cash', 
                                 'CPC-COM[18]-Cache_cash',
                                 'CPC-COM[43]-full_metal_cachette',
                                 'CPC-CO[08]-Le_cri_du_rale',
                                 'CPC-CO[06]-BAF_Rendez-vous',
                                 'CPC-CO[10]-ile_aux_pirates',
                                 'CPC-COM[14]-Trauma_RHS',
                                 'CPC-CO[10]-BAF_Gasoline',
                                 'CPC-TVT[16]-TARGET_caribou',
                                 'CPC-TVT[16]-TARGET_darshag',
                                 'CPC-TVT[20]-BR_Thirsk',
                                 'CPC-CO[15]-Sabotage',
                                 'CPC-CO[20]-Le_Grand_Saut',
                                 'CPC-TVT[16]-TARGET_frini',
                                 'CPC-CO[16]-alqafs',
                                 'CPC-TR[17]-Saut_HALO',
                                 'CPC-CO[14]-RDV_a_zapadlisko',
                                 'CPC-TVT[16]-BR_ile_aux_tresors',
                                 'CPC-TVT[16]-TARGET_IFA_baranow_1',
                                 'CPC-TVT[16]-TARGET_barro',
                                 'CPC-CO[12]-Los_pajaros',
                                 'CPC-TVT[16]-TARGET_deadland',
                                 'CPC-CO[15]-fin_de_treve',
                                 'CPC-CO[15]-la_fievre_des_echecs',
                                 'CPC-CO[15]-El_Golpe',
                                 'CPC-CO[18]-Scudbidouwa',
                                 'CPC-CO[30]-Le_Grand_Saut',
                                 'CPC-TVT[20]-BR_Ahmad',
                                 'CPC-TVT[16]-TARGET_IFA_baranow_2',
                                 'CPC-TVT[16]-TARGET_shahbaz',
                                 'CPC-TVT[16]-TARGET_winter_pusta',
                                 'CPC-CO[17]-pico_turquino',
                                 'CPC-TVT[20]-BR_Bagango',
                                 'CPC-CO[16]-Pente_glissante',
                                 'CPC-TVT[16]-TARGET_lingor',
                                 'CPC-CO[14]-BAF_scorpion',
                                 'CPC-CO[08]-La_derniere_heure',
                                 'CPC-CO[08]-nageurs_de_combat',
                                 'CPC-CO[10]-Coupure',
                                 'CPC-CO[08]-arborescence',
                                 'CPC-CO[10]-il_faut_sauver_le_soldat_Weldrid',
                                 'CPC-CO[25]-le_temps_des_pluies',
                                 'CPC-CO[10]-viet_bac',
                                 'CPC-CO[25]-Hasta_Siempre',
                                 'CPC-CO[10]-Decuvee',
                                 'CPC-CO[10]-ravitaillement',
                                 'CPC-CO[10]-Snowball',
                                 'CPC-CO[08]-le_rugissement_du_tigre_1',
                                 'CPC-CO[08]-fuite_africaine',
                                 'CPC-CO[08]-Monsieur_Bombe',
                                 'CPC-CO[08]-proxy_connections',
                                 'CPC-CO[08]-SAF_Le_poids_des_murs',
                                 'CPC-CO[08]-Secret',
                                 'CPC-CO[08]-Najmudin_2',
                                 'CPC-CO[10]-above_tonos',
                                 'CPC-CO[08]-des_armes',
                                 'CPC-CO[13]-sugar_train',
                                 'CPC-CO[24]-Polvo',
                                 'CPC-CO[12]-Zone_de_Friction',
                                 'CPC-CO[30]-conflit_larve',
                                 'CPC-CO[13]-renegat',
                                 'CPC-CO[12]-places_gratuites',
                                 'CPC-CO[08]-merlan_frit',
                                 'CPC-CO[14]-la_cote',
                                 'CPC-CO[06]-shadow_of_topolin',
                                 'CPC-CO[12]-la_der',
                                 'CPC-CO[12]-ultime_topo',
                                 'CPC-TVT[20]-BR_Utes',]:
                    self.linkedUser = 'Sparfell'
                elif self.name in ['CPC-CO[16]-OperationPhantomCarbon']:
                    self.linkedUser = 'Apoc'
                elif self.name in ['CPC-CO[10]-Operation_baliste']:
                    self.linkedUser = 'Ashrak'
                elif self.name in ['CPC-TVT[12]-Le_Sentier_de_la_Gloire',
                                   'CPC-CO[24]-Doug_et_Alfred',
                                   'CPC-CO[28]-Tel_Meggido',
                                   'CPC-CO[24]-Tel_Meggido',
                                   'CPC-CO[08]-Le_Pont_Du_Manteau',
                                   'CPC-CO[24]-Le_Culte_de_Khong_Phrachao',
                                   'CPC-CO[28]-Vigilante',
                                   "CPC-CO[21]-Dans_les_prisons_d'Arghuk",
                                   'CPC-CO[26]-Convergence_Harmonique']:
                    self.linkedUser = 'bluth'
                elif self.name in ['CPC-CO-[14]-Qui_aime_se_vent']:
                    self.linkedUser = 'Bruno'
                elif self.name in ['CPC-GM[12]-Mission_de_routine']:
                    self.linkedUser = "Eagletres4"
                elif self.name in ['CPC-CO[12]-Leger_Grain']:
                    self.linkedUser = "Elma"
                elif self.name in ['CPC-CO[14]-Le_Commencement']:
                    self.linkedUser = "Folken"
                elif self.name in ['CPC-CO[12]-Gaia_Bleue_I',
                                   'CPC-CO[12]-Gaia_Bleue_II',
                                   'CPC-CO[12]-Gaia_Bleue_III',
                                   'CPC-CO[12]-Gaia_Bleue_IV',
                                   'CPC-CO[12]-Gaia_Bleue_V',
                                   'CPC-CO[12]-Gaia_Rouge_I',
                                   'CPC-CO[12]-Gaia_Rouge_II',
                                   'CPC-CO[12]-Gaia_Rouge_III',
                                   'CPC-CO[12]-Gaia_Rouge_IV',
                                   'CPC-CO[12]-Gaia_Rouge_V',
                                   'CPC-CO[12]-Gaia_Verte_I',
                                   'CPC-CO[12]-Gaia_Verte_II',
                                   'CPC-CO[12]-Gaia_Verte_III',
                                   'CPC-CO[12]-Gaia_Verte_IV',
                                   'CPC-CO[12]-Gaia_Verte_V',]:
                    self.linkedUser = "Izual"
                elif self.name in ['CPC-CO[13]-La_derniere_danse_de_OuiOui']:
                    self.linkedUser = "Minebrothers"
                elif self.name in ['CPC-CO[21]-Mort_aux_Moros',
                                   'CPC-CO[24]-Un_ticket_pour_le_paradis',
                                   'CPC-CO[18]-La_chute_de_l_Empire',
                                   'CPC-CO[19]-Chtorm_333',
                                   'CPC-CO[19]-Matinee_brumeuse',
                                   'CPC-CO[19]-Les_perles-rouges',
                                   'CPC-CO[16]-Les_perles-rouges',
                                   'CPC-CO[14]-Les_perles_bleues',
                                   'CPC-CO[16]-Chasse_aux_chasseurs',
                                   'CPC-CO[16]-Free_Ocalan',
                                   'CPC-CO[16]-Reveil_brusque',
                                   'CPC-CO[08]-Gangstas_Paradise',
                                   'CPC-CO[26]-Le_droit_du_plus_fort',
                                   'CPC-CO[25]-Un_pont_trop_pres',
                                   'CPC-CO[09]-Energie-perpetuelle',
                                   'CPC-CO[09]-Kalastus',
                                   'CPC-CO[08]-Les_deux_villages',]:
                    self.linkedUser = 'Pataplouf'
                elif self.name in ['CPC-HICOM[20]-Regicide']:
                    self.linkedUser = 'Perdance'
                elif self.name in ['CPC-CO[16]-La_Revanche_de_Massoud']:
                    self.linkedUser = 'Pocman'
                elif self.name in ['CPC-CO[17]-Chien_de_traineau',
                                   'CPC-CO[14]-Il_faut_escorter_Willy',
                                   'CPC-CO[18]-Des_lions_en_cages',
                                   'CPC-CO[17]-Chien_de_traineau']:
                    self.linkedUser = 'Raiver'
                elif self.name in ['CPC-CO[12]-Asylum',
                                   'CPC-CO[10]-Deratisation']:
                    self.linkedUser = 'Random'
                elif self.name in ["CPC-CO[14]-L'etoile",
                                   'CPC-CO[14]-The-hunt',
                                   'CPC-CO[15]-Operation-Eiche',
                                   'CPC-CO[15]-Pegasus-Bridge',
                                   'CPC-CO[16]-Operation-octobre-rouge',
                                   'CPC-CO[18]-Age-de-glace',
                                   'CPC-CO[18]-Carre-As',
                                   'CPC-CO[18]-La-Vallee',
                                   'CPC-CO[18]-Le-Cartel-de-Sonora',
                                   'CPC-CO[18]-Le-groupe-Arcadia',
                                   'CPC-CO[18]-Manoir-Brecourt',
                                   'CPC-CO[18]-Operation-Kodiac',
                                   'CPC-CO[18]-Panzerjager',
                                   'CPC-CO[19]-3h10-pour-El-Alamein',
                                   'CPC-CO[20]-Easy-Compagny',
                                   'CPC-CO[20]-Operation-Atlas',
                                   'CPC-CO[20]-Rescue-Tom',
                                   'CPC-CO[22]-Operation-Octopus',
                                   'CPC-CO[23]-Farc',
                                   'CPC-CO[23]-Les-larmes-du-soleil',
                                   'CPC-CO[25]-Arrowhead',
                                   'CPC-CO[26]-Operation-Fire-Shield',
                                   'CPC-CO[27]-La-prise-de-Neville',
                                   'CPC-CO[28]-Cimetiere',
                                   'CPC-CO[28]-Operation-Mama-One',
                                   'CPC-CO[28]-Operation-Salamandre',
                                   'CPC-CO[29]-Aube-rouge',]:
                    self.linkedUser = "Sardo"
                elif self.name in ['CPC-CO[27]-Operation-Newton',
                                   'CPC-GM[21]-Oborona']:
                    self.linkedUser = "Socio"
                elif self.name in ['CPC-CO[09]-CDF-Contre_Artillerie']:
                    self.linkedUser = "Woami"
                elif self.name in ['CPC-CO[20]-Patrouille_Chinari',
                                   'CPC-CO[15]-L_art_du_vole',
                                   'CPC-CO[15]-Raid_Americain',
                                   'CPC-CO[12]-La_discretion_est_la_clee',]:
                    self.linkedUser = 'Zey'
        super().save(*args, **kwargs)

class LegacyImportError(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    filename = models.CharField(max_length=255)
    error_message = models.TextField()

    def __str__(self):
        return f"{self.filename}: {self.error_message[:60]}"

class LegacyRole(models.Model):
    legacy_id = models.PositiveIntegerField(verbose_name='ID du rôle')
    name = models.CharField(max_length=255, verbose_name='Nom du rôle')

    def __str__(self):
        return f"{self.legacy_id} - {self.name}"


class LegacyGameSession(models.Model):
    session_id = models.PositiveIntegerField(verbose_name='ID')
    name = models.CharField(max_length=255, verbose_name='Nom')
    start_time = models.DateTimeField(verbose_name='Début')
    end_time = models.DateTimeField(verbose_name='Fin')
    verdict = models.CharField(max_length=50, verbose_name='Verdict')
    map_name = models.CharField(max_length=100, verbose_name='Nom de la map')

    def __str__(self):
        return f"{self.session_id} - {self.name} ({self.start_time})"


class LegacyMapNames(models.Model):
    code_name = models.CharField(max_length=100, verbose_name='Code name')
    display_name = models.CharField(max_length=255, verbose_name='Display name')
    game_session_names = models.JSONField(default=list, blank=True, verbose_name='Game session names')

    def __str__(self):
        return f"{self.code_name} - {self.display_name}"


class LegacyGameSessionPlayerRole(models.Model):
    data_id = models.PositiveIntegerField(verbose_name='ID de la ligne')
    player_id = models.PositiveIntegerField(verbose_name='ID du joueur')
    gamesession_id = models.PositiveIntegerField(verbose_name='ID de la GameSession')
    role_id = models.PositiveIntegerField(verbose_name='ID du rôle')
    status = models.CharField(max_length=50, verbose_name='Statut')

    def __str__(self):
        return f"Session {self.gamesession_id} - Joueur {self.player_id} - Rôle {self.role_id} - {self.status}"


class LegacyPlayers(models.Model):

    legacy_id = models.PositiveIntegerField(verbose_name='ID du joueur (CSV)', null=True, blank=True)
    name = models.CharField(max_length=255, verbose_name='Nom du joueur')
    created_at = models.DateTimeField(verbose_name='Date de création')
    raw_data = models.JSONField(default=dict, blank=True, verbose_name='Données brutes du CSV')

    def __str__(self):
        return self.name
