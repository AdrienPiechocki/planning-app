"""
Service de génération de planning via Ollama (IA locale) + algorithmes déterministes.

Principe de conception (v5) :
- Les impératifs (récurrents + exceptionnels) sont insérés de façon
  DÉTERMINISTE (pas par le LLM), car ce sont des faits exacts (dates/heures)
  qu'on ne veut pas laisser une IA halluciner ou déplacer.
- La sélection des sujets de la semaine (jeux, films/séries, projet dev) est
  un ALGORITHME DÉTERMINISTE (pas d'IA), avec deux stratégies au choix par
  catégorie : sélection priorisée ("en_cours" avant "à_faire", dans toute la
  bibliothèque) ou sélection aléatoire (sans priorité, parmi une sélection de
  candidats faite par l'utilisateur). Le nombre de jeux et de films/séries à
  programmer par semaine est configurable.
- Les activités de LOISIR (jeu/film/série) reçoivent un titre générique
  déterministe ("Jouer à ..." / "Regarder ...") : pas d'appel IA.
- Le PROJET DEV est la seule chose que le LLM rédige : un unique appel Ollama
  transforme le(s) sujet(s) choisi(s) (1 ou plusieurs compétences) en une
  seule idée de projet concrète, réalisable en une semaine.
- L'ASSEMBLAGE du planning (répartition de ces activités sur les créneaux
  libres de la semaine, chacune avec sa propre durée de session) est un
  algorithme déterministe en Python, pas une tâche confiée au LLM.
"""
import json
import logging
import random
from datetime import date, timedelta, time as time_cls, datetime
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app import models
from app.config import settings

logger = logging.getLogger(__name__)

WEEKDAY_NAMES = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

# Durée cible et durée minimale d'un bloc d'activité lors de l'assemblage.
BLOCK_TARGET_MINUTES = 90
BLOCK_MIN_MINUTES = 30


class OllamaError(Exception):
    """Levée quand Ollama est injoignable ou renvoie une réponse inexploitable."""


# ---------------------------------------------------------------------------
# 1. Blocs déterministes (impératifs + pauses)
# ---------------------------------------------------------------------------
def insert_deterministic_blocks(
    db: Session,
    planning: models.GeneratedPlanning,
    weekly_input: models.WeeklyInput,
) -> list[dict]:
    """
    Crée les PlanningBlock pour les impératifs récurrents actifs et exceptionnels.
    Retourne la liste des créneaux occupés (dicts) pour servir de contexte à l'assemblage.
    """
    week_start: date = weekly_input.semaine_du
    weekdays = [week_start + timedelta(days=i) for i in range(5)]  # lundi -> vendredi
    occupied: list[dict] = []

    # --- Impératifs récurrents ---
    active_ids = set()
    if weekly_input.recurring_constraints_actives:
        try:
            active_ids = set(json.loads(weekly_input.recurring_constraints_actives))
        except (json.JSONDecodeError, TypeError):
            logger.warning("recurring_constraints_actives illisible pour weekly_input %s", weekly_input.id)

    if active_ids:
        recurring = (
            db.query(models.RecurringConstraint)
            .filter(models.RecurringConstraint.id.in_(active_ids), models.RecurringConstraint.actif.is_(True))
            .all()
        )
        for rc in recurring:
            targets = weekdays
            if rc.jour_prefere:
                targets = [d for d in weekdays if WEEKDAY_NAMES[d.weekday()] == rc.jour_prefere.lower()]
                if not targets:
                    targets = weekdays  # jour_prefere invalide -> on retombe sur toute la semaine

            for jour in targets:
                block = models.PlanningBlock(
                    planning_id=planning.id,
                    jour=jour,
                    heure_debut=rc.heure_debut,
                    heure_fin=rc.heure_fin,
                    titre=rc.nom,
                    description=rc.description or "",
                    categorie=models.ActivityCategory.IMPERATIF_RECURRENT,
                    ordre=0,
                )
                db.add(block)
                if rc.heure_debut and rc.heure_fin:
                    occupied.append({
                        "jour": jour.isoformat(), "heure_debut": rc.heure_debut.strftime("%H:%M"),
                        "heure_fin": rc.heure_fin.strftime("%H:%M"), "titre": rc.nom,
                    })

    # --- Impératifs exceptionnels ---
    for ec in weekly_input.exceptional_constraints:
        block = models.PlanningBlock(
            planning_id=planning.id,
            jour=ec.jour,
            heure_debut=ec.heure_debut,
            heure_fin=ec.heure_fin,
            titre=ec.nom,
            description=ec.description or "",
            categorie=models.ActivityCategory.IMPERATIF_EXCEPTIONNEL,
            ordre=0,
        )
        db.add(block)
        if ec.heure_debut and ec.heure_fin:
            occupied.append({
                "jour": ec.jour.isoformat(), "heure_debut": ec.heure_debut.strftime("%H:%M"),
                "heure_fin": ec.heure_fin.strftime("%H:%M"), "titre": ec.nom,
            })

    db.commit()
    return occupied


