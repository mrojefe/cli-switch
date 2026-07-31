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


class DaemonRestartFailed(CliSwitchError):
    """Le switch a réussi (credentials + identité écrits sur disque), mais le
    démon Claude relancé n'a pas pu être confirmé sain avant le timeout —
    p.ex. resté en `auth_required` malgré des tokens valides sur disque
    (course de démarrage, cf. switcher.restart_claude_daemon_and_verify).
    Non fatal : un `claude daemon status` / nouvelle commande finira par
    déclencher un nouveau démon sain, mais sans la garantie qu'on visait."""
