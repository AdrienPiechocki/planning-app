# Planning App

Génération assistée par IA locale (Ollama) d'un planning hebdomadaire
(lundi-vendredi), synchronisé avec Nextcloud (Calendar + Deck).

## État d'avancement — MVP complet

- [x] Squelette app + base de données
- [x] Intégration Ollama (génération du planning)
- [x] Sync Nextcloud (Calendar/Deck)
- [x] Interface web (formulaire + grille éditable)

## Démarrage rapide

Le plus simple : utiliser le script fourni, qui s'occupe de tout (venv,
dépendances, fichier `.env`, vérification Ollama) :

```bash
./start.sh              # lancement normal
./start.sh --dev        # avec rechargement automatique (développement)
./start.sh --port 8080  # changer le port (défaut : 8000)
./start.sh --host 0.0.0.0  # écouter sur toutes les interfaces (accès réseau)
```

Ou manuellement si tu préfères tout contrôler :

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# éditer .env avec tes identifiants Nextcloud + config Ollama

uvicorn app.main:app --reload
```

Ouvre http://127.0.0.1:8000/semaine — c'est le point d'entrée principal.
La doc API interactive reste dispo sur http://127.0.0.1:8000/docs si besoin.

## Docker (déploiement derrière Traefik)

L'app tourne en conteneur, avec Ollama lui-même en conteneur (pas besoin
d'Ollama installé sur la machine hôte), exposée via Traefik sur
`planning.adrien-dev.fr` :

```bash
cp .env.example .env
# éditer .env : au minimum tes identifiants Nextcloud (OLLAMA_BASE_URL et
# DATABASE_URL sont forcés par docker-compose.yml, pas besoin de les changer)

docker compose up -d --build
```

Au premier lancement, le service `ollama-pull` télécharge automatiquement le
modèle configuré (`OLLAMA_MODEL` dans `.env`, `gemma3n` par défaut) — ça peut
prendre plusieurs minutes selon ta connexion. Suis la progression avec :
```bash
docker compose logs -f ollama-pull
```
Il se termine ensuite normalement (`Exited (0)`, c'est attendu — c'est un
service "one-shot", pas censé rester en cours d'exécution).

Points importants :
- **Réseau Traefik** : `docker-compose.yml` suppose un réseau Docker externe
  nommé `traefik` (déjà créé par ta stack Traefik existante). Si le tien
  s'appelle différemment, ajuste `networks.traefik.name` dans
  `docker-compose.yml` (`docker network ls` pour vérifier).
- **Certresolver** : le label `tls.certresolver=letsencrypt` suppose un
  resolver nommé `letsencrypt` dans ta config Traefik — adapte si le tien
  porte un autre nom.
- **Base de données** : persistée dans `./data/planning.db` sur l'hôte (monté
  en volume), donc conservée entre les rebuilds/redémarrages du conteneur.
- **Modèles Ollama** : persistés dans `./ollama-data/` sur l'hôte (monté en
  volume), donc téléchargés une seule fois, pas à chaque rebuild.
- **GPU** : Ollama tourne sur CPU par défaut dans le conteneur (plus lent).
  Si tu as un GPU NVIDIA + le `nvidia-container-toolkit` installé sur l'hôte,
  décommente le bloc `deploy.resources.reservations.devices` du service
  `ollama` dans `docker-compose.yml`.
- **Réseau interne** : Ollama n'est joignable que depuis `planning-app`, via
  un réseau Docker privé (`internal`) créé par ce compose — jamais exposé via
  Traefik ni depuis l'extérieur.

Commandes utiles :
```bash
docker compose logs -f planning-app          # logs de l'app
docker compose exec ollama ollama list       # modèles installés
docker compose exec ollama ollama pull XXX   # installer/changer un modèle manuellement
docker compose restart planning-app          # relancer l'app
docker compose down                          # arrêter (les données restent dans ./data et ./ollama-data)
```

## Interface web

- **`/semaine`** : le formulaire du week-end — coche les impératifs récurrents actifs,
  ajoute les impératifs exceptionnels (lignes dynamiques via Alpine.js), décris tes
  envies de loisir et le thème du projet dev. La soumission génère immédiatement le
  planning et redirige dessus.
- **`/planning/{id}`** : grille lundi-vendredi. Chaque jour est une colonne avec un rail
  vertical façon graphe de commits (un point de couleur par catégorie : récurrent,
  exceptionnel, loisir, projet dev). Tant que le planning n'est pas validé, l'ajout,
  l'édition et la suppression de blocs se font en direct via HTMX, sans recharger la
  page. Si l'IA a généré trop peu d'activités, une bannière l'indique clairement et le
  bouton "↻ Régénérer les activités IA" relance uniquement la génération loisir/projet
  dev (les impératifs restent inchangés) — pratique si un modèle local sous-délivre.
  Le bouton "Valider et synchroniser" déclenche la sync Nextcloud et verrouille
  le planning (les blocs restent visibles mais non modifiables).
- **`/media`**, **`/skills`**, **`/constraints`** : gestion de la bibliothèque de jeux/
  films/séries, des compétences/envies d'apprentissage, et des impératifs récurrents —
  les trois sources d'information utilisées pour la génération. Ajout, **édition inline**
  (bouton "Modifier" sur chaque ligne, via HTMX, sans recharger la page) et suppression.

Thème visuel sombre inspiré d'un terminal (police JetBrains Mono pour les labels/tags,
Inter pour le texte), pensé pour un usage perso orienté dev.

## Comment fonctionne la génération (Ollama)

1. Les **impératifs** (récurrents actifs + exceptionnels de la semaine) sont insérés
   de façon déterministe, sans passer par l'IA — ce sont des faits exacts qu'on ne
   veut pas laisser une IA déplacer ou halluciner.
2. Le LLM est interrogé **uniquement** pour décider des activités de loisir et des
   sessions de projet dev, dans les créneaux encore libres, en s'appuyant sur ta
   bibliothèque média (priorité aux éléments "en cours"), tes compétences/envies
   d'apprentissage, et le thème/les envies saisis dans le formulaire.
3. La réponse est forcée en JSON strict (`format: "json"`), parsée et validée bloc
   par bloc ; tout bloc invalide est ignoré plutôt que de faire planter la génération.
4. Si Ollama est injoignable, le planning est quand même créé avec les impératifs
   seuls, et un avertissement clair s'affiche en haut de la page planning.

**Configuration** (`.env`) : `OLLAMA_BASE_URL` (ex: `http://localhost:11434`) et
`OLLAMA_MODEL`. `GET /api/ollama/health` vérifie la connectivité.