def compute_pause_slots(weekdays: list[date]) -> list[dict]:
    """
    Construit les créneaux de pause fixes (repas/coupures) pour chaque jour de la
    semaine, à partir de settings.pause_times. Ces créneaux ne sont PAS créés comme
    PlanningBlock (ce n'est pas une "activité"), mais servent de créneaux occupés
    à ne jamais utiliser lors de l'assemblage.
    """
    pauses = []
    duration = timedelta(minutes=settings.pause_duration_minutes)
    for raw_time in settings.pause_times.split(","):
        raw_time = raw_time.strip()
        if not raw_time:
            continue
        h, m = raw_time.split(":")
        start = time_cls(hour=int(h), minute=int(m))
        end_dt = datetime.combine(date.today(), start) + duration
        for jour in weekdays:
            pauses.append({
                "jour": jour.isoformat(),
                "heure_debut": start.strftime("%H:%M"),
                "heure_fin": end_dt.strftime("%H:%M"),
                "titre": "Pause (repas/coupure)",
            })
    return pauses


# ---------------------------------------------------------------------------
# 2. Sujets de la semaine : N jeux, N films/séries, projet(s) dev
# ---------------------------------------------------------------------------
def _pick_prioritized(items: list["models.MediaItem"], n: int) -> list["models.MediaItem"]:
    """
    Sélectionne jusqu'à `n` éléments dans `items`, en priorisant les "en_cours"
    (ils sortent en premier, mélangés aléatoirement entre eux), puis complète
    avec des "à_faire" (mélangés aléatoirement) si `n` n'est pas atteint.
    """
    if n <= 0 or not items:
        return []
    en_cours = [m for m in items if m.statut == models.MediaStatus.EN_COURS]
    autres = [m for m in items if m.statut != models.MediaStatus.EN_COURS]
    random.shuffle(en_cours)
    random.shuffle(autres)
    return (en_cours + autres)[:n]


def _pick_random(items: list["models.MediaItem"], n: int) -> list["models.MediaItem"]:
    """Sélectionne jusqu'à `n` éléments au hasard dans `items`, sans priorité."""
    if n <= 0 or not items:
        return []
    return random.sample(items, min(n, len(items)))


def _select_media_subjects(
    db: Session, media_types: list["models.MediaType"], mode: str, nombre: int, candidate_ids: list[int],
) -> list["models.MediaItem"]:
    """
    Sélectionne les MediaItem d'un type donné (jeu, ou film/série) pour la
    semaine, selon l'un des deux algorithmes déterministes :
    - "priorise" : dans toute la bibliothèque (hors "terminé"), en priorisant
      les éléments "en_cours" sur les "à_faire".
    - "aleatoire" : tirage sans priorité, uniquement parmi les IDs cochés par
      l'utilisateur (`candidate_ids`). Si l'utilisateur n'a rien coché, replie
      sur toute la bibliothèque (hors "terminé") pour ne pas bloquer la
      génération.
    """
    if mode == "aleatoire" and candidate_ids:
        pool = db.query(models.MediaItem).filter(models.MediaItem.id.in_(candidate_ids)).all()
        return _pick_random(pool, nombre)

    pool = (
        db.query(models.MediaItem)
        .filter(
            models.MediaItem.type.in_(media_types),
            models.MediaItem.statut != models.MediaStatus.TERMINE,
        )
        .all()
    )
    if mode == "aleatoire":
        return _pick_random(pool, nombre)
    return _pick_prioritized(pool, nombre)


