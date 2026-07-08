"""Exceptions partagées entre les modules claude/ et agy/."""


class CliSwitchError(Exception):
    """Erreur de base pour cli-switch."""


class ProfileNotFoundError(CliSwitchError):
    pass


class NoCredentialsError(CliSwitchError):
    """Aucun credential trouvé là où on l'attendait (pas encore loggé)."""


class LiveProcessConflict(CliSwitchError):
    """Un process vivant possède le compte actuel — action refusée sans
    confirmation explicite."""