**Modèle recommandé : `gemma3n`** (`ollama pull gemma3n`). Il respecte les consignes
de format à la lettre, ce qui compte beaucoup ici puisqu'on force une sortie JSON
stricte. Le prompt système liste explicitement les formats interdits (objet unique,
regroupement par date, clé racine `"blocks"`) accompagnés d'un exemple concret de
sortie correcte — une structure à laquelle un modèle "instruction-strict" comme
Gemma3n adhère bien mieux qu'un modèle plus créatif avec les formats.

Le parsing (`parse_llm_blocks`) reste volontairement tolérant (liste plate, objet
unique, regroupement par date ou par clé englobante) : quel que soit le modèle utilisé,
une réponse mal formée est rattrapée plutôt que de faire échouer toute la génération.

### Remplissage de la journée et pauses fixes

Par défaut, l'IA cherche à remplir la plage `DAY_START_TIME`–`DAY_END_TIME` (10:00–22:30)
avec plusieurs activités par jour (2 à 4 quand assez de matière est disponible), tout en
respectant des pauses fixes définies par `PAUSE_TIMES` (12:30, 16:00, 20:00 par
défaut, réglables dans `.env`) et leur durée (`PAUSE_DURATION_MINUTES`, 30 par défaut).
Ces pauses ne sont pas des `PlanningBlock` visibles — elles sont simplement transmises
au LLM comme des créneaux occupés à ne jamais utiliser, au même titre que les impératifs.

Le prompt demande aussi explicitement d'alterner les catégories (loisir / projet dev)
d'un jour à l'autre plutôt que de regrouper toute une catégorie sur les mêmes jours.

**Plancher garanti côté prompt** : un nombre minimum d'activités est calculé côté serveur
(`compute_minimum_activities`, 1 par jour et par catégorie active — loisir et/ou projet dev)
et communiqué explicitement au LLM comme un plancher strict à atteindre, avec un exemple
de sortie multi-jours/multi-activités inclus dans le prompt système. L'appel Ollama utilise
aussi `temperature: 0.3` (plus littéral), `num_predict: 4096` et `num_ctx: 8192` (fenêtre
de contexte large, pour éviter qu'une réponse volumineuse soit coupée en plein milieu —
le prompt lui-même est déjà conséquent une fois rempli de contraintes/bibliothèque/exemple).

**Récupération d'une réponse tronquée** : si malgré tout la réponse s'arrête en plein milieu
(JSON invalide), `_try_repair_truncated_json` retrouve le dernier objet complet à n'importe
quelle profondeur d'imbrication, tronque proprement à cet endroit et referme les crochets/
accolades encore ouverts — les activités déjà générées sont récupérées plutôt que perdues.

**Filtrage des pauses recopiées par erreur** : certains modèles ont tendance à "halluciner"
un bloc reprenant une pause de la liste des créneaux occupés comme s'il s'agissait d'une
activité. Le prompt l'interdit explicitement, et un filtre défensif écarte quand même tout
bloc dont le titre contient "pause", au cas où.