def select_weekly_subjects(db: Session, weekly_input: models.WeeklyInput) -> dict:
    """
    Détermine les sujets de la semaine : jusqu'à `nombre_jeux` jeux, jusqu'à
    `nombre_films_series` films/séries, et un ou plusieurs sujets de projet dev
    (combinés en une seule idée de projet par l'IA).

    Aucune IA n'intervient dans cette sélection : c'est un algorithme
    déterministe en Python, avec deux stratégies au choix par catégorie (cf.
    `_select_media_subjects` pour jeu/film-série, et ci-dessous pour le dev).
    """
    jeu_ids = json.loads(weekly_input.loisir_jeu_media_ids or "[]")
    jeux = _select_media_subjects(
        db, [models.MediaType.JEU_VIDEO], weekly_input.jeu_mode or "priorise",
        weekly_input.nombre_jeux if weekly_input.nombre_jeux is not None else 1, jeu_ids,
    )

    film_serie_ids = json.loads(weekly_input.loisir_film_serie_media_ids or "[]")
    films_series = _select_media_subjects(
        db, [models.MediaType.FILM, models.MediaType.SERIE], weekly_input.film_serie_mode or "priorise",
        weekly_input.nombre_films_series if weekly_input.nombre_films_series is not None else 1, film_serie_ids,
    )

    # Projet dev : "aleatoire" = choix unique au hasard (priorité aux envies
    # d'apprentissage) ; "manuel" = utilise directement la sélection de
    # l'utilisateur, telle quelle (1 ou plusieurs sujets).
    projet_dev_ids = json.loads(weekly_input.projet_dev_skill_ids or "[]")
    if weekly_input.dev_mode == "manuel" and projet_dev_ids:
        projet_dev_skills = (
            db.query(models.SkillGoal).filter(models.SkillGoal.id.in_(projet_dev_ids)).all()
        )
    else:
        skill_goals = db.query(models.SkillGoal).all()
        envies = [s for s in skill_goals if s.est_envie_apprentissage]
        pool = envies or skill_goals
        projet_dev_skills = [random.choice(pool)] if pool else []

    return {"jeux": jeux, "films_series": films_series, "projet_dev_skills": projet_dev_skills}


# ---------------------------------------------------------------------------
# 3. Génération des activités de la semaine
#    - Loisir (jeu/film/série) : titre générique déterministe, PAS d'IA.
#    - Projet dev : un seul appel Ollama, combinant le(s) sujet(s) choisi(s)
#      en une seule idée de projet réalisable en une semaine.
# ---------------------------------------------------------------------------
DEV_SYSTEM_PROMPT = """Tu proposes UNE idée de projet de développement original et concret, \
adaptée au(x) sujet(s) et niveau(x) indiqués par l'utilsateur. \
Si plusieurs sujets sont donnés, propose UNE SEULE idée de projet qui les combine ou \
les articule ensemble, plutôt qu'une idée par sujet.

Si le message utilisateur contient une idée précise donnée par l'utilisateur, tu DOIS reprendre \
cette idée telle quelle (reformulée en titre/description), et NE PAS proposer autre chose à la place. \
Ne propose une idée de ton cru que si aucune idée précise n'est donnée.

Voila comment tu dois considérer les différents niveaux indiquables par l'utilisateur :
- Débutant: propose un projet simple pour apprendre la techno du sujet
- Intermédiaire: propose un projet complet permettant d'approfondire les connaisances sur le sujet
- Avancé: propose un projet complexe qui mets au défi les compétences sur le sujet

RÉPONDS UNIQUEMENT avec un objet JSON, sans texte ni Markdown avant ou après, avec \
EXACTEMENT ces clés :
- "titre" : nom court du projet (ex: "Rust - petit client TUI").
- "description" : 2 à 3 phrases décrivant concrètement le projet.
- "categorie" : toujours "projet_dev".

Ne donne jamais de description de projet trop générique. Sois précis dans les descriptions.
Exemple de réponse correcte pour du Rust, niveau débutant :
{"titre": "Rust - petit client TUI", "description": "Développer une application avec une interface dans le terminal en Rust pour consulter la météo avec l'API OpenMétéo.", "categorie": "projet_dev"}
"""


