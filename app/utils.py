from datetime import date, timedelta

WEEKDAY_NAMES_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]


def next_monday(today: date | None = None) -> date:
    """Retourne le lundi de la semaine prochaine (ou celui de la semaine en cours si on est déjà lundi)."""
    today = today or date.today()
    days_ahead = (7 - today.weekday()) % 7
    days_ahead = days_ahead or 7  # si on est lundi, on vise le lundi suivant
    return today + timedelta(days=days_ahead)


def week_dates(monday: date) -> list[date]:
    return [monday + timedelta(days=i) for i in range(5)]