**Anti-chevauchement systématique** : le prompt demande au LLM de ne jamais superposer
d'activités, mais un modèle local ne le respecte pas toujours. `filter_overlapping_blocks`
vérifie donc côté serveur, après coup, que chaque bloc proposé ne chevauche ni un créneau
déjà occupé (impératif/pause) ni un autre bloc LLM déjà accepté dans le même lot — tout
conflit est silencieusement écarté plutôt que d'atterrir dans ton planning.

**Nouvelle tentative automatique** : si après filtrage le total reste sous le minimum
attendu, une seconde génération est relancée automatiquement (avec les créneaux déjà
obtenus ajoutés aux occupations, pour ne pas les dupliquer) avant d'afficher un
avertissement — pour éviter d'avoir à cliquer sur "Régénérer" à chaque fois qu'un petit
modèle local sous-délivre au premier essai. Si le total reste insuffisant après ces deux
tentatives, l'avertissement détaille à la fois les éventuels chevauchements écartés et le
nombre d'activités manquantes, et le bouton "Régénérer" reste disponible pour réessayer.

## Comment fonctionne la synchronisation (Nextcloud)

Déclenchée automatiquement à la validation d'un planning :

1. **Calendar** (CalDAV) : un agenda nommé `NEXTCLOUD_CALENDAR_NAME` est trouvé ou
   créé, puis un événement est créé par bloc (horaire précis si renseigné, sinon
   "toute la journée").
2. **Deck** : un tableau nommé `NEXTCLOUD_DECK_BOARD_NAME` est trouvé ou créé, avec
   une colonne par jour et une carte par bloc.
3. **Isolation des pannes** : Calendar et Deck sont synchronisés indépendamment, et
   chaque bloc l'est indépendamment des autres — un échec isolé n'empêche jamais le
   reste de se synchroniser. Chaque tentative est tracée dans `SyncLog`.
4. Si Nextcloud n'est pas configuré, la validation reste possible mais la sync est
   ignorée avec un message explicite affiché dans l'interface.

**Configuration** (`.env`) : `NEXTCLOUD_URL`, `NEXTCLOUD_USERNAME`,
`NEXTCLOUD_APP_PASSWORD` (à générer dans Nextcloud : Paramètres → Sécurité →
"Mots de passe et jetons d'accès"). `GET /api/nextcloud/health` teste Calendar et
Deck séparément.

## Structure du projet

```
app/
├── main.py                    # point d'entrée FastAPI, montage des routers + static
├── config.py                  # configuration (variables d'env)
├── database.py                # connexion SQLAlchemy / SQLite
├── models.py                  # schéma de données (tables)
├── schemas.py                 # validation Pydantic (API JSON)
├── utils.py                   # calcul des dates de la semaine
├── web_templates.py           # config Jinja2Templates
├── templates/                 # pages HTML (Jinja2 + HTMX + Alpine.js)
│   ├── base.html
│   ├── semaine_form.html
│   ├── planning_view.html
│   ├── media.html / skills.html / constraints.html
│   └── partials/               # fragments réutilisés en réponse HTMX
├── static/css/style.css       # design tokens + styles
├── routers/
│   ├── web.py                  # toutes les pages HTML (interface principale)
│   ├── constraints.py          # API JSON impératifs récurrents
│   ├── media.py                # API JSON bibliothèque
│   ├── skills.py                # API JSON compétences
│   └── planning.py              # API JSON saisie/génération/édition/validation
└── services/
    ├── llm_service.py          # génération via Ollama
    └── nextcloud_service.py    # sync Calendar/Deck
```

Note : l'API JSON (`/api/...`, documentée sur `/docs`) et l'interface web (`/semaine`,
`/planning/...`) partagent la même base de données et la même logique métier
(`services/`). L'API JSON reste utile pour scripter ou déboguer indépendamment de
l'interface.

## Modèle de données

- **RecurringConstraint** : impératifs présents toutes les semaines
- **ExceptionalConstraint** : impératifs ponctuels, liés à une `WeeklyInput`
- **MediaItem** : jeux/films/séries possédés + statut de progression
- **SkillGoal** : compétences connues + envies d'apprentissage
- **WeeklyInput** : la saisie faite chaque week-end
- **GeneratedPlanning** : planning généré (brouillon → validé), lié à une `WeeklyInput`
- **PlanningBlock** : chaque activité/créneau du planning (éditable tant que non validé)
- **SyncLog** : historique des synchronisations Nextcloud

## Pistes d'amélioration possibles (non bloquantes)

- Ré-essayer la génération LLM ou la sync Nextcloud individuellement sans tout refaire
- Détection de conflits si un bloc modifié empiète sur un autre
- Authentification (l'app est actuellement pensée pour un usage strictement personnel,
  sans compte ni mot de passe)
- Passage à Alembic pour les migrations si le schéma évolue beaucoup