def build_loisir_activity(media_item: "models.MediaItem", notes_libres: str = "") -> dict:
    """
    Construit une activité de loisir SANS IA : titre générique déterministe
    ("Jouer à [Jeu]" / "Regarder [Film ou série]"), description reprenant la
    note libre de l'utilisateur si elle existe (sinon vide).

    Un FILM a une durée fixe et se regarde en une seule fois : `max_sessions`
    est forcé à 1 pour empêcher l'assemblage de fusionner plusieurs séances
    consécutives du même film pour remplir un créneau plus grand que sa durée
    (ce qui afficherait un bloc plus long que le film lui-même). Une SÉRIE ou
    un JEU gardent leur plafond habituel (`sessions_restantes`, illimité si
    None), puisqu'ils se consomment naturellement par séances répétées
    (épisodes, sessions de jeu).
    """
    verbe = "Jouer à" if media_item.type == models.MediaType.JEU_VIDEO else "Regarder"
    max_sessions = media_item.sessions_restantes
    if media_item.type == models.MediaType.FILM:
        max_sessions = 1 if max_sessions is None else min(max_sessions, 1)
    return {
        "titre": f"{verbe} {media_item.titre}",
        "description": notes_libres.strip(),
        "categorie": "loisir",
        "duree_minutes": media_item.duree_session_minutes or BLOCK_TARGET_MINUTES,
        "max_sessions": max_sessions,
    }


def build_dev_prompt(skills: list["models.SkillGoal"], notes_libres: str = "") -> str:
    lines = []
    if len(skills) == 1:
        s = skills[0]
        lines.append(f"Thème/sujet de projet dev imposé: {s.nom}")
        lines.append(f"Niveau de compétence sur ce sujet: {s.niveau_actuel or 'non précisé'}")
    else:
        lines.append("Sujets de projet dev imposés (à combiner en UNE seule idée):")
        for s in skills:
            lines.append(f"- {s.nom} (niveau: {s.niveau_actuel or 'non précisé'})")
    if notes_libres.strip():
        lines.append(
            f"Idée précise donnée par l'utilisateur pour cette semaine, à respecter en priorité "
            f"si elle est cohérente avec le(s) thème(s) ci-dessus: {notes_libres.strip()}"
        )
    lines.append("Propose une idée de projet concret et réalisable à ce niveau.")
    return "\n".join(lines)


def call_ollama(system_prompt: str, user_prompt: str) -> str:
    """Appelle l'API chat d'Ollama en mode JSON forcé, retourne le contenu brut."""
    url = f"{settings.ollama_base_url}/api/chat"
    payload = {
        "model": settings.ollama_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 1.2,   # plus littéral/déterministe, moins créatif sur le format
            "num_predict": 512,   # un seul objet JSON attendu par appel : pas besoin d'une
                                  # grande marge comme pour l'ancien prompt "semaine entière".
            "num_ctx": 4096,
        },
    }
    try:
        response = httpx.post(url, json=payload, timeout=120)
        response.raise_for_status()
    except httpx.ConnectError as exc:
        raise OllamaError(
            f"Impossible de joindre Ollama sur {settings.ollama_base_url}. "
            "Vérifie qu'Ollama tourne bien (`ollama serve`) et que l'URL est correcte."
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise OllamaError(f"Ollama a répondu une erreur HTTP {exc.response.status_code}: {exc.response.text}") from exc

    data = response.json()
    content = data.get("message", {}).get("content", "")
    if not content:
        raise OllamaError(f"Réponse Ollama vide ou inattendue: {data}")
    return content


def _try_repair_truncated_json(raw_content: str) -> Optional[list | dict]:
    """
    Tente de sauver une réponse JSON coupée en plein milieu (fenêtre de contexte
    dépassée, connexion interrompue...). Parcourt le texte en suivant une pile
    d'accolades/crochets ouverts, repère la position juste après le DERNIER objet
    "{...}" complet (à n'importe quelle profondeur d'imbrication), tronque là, puis
    referme proprement tous les crochets/accolades encore ouverts à ce point.
    """
    text = raw_content.strip()
    if not text or text[0] not in "[{":
        return None

    stack: list[str] = []
    in_string = False
    escape = False
    last_safe_cut = None  # (index du dernier caractère à garder, pile restante à cet instant)

    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if not stack:
                continue
            stack.pop()
            if ch == "}":
                last_safe_cut = (i, list(stack))

    if last_safe_cut is None:
        return None

    cut_index, remaining_stack = last_safe_cut
    closers = {"{": "}", "[": "]"}
    candidate = text[: cut_index + 1] + "".join(closers[o] for o in reversed(remaining_stack))
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def parse_single_activity(raw_content: str, expected_categorie: str) -> dict:
    """
    Parse la réponse Ollama pour UNE activité. Tolère qu'elle soit renvoyée
    dans une liste à un seul élément plutôt qu'en objet nu. La "categorie" est
    toujours forcée à `expected_categorie` (on sait déjà, avant l'appel, à
    quel type d'activité correspond ce prompt : pas besoin de faire confiance
    au LLM sur ce point).
    """
    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError:
        repaired = _try_repair_truncated_json(raw_content)
        if repaired is None:
            raise OllamaError(f"Réponse d'Ollama non-JSON ou tronquée: {raw_content[:500]}")
        logger.warning("Réponse Ollama tronquée, tentative de récupération partielle.")
        parsed = repaired

    if isinstance(parsed, list):
        if not parsed:
            raise OllamaError("Réponse Ollama: liste vide.")
        parsed = parsed[0]

    if not isinstance(parsed, dict):
        raise OllamaError(f"Format JSON inattendu (objet attendu): {parsed}")

    titre = parsed.get("titre")
    if not titre:
        raise OllamaError(f"Réponse Ollama incomplète (titre manquant): {parsed}")

    return {
        "titre": titre,
        "description": parsed.get("description", ""),
        "categorie": expected_categorie,
    }


def _generate_activity(system_prompt: str, user_prompt: str, expected_categorie: str, label: str) -> Optional[dict]:
    """
    Appelle Ollama pour un sujet donné et retourne l'activité générée, ou None
    si Ollama est injoignable / répond n'importe quoi (on ne fait pas planter
    toute la génération de la semaine pour un seul sujet raté).
    """
    try:
        raw = call_ollama(system_prompt, user_prompt)
        return parse_single_activity(raw, expected_categorie)
    except OllamaError as exc:
        logger.warning("Génération de l'activité '%s' échouée: %s", label, exc)
        return None


def generate_week_activities(
    subjects: dict,
    notes_jeu: str = "",
    notes_film_serie: str = "",
    notes_projet_dev: str = "",
) -> dict:
    """
    Construit les activités de la semaine à partir des sujets sélectionnés
    (aucune IA pour le loisir, un seul appel Ollama pour le projet dev).
    Retourne {"jeu": [...], "film_serie": [...], "projet_dev": [...]} (listes,
    potentiellement vides).

    - "duree_minutes" d'un loisir vient de `MediaItem.duree_session_minutes`.
    - "max_sessions" d'un loisir vient de `MediaItem.sessions_restantes` (None =
      pas de plafond), pour éviter de programmer plus de séances qu'il ne reste
      d'épisodes/contenu à consommer.
    - Chaque catégorie a sa PROPRE note libre (`notes_jeu` / `notes_film_serie`
      / `notes_projet_dev`, saisies dans des champs séparés du formulaire
      hebdomadaire).
    """
    jeux_activities = [build_loisir_activity(m, notes_jeu) for m in subjects.get("jeux", [])]
    films_series_activities = [build_loisir_activity(m, notes_film_serie) for m in subjects.get("films_series", [])]

    projet_dev_activities: list[dict] = []
    skills = subjects.get("projet_dev_skills") or []
    if skills:
        label = " + ".join(s.nom for s in skills)
        activity = _generate_activity(
            DEV_SYSTEM_PROMPT,
            build_dev_prompt(skills, notes_projet_dev),
            "projet_dev",
            f"projet dev: {label}",
        )
        if activity:
            activity["duree_minutes"] = BLOCK_TARGET_MINUTES
            activity["max_sessions"] = None
            projet_dev_activities.append(activity)

    return {"jeu": jeux_activities, "film_serie": films_series_activities, "projet_dev": projet_dev_activities}


# ---------------------------------------------------------------------------
# 4. Assemblage déterministe du planning (répartition sur les créneaux libres)
# ---------------------------------------------------------------------------
def _parse_time(value: Optional[str]) -> Optional[time_cls]:
    if not value:
        return None
    try:
        h, m = value.split(":")
        return time_cls(hour=int(h), minute=int(m))
    except (ValueError, AttributeError):
        return None


def _compute_free_intervals(jour: date, occupied_by_day: dict[str, list[tuple[time_cls, time_cls]]]) -> list[tuple[time_cls, time_cls]]:
    """
    Calcule les créneaux libres d'une journée (entre day_start_time et
    day_end_time), en retirant les plages déjà occupées (impératifs + pauses).
    """
    day_start = _parse_time(settings.day_start_time) or time_cls(9, 0)
    day_end = _parse_time(settings.day_end_time) or time_cls(22, 0)

    ranges = sorted(occupied_by_day.get(jour.isoformat(), []), key=lambda r: r[0])
    free: list[tuple[time_cls, time_cls]] = []
    cursor = day_start
    for start, end in ranges:
        if start > cursor:
            free.append((cursor, start))
        if end > cursor:
            cursor = end
    if cursor < day_end:
        free.append((cursor, day_end))
    return free


def _next_block_length(
    remaining_minutes: float, target_minutes: int, min_minutes: int = BLOCK_MIN_MINUTES, allow_absorb: bool = True,
) -> float:
    """
    Détermine la durée du prochain bloc à poser dans un créneau libre restant :
    la durée cible de l'activité, sauf s'il ne reste pas assez de temps après
    coup pour un autre bloc exploitable (min_minutes) — auquel cas on absorbe
    le reliquat entier plutôt que de laisser un micro-créneau inutilisé.

    `allow_absorb=False` désactive cette absorption du petit reliquat (utilisé
    pour les activités à séance unique, ex: un film, dont la durée affichée ne
    doit jamais dépasser sa durée réelle même s'il reste quelques minutes
    après coup dans le créneau).
    """
    if remaining_minutes >= target_minutes + min_minutes:
        return target_minutes
    if allow_absorb:
        return remaining_minutes
    return min(remaining_minutes, target_minutes)


def _pick_next_available(
    activity_list: list[dict],
    rotation: int,
    session_counts: dict[str, int],
    remaining_minutes: Optional[float] = None,
) -> tuple[Optional[dict], int]:
    """
    Cherche, à partir de `rotation`, la prochaine activité de `activity_list` qui
    n'a pas encore atteint son plafond de séances ("max_sessions").

    Si `remaining_minutes` est fourni (temps encore disponible dans le créneau
    en cours), priorise une activité dont la durée cible ("duree_minutes")
    tient entièrement dans ce temps restant — pour éviter de tronquer par
    exemple un film de 2h dans un reliquat de 1h30 alors qu'une autre activité
    plus courte (ou le même film à une autre heure de la semaine) y tiendrait
    mieux. Si aucune activité ne tient entièrement, on retombe sur la première
    disponible (comportement précédent : mieux vaut une séance tronquée qu'un
    créneau vide).

    Retourne (activité, nouvelle valeur de rotation à utiliser pour le
    prochain appel), ou (None, rotation) si toutes les activités sont épuisées.
    """
    n = len(activity_list)

    if remaining_minutes is not None:
        for offset in range(n):
            idx = (rotation + offset) % n
            candidate = activity_list[idx]
            cap = candidate.get("max_sessions")
            if cap is not None and session_counts.get(candidate["titre"], 0) >= cap:
                continue
            target = candidate.get("duree_minutes") or BLOCK_TARGET_MINUTES
            if target <= remaining_minutes:
                return candidate, idx + 1

    for offset in range(n):
        idx = (rotation + offset) % n
        candidate = activity_list[idx]
        cap = candidate.get("max_sessions")
        if cap is None or session_counts.get(candidate["titre"], 0) < cap:
            return candidate, idx + 1
    return None, rotation


def assemble_weekly_blocks(weekdays: list[date], occupied: list[dict], activities: dict) -> list[dict]:
    """
    Répartit les (au plus 3) activités de la semaine — jeu, film/série, projet
    dev — sur tous les créneaux libres de la semaine :
    - Pour chaque créneau libre, UNE SEULE activité (celle désignée par la
      rotation) le remplit avec autant de séances consécutives que sa durée le
      permet ; ces séances contiguës sont FUSIONNÉES en un seul bloc affiché
      (ex: deux séances de 45 min à la suite -> un unique bloc de 1h30),
      plutôt que d'apparaître comme plusieurs blocs juxtaposés.
    - Si cette activité atteint son plafond de séances ("max_sessions", ex:
      nombre d'épisodes restants) avant la fin du créneau, le reste du créneau
      est proposé à l'activité suivante de la rotation (toujours groupée).
    - La rotation avance d'un créneau à l'autre (et d'un jour à l'autre), ce
      qui alterne naturellement les activités sur la semaine sans les mélanger
      au sein d'un même créneau.
    - Chaque fois qu'un même projet dev revient, sa description reçoit une
      mention d'étape ("Étape N") pour simuler une progression.
    - S'il n'y a qu'un seul sujet disponible (ou que les autres sont épuisés),
      il occupe les créneaux libres restants plutôt que de les laisser vides.

    Ne touche pas à la base de données : retourne une liste de dicts prêts à
    être transformés en PlanningBlock par l'appelant.
    """
    activity_list = [
        a for a in (
            activities.get("jeu", []) + activities.get("film_serie", []) + activities.get("projet_dev", [])
        ) if a
    ]
    if not activity_list:
        return []

    occupied_by_day: dict[str, list[tuple[time_cls, time_cls]]] = {}
    for o in occupied:
        start, end = _parse_time(o.get("heure_debut")), _parse_time(o.get("heure_fin"))
        if start is not None and end is not None:
            occupied_by_day.setdefault(o["jour"], []).append((start, end))

    result: list[dict] = []
    rotation = 0
    session_counts: dict[str, int] = {}
    dev_step_counts: dict[str, int] = {}

    for jour in weekdays:
        free_intervals = _compute_free_intervals(jour, occupied_by_day)

        for interval_start, interval_end in free_intervals:
            anchor = date.today()
            cursor = datetime.combine(anchor, interval_start)
            end_dt = datetime.combine(anchor, interval_end)

            # Remplit ce créneau par groupes d'activité successifs (au moins un,
            # potentiellement plusieurs si la première activité du groupe
            # atteint son plafond avant la fin du créneau).
            while (end_dt - cursor).total_seconds() / 60 >= BLOCK_MIN_MINUTES:
                remaining_in_interval = (end_dt - cursor).total_seconds() / 60
                activity, rotation = _pick_next_available(activity_list, rotation, session_counts, remaining_in_interval)
                if activity is None:
                    break  # toutes les activités ont atteint leur plafond

                target = activity.get("duree_minutes") or BLOCK_TARGET_MINUTES
                titre = activity["titre"]
                cap = activity.get("max_sessions")

                # Enchaîne les séances consécutives de CETTE activité (sans
                # créer un bloc par séance) : on avance juste le curseur et on
                # compte les séances consommées, pour ne créer QU'UN SEUL bloc
                # fusionné à la fin de tout le groupe contigu.
                run_start = cursor
                sessions_in_run = 0
                while (end_dt - cursor).total_seconds() / 60 >= BLOCK_MIN_MINUTES and (
                    cap is None or session_counts.get(titre, 0) < cap
                ):
                    remaining_minutes = (end_dt - cursor).total_seconds() / 60
                    length = _next_block_length(remaining_minutes, target, allow_absorb=(cap != 1))
                    cursor = cursor + timedelta(minutes=length)
                    session_counts[titre] = session_counts.get(titre, 0) + 1
                    sessions_in_run += 1

                if sessions_in_run == 0:
                    break  # sécurité : ne devrait pas arriver (cap déjà à 0 dès le départ)

                description = activity["description"]
                if activity["categorie"] == "projet_dev":
                    dev_step_counts[titre] = dev_step_counts.get(titre, 0) + 1
                    step = dev_step_counts[titre]
                    if step > 1:
                        description = f"{description} (Étape {step})"

                result.append({
                    "jour": jour.isoformat(),
                    "heure_debut": run_start.time().strftime("%H:%M"),
                    "heure_fin": cursor.time().strftime("%H:%M"),
                    "titre": titre,
                    "description": description,
                    "categorie": activity["categorie"],
                })

    return result


# ---------------------------------------------------------------------------
# 5. Orchestration
# ---------------------------------------------------------------------------
def _run_generation(db: Session, planning: models.GeneratedPlanning, weekly_input: models.WeeklyInput, occupied: list[dict]) -> None:
    """
    Logique commune à la génération initiale et à la régénération :
    détermine les sujets de la semaine (sélection manuelle ou auto), appelle
    Ollama pour chacun, assemble le planning, puis insère les blocs obtenus.
    Attache un avertissement transitoire (`planning.generation_warning`) en
    cas de souci partiel ou total.
    """
    weekdays = [weekly_input.semaine_du + timedelta(days=i) for i in range(5)]
    planning.generation_warning = None

    subjects = select_weekly_subjects(db, weekly_input)
    activities = generate_week_activities(
        subjects,
        notes_jeu=weekly_input.notes_jeu or "",
        notes_film_serie=weekly_input.notes_film_serie or "",
        notes_projet_dev=weekly_input.notes_projet_dev or "",
    )
    blocks = assemble_weekly_blocks(weekdays, occupied, activities)

    for i, b in enumerate(blocks):
        db.add(models.PlanningBlock(
            planning_id=planning.id,
            ordre=i,
            jour=date.fromisoformat(b["jour"]),
            heure_debut=_parse_time(b["heure_debut"]),
            heure_fin=_parse_time(b["heure_fin"]),
            titre=b["titre"],
            description=b["description"],
            categorie=models.ActivityCategory(b["categorie"]),
        ))
    db.commit()
    db.refresh(planning)

    subject_to_activity_key = {"jeux": "jeu", "films_series": "film_serie", "projet_dev_skills": "projet_dev"}
    labels = {"jeux": "jeu", "films_series": "film/série", "projet_dev_skills": "projet dev"}
    # Nombre voulu par catégorie : 0 = l'utilisateur ne veut explicitement rien
    # cette semaine dans cette catégorie, donc pas d'avertissement à générer.
    nombres_voulus = {
        "jeux": weekly_input.nombre_jeux if weekly_input.nombre_jeux is not None else 1,
        "films_series": weekly_input.nombre_films_series if weekly_input.nombre_films_series is not None else 1,
        "projet_dev_skills": 1,
    }
    warnings = []
    for key, label in labels.items():
        activity_key = subject_to_activity_key[key]
        if nombres_voulus[key] <= 0:
            continue
        if subjects.get(key) and not activities.get(activity_key):
            warnings.append(f"Génération IA du {label} échouée (Ollama indisponible ou réponse invalide).")
        elif not subjects.get(key):
            warnings.append(f"Aucun {label} disponible en base pour cette semaine.")
    if not blocks:
        warnings.insert(0, "Aucune activité n'a pu être placée cette semaine.")
    if warnings:
        planning.generation_warning = " ".join(warnings)
        db.commit()


def generate_planning(db: Session, weekly_input: models.WeeklyInput) -> models.GeneratedPlanning:
    """
    Crée le planning complet :
    1. blocs déterministes (impératifs)
    2. sélection des sujets + génération LLM par sujet + assemblage déterministe
    En cas d'échec du LLM, le planning est quand même créé (avec les impératifs seuls)
    et un avertissement transitoire est attaché à l'objet retourné.
    """
    planning = models.GeneratedPlanning(
        weekly_input_id=weekly_input.id,
        statut=models.PlanningStatus.BROUILLON,
    )
    db.add(planning)
    db.commit()
    db.refresh(planning)

    occupied = insert_deterministic_blocks(db, planning, weekly_input)
    weekdays = [weekly_input.semaine_du + timedelta(days=i) for i in range(5)]
    occupied += compute_pause_slots(weekdays)

    _run_generation(db, planning, weekly_input, occupied)
    return planning


def regenerate_llm_blocks(db: Session, planning: models.GeneratedPlanning) -> models.GeneratedPlanning:
    """
    Supprime les blocs loisir/projet_dev existants (garde les impératifs intacts),
    retire un nouveau tirage de sujets, et relance la génération + l'assemblage.
    """
    weekly_input = planning.weekly_input

    for block in list(planning.blocks):
        if block.categorie in (models.ActivityCategory.LOISIR, models.ActivityCategory.PROJET_DEV):
            db.delete(block)
    db.commit()
    db.refresh(planning)

    weekdays = [weekly_input.semaine_du + timedelta(days=i) for i in range(5)]
    occupied = [
        {
            "jour": b.jour.isoformat(),
            "heure_debut": b.heure_debut.strftime("%H:%M"),
            "heure_fin": b.heure_fin.strftime("%H:%M"),
            "titre": b.titre,
        }
        for b in planning.blocks if b.heure_debut and b.heure_fin
    ]
    occupied += compute_pause_slots(weekdays)

    _run_generation(db, planning, weekly_input, occupied)
    return planning
