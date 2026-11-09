"""Interface en ligne de commande de cli-switch."""

from __future__ import annotations

import typer

from cli_switch.agy import credentials as agy_credentials
from cli_switch.agy import switcher as agy_switcher
from cli_switch.claude import credentials as claude_credentials
from cli_switch.claude import switcher as claude_switcher
from cli_switch.codex import credentials as codex_credentials
from cli_switch.codex import switcher as codex_switcher
from cli_switch.exceptions import CliSwitchError, DaemonRestartFailed, LiveProcessConflict
from cli_switch.locking import atomic_copy, cli_switch_lock
from cli_switch.paths import (
    agy_google_accounts_path,
    agy_live_token_path,
    agy_oauth_creds_path,
)
from cli_switch.store import Store

# `-h` en plus de `--help` partout (sans ça, seul --help marche, ce qui
# surprend puisqu'un mot nu est interprété comme un nom de profil).
_HELP_OPTS = {"help_option_names": ["-h", "--help"]}

app = typer.Typer(
    help="Bascule entre plusieurs comptes Claude Code, Antigravity (AGY) et OpenAI Codex.",
    epilog=(
        "Raccourcis : `cli-switch <profil>` = switch ; `ls` = list ; "
        "`claude|agy|codex ls` = list filtré ; `claude|agy|codex <profil>` = switch de la famille. "
        "Alias shell : cs, csa (agy), cscl (claude), csco (codex) ; suffixe s = switch (csas, cscls, cscos)."
    ),
    no_args_is_help=True,
    context_settings=_HELP_OPTS,
)
claude_app = typer.Typer(help="Commandes spécifiques à Claude Code.", context_settings=_HELP_OPTS)
agy_app = typer.Typer(help="Commandes spécifiques à AGY (Antigravity).", context_settings=_HELP_OPTS)
agy_proxy_app = typer.Typer(
    help="Proxy AGY multi-compte (hot-swap de toutes les sessions).",
    context_settings=_HELP_OPTS,
)
codex_app = typer.Typer(help="Commandes spécifiques à OpenAI Codex.", context_settings=_HELP_OPTS)
instance_app = typer.Typer(
    help="Gestion des environnements d'instance isolés (multi-comptes).",
    context_settings=_HELP_OPTS,
)
app.add_typer(claude_app, name="claude")
app.add_typer(agy_app, name="agy")
app.add_typer(codex_app, name="codex")
app.add_typer(instance_app, name="instance")
agy_app.add_typer(agy_proxy_app, name="proxy")




def _store() -> Store:
    return Store()


def _fail(msg: str) -> None:
    typer.secho(f"✖ {msg}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _ok(msg: str) -> None:
    typer.secho(f"✔ {msg}", fg=typer.colors.GREEN)


def _info(msg: str) -> None:
    typer.secho(f"ℹ {msg}", fg=typer.colors.CYAN)


def _warn(msg: str) -> None:
    typer.secho(f"⚠ {msg}", fg=typer.colors.YELLOW, err=True)


def _warn_duplicate_claude(store: Store, name: str, email: str | None) -> None:
    """Signale (SANS rien supprimer) qu'un autre profil porte déjà ce compte
    Claude. Remplace l'ancien dédoublonnage destructif qui effaçait le profil
    en double (et a fait perdre jfe le 2026-07-08)."""
    if not email:
        return
    dupes = [
        p
        for p in store.list_profiles()
        if p != name
        and store.profile(p).claude_email.exists()
        and store.profile(p).claude_email.read_text(encoding="utf-8").strip() == email
    ]
    if dupes:
        _warn(
            f"Le compte {email} est aussi enregistré sous : {', '.join(dupes)}. "
            "Rien n'a été supprimé — retire un doublon avec "
            "`cli-switch claude rm <nom>` si tu veux."
        )


# ── claude login/switch ──────────────────────────────────────────────────


@claude_app.command("login")
def claude_login(
    name: str,
    current: bool = typer.Option(
        False,
        "--current",
        "-c",
        help="Capturer le compte DÉJÀ connecté sans relancer `claude auth login`.",
    ),
) -> None:
    """Connecte un NOUVEAU compte Claude (`claude auth login`) et l'enregistre
    sous le profil <name> — comme l'ancien claude-switch.

    Avec --current : capture plutôt le compte déjà chargé (utile pour
    ré-enregistrer le token frais de ta session en cours)."""
    store = _store()
    if not current:
        _info(
            "Le navigateur va s'ouvrir (ou un lien sera affiché). "
            "Connecte-toi avec le bon compte, puis copie le CODE de l'URL de callback."
        )
        _info(
            "⚠ Quand 'Paste code here if prompted >' apparaît : "
            "COLLE le code et appuie sur Entrée. "
            "La saisie est INVISIBLE (masquée comme un mot de passe) — c'est normal."
        )
        try:
            claude_switcher.run_claude_auth_login()
        except RuntimeError as e:
            _fail(str(e))
            return
    try:
        email = claude_switcher.login(store, name)
    except CliSwitchError as e:
        _fail(str(e))
        return
    _ok(f"Profil Claude '{name}' enregistré ({email or 'email inconnu'}).")
    _warn_duplicate_claude(store, name, email)


@claude_app.command("current")
def claude_current() -> None:
    """Affiche le profil Claude actuellement actif."""
    store = _store()
    typer.echo(f"Claude : {store.current_claude() or '(aucun)'}")


@claude_app.command("add")
def claude_add(
    name: str,
    current: bool = typer.Option(
        False,
        "--current",
        "-c",
        help="Capturer le compte DÉJÀ connecté sans relancer `claude auth login`.",
    ),
) -> None:
    """Connecte un NOUVEAU compte Claude (`claude auth login`), l'enregistre
    sous <name>, puis RESTAURE le compte actif d'avant — comme l'ancien
    claude-switch `add`.

    Avec --current : capture le compte déjà chargé sous <name> sans relancer de
    connexion (et reste dessus)."""
    store = _store()
    active_before = store.current_claude()
    if not current:
        try:
            claude_switcher.run_claude_auth_login()
        except RuntimeError as e:
            _fail(str(e))
            return
    try:
        email = claude_switcher.login(store, name)
    except CliSwitchError as e:
        _fail(str(e))
        return
    # Restaurer l'ancien actif s'il a des credentials Claude. login() ne
    # supprime plus jamais un autre profil (cf. switcher), donc l'ancien actif
    # garde les siens — la restauration fonctionne et on ne perd pas jfe.
    can_restore = (
        active_before
        and active_before != name
        and store.profile(active_before).claude_credentials.exists()
    )
    if can_restore:
        try:
            claude_switcher.switch_to(store, active_before, force=True)
            _ok(f"Profil Claude '{name}' enregistré ; compte précédent '{active_before}' restauré comme actif.")
            _warn_duplicate_claude(store, name, email)
            return
        except CliSwitchError as e:
            _warn(f"Profil '{name}' enregistré, mais restauration de '{active_before}' impossible : {e}")
            return
    _ok(f"Profil Claude '{name}' enregistré et actif.")
    _warn_duplicate_claude(store, name, email)


@claude_app.command("sync")
def claude_sync(
    name: str | None = typer.Argument(None, help="Nom du profil (par défaut: profil Claude actif ou email)."),
) -> None:
    """Capture et sauvegarde la session Claude actuellement connectée sans redemander de login."""
    store = _store()
    target = name or store.current_claude()
    if not target:
        email = claude_credentials.current_claude_email()
        if email:
            target = email.split("@")[0].replace(".", "_")
        else:
            _fail("Aucun profil Claude actif et impossible de détecter l'email courant.")
            return
    try:
        email = claude_switcher.login(store, target)
        store.set_current_claude(target)
        _ok(f"Session Claude active enregistrée dans '{target}' ({email}).")
    except CliSwitchError as e:
        _fail(str(e))


@claude_app.command("rm")
def claude_rm(name: str, yes: bool = typer.Option(False, "-y", "--yes")) -> None:
    """Supprime uniquement la partie Claude d'un profil (garde AGY intact)."""
    store = _store()
    if not store.profile_exists(name) or not store.profile(name).claude_credentials.exists():
        _fail(f"Profil Claude '{name}' introuvable.")
        return
    if not yes and not typer.confirm(f"Supprimer la partie Claude du profil '{name}' ?", default=False):
        raise typer.Exit(code=1)
    store.clear_claude_side(name)
    if store.current_claude() == name:
        store.current_claude_path.unlink(missing_ok=True)
    store.remove_profile_if_empty(name)
    _ok(f"Claude retiré du profil '{name}'.")


@claude_app.command("clear")
def claude_clear_all(yes: bool = typer.Option(False, "-y", "--yes")) -> None:
    """Efface les credentials Claude de TOUS les profils (garde AGY intact)
    ET remet à zéro les fichiers live de Claude Code (~/.claude/.credentials.json,
    ~/.claude.json) pour repartir d'un état vierge."""
    import json
    from cli_switch.paths import claude_credentials_path, claude_global_config_path

    store = _store()
    profiles = [n for n in store.list_profiles() if store.profile(n).claude_credentials.exists()]
    if not yes and not typer.confirm(
        f"⚠ Effacer les credentials Claude de {len(profiles)} profil(s) + fichiers live ? (AGY conservé)",
        default=False,
    ):
        raise typer.Exit(code=1)

    # 1. Profils stockés
    for name in profiles:
        store.clear_claude_side(name)
        store.remove_profile_if_empty(name)
    store.current_claude_path.unlink(missing_ok=True)

    # 2. Fichier live ~/.claude/.credentials.json → structure vide valide
    live_creds = claude_credentials_path()
    if live_creds.exists():
        empty = {"claudeAiOauth": {"accessToken": "", "refreshToken": "", "expiresAt": 0,
                                    "scopes": [], "subscriptionType": ""}}
        live_creds.write_text(json.dumps(empty, indent=2), encoding="utf-8")
        _info(f"Fichier live réinitialisé : {live_creds}")

    # 3. ~/.claude.json : on vide UNIQUEMENT oauthAccount (ne PAS supprimer
    #    ce fichier — il contient l'état des projets/onboarding Claude et
    #    sa suppression casse `claude auth login`).
    global_cfg = claude_global_config_path()
    if global_cfg.exists():
        import json as _j
        try:
            _d = _j.loads(global_cfg.read_text(encoding="utf-8"))
            _d.pop("oauthAccount", None)
            global_cfg.write_text(_j.dumps(_d, indent=2), encoding="utf-8")
            _info(f"oauthAccount effacé dans : {global_cfg} (fichier conservé)")
        except Exception:
            pass

    if profiles:
        _ok(f"Credentials Claude effacés sur {len(profiles)} profil(s) + fichiers live réinitialisés.")
    else:
        _ok("Fichiers live Claude réinitialisés (aucun profil stocké à effacer).")


@claude_app.command("switch")
def claude_switch_cmd(name: str) -> None:
    """Bascule Claude vers le profil <name>."""
    store = _store()
    has_identity = store.profile(name).claude_oauth_account.exists()
    try:
        warning = claude_switcher.switch_to(store, name)
    except LiveProcessConflict:
        # Ne devrait plus arriver (switcher auto-force) mais au cas où
        warning = claude_switcher.switch_to(store, name, force=True)
    except DaemonRestartFailed as e:
        # Non fatal : les credentials ont bien basculé, seule la vérification
        # du redémarrage du démon a expiré. Le switch est réel.
        warning = None
        _warn(str(e))
    except CliSwitchError as e:
        _fail(str(e))
        return
    if warning:
        _info(f"Sessions actives détectées — daemon stoppé, switch effectué sur '{name}'.")
    _ok(f"Claude → profil '{name}' actif.")
    if not has_identity:
        _warn(
            f"Le profil '{name}' n'a pas d'identité enregistrée (profil migré). "
            "Claude peut demander une connexion. Fais-le UNE fois via "
            f"`claude` (login), puis `cli-switch claude login {name}` pour "
            "mémoriser l'identité — les switchs suivants seront transparents."
        )


# ── agy login/switch ─────────────────────────────────────────────────────


@agy_app.command("login")
def agy_login(name: str) -> None:
    """Enregistre un nouveau compte AGY sous le profil <name>."""
    store = _store()
    session = agy_switcher.begin_login(name)
    _info(f"Connexion AGY pour '{name}'...")
    if session.evicted:
        _info("Session AGY active (token + refresh legacy) mise de côté temporairement.")
    _info(
        "Lancez `agy` (sans argument) dans un autre terminal. Il affichera "
        "d'abord 'not signed in' sans rien demander de plus — tapez ensuite "
        "n'importe quel message au prompt pour déclencher l'authentification, "
        "un lien devrait alors s'afficher. Connectez-vous avec le bon compte."
    )
    typer.prompt("Appuyez sur [Entrée] une fois connecté", default="", show_default=False)
    try:
        email = agy_switcher.complete_login(store, session)
    except CliSwitchError as e:
        _fail(str(e))
        return
    _ok(f"Profil AGY '{name}' enregistré ({email or 'email inconnu'}).")


@agy_app.command("current")
def agy_current() -> None:
    """Affiche le profil AGY actuellement actif."""
    store = _store()
    typer.echo(f"AGY : {store.current_agy() or '(aucun)'}")


@agy_app.command("add")
def agy_add(name: str) -> None:
    """Enregistre un nouveau compte AGY sous <name> SANS changer le compte
    actuellement actif (restaure l'ancien après capture)."""
    store = _store()
    active_before = store.current_agy()
    session = agy_switcher.begin_login(name)
    _info(f"Connexion AGY pour '{name}'...")
    if session.evicted:
        _info("Session AGY active (token + refresh legacy) mise de côté temporairement.")
    _info(
        "Lancez `agy` (sans argument) dans un autre terminal, tapez un message pour "
        "déclencher l'authentification, connectez-vous avec le bon compte."
    )
    typer.prompt("Appuyez sur [Entrée] une fois connecté", default="", show_default=False)
    try:
        agy_switcher.complete_login(store, session)
    except CliSwitchError as e:
        _fail(str(e))
        return
    if active_before and active_before != name and store.profile_exists(active_before):
        agy_switcher.switch_to(store, active_before)
        _ok(f"Profil AGY '{name}' enregistré ; compte précédent '{active_before}' restauré comme actif.")
    else:
        _ok(f"Profil AGY '{name}' enregistré et actif.")


@agy_app.command("sync")
def agy_sync(
    name: str | None = typer.Argument(None, help="Nom du profil (par défaut: profil AGY actif ou email)."),
) -> None:
    """Capture et sauvegarde la session AGY actuellement connectée sans redemander de login."""
    store = _store()
    target = name or store.current_agy()
    if not target:
        email = agy_credentials.current_agy_email()
        if email:
            target = email.split("@")[0].replace(".", "_")
        else:
            _fail("Aucun profil AGY actif et impossible de détecter l'email courant.")
            return

    kt = agy_credentials.get_keyring_token()
    live_path = agy_live_token_path()
    if not kt and not live_path.exists():
        _fail("Aucune session AGY active trouvée (ni dans le trousseau ni sur le disque).")
        return

    email = agy_credentials.current_agy_email()
    with cli_switch_lock(store.lock_path):
        profile = store.profile(target)
        profile.root.mkdir(parents=True, exist_ok=True)
        if kt:
            profile.agy_live_token.write_text(kt, encoding="utf-8")
        elif live_path.exists():
            atomic_copy(live_path, profile.agy_live_token)
        if agy_google_accounts_path().exists():
            atomic_copy(agy_google_accounts_path(), profile.agy_google_accounts)
        if agy_oauth_creds_path().exists():
            atomic_copy(agy_oauth_creds_path(), profile.agy_oauth_creds)
        if email:
            profile.agy_email.write_text(email, encoding="utf-8")
        store.set_current_agy(target)

    _ok(f"Session AGY active enregistrée dans '{target}' ({email or 'inconnu'}).")


@agy_app.command("rm")
def agy_rm(name: str, yes: bool = typer.Option(False, "-y", "--yes")) -> None:
    """Supprime uniquement la partie AGY d'un profil (garde Claude intact)."""
    store = _store()
    if not store.profile_exists(name) or not store.profile(name).agy_live_token.exists():
        _fail(f"Profil AGY '{name}' introuvable.")
        return
    if not yes and not typer.confirm(f"Supprimer la partie AGY du profil '{name}' ?", default=False):
        raise typer.Exit(code=1)
    store.clear_agy_side(name)
    if store.current_agy() == name:
        store.current_agy_path.unlink(missing_ok=True)
    store.remove_profile_if_empty(name)
    _ok(f"AGY retiré du profil '{name}'.")


@agy_app.command("clear")
def agy_clear_all(yes: bool = typer.Option(False, "-y", "--yes")) -> None:
    """Efface les credentials AGY de TOUS les profils (garde Claude intact)
    ET remet à zéro les fichiers live d'AGY (~/.gemini/antigravity-cli/antigravity-oauth-token,
    ~/.gemini/google_accounts.json) pour repartir d'un état vierge."""
    from cli_switch.paths import agy_live_token_path, agy_google_accounts_path

    store = _store()
    profiles = [n for n in store.list_profiles() if store.profile(n).agy_live_token.exists()]
    if not yes and not typer.confirm(
        f"⚠ Effacer les credentials AGY de {len(profiles)} profil(s) + fichiers live ? (Claude conservé)",
        default=False,
    ):
        raise typer.Exit(code=1)

    # 1. Profils stockés
    for name in profiles:
        store.clear_agy_side(name)
        store.remove_profile_if_empty(name)
    store.current_agy_path.unlink(missing_ok=True)

    # 2. Token live AGY (CLI + Desktop) → supprimé (AGY le recrée au prochain login)
    live_tok = agy_live_token_path()
    if live_tok.exists():
        live_tok.unlink()
        _info(f"Token live supprimé : {live_tok}")
    agy_credentials.clear_live_token_files()
    agy_credentials.clear_keyring_token()

    # 3. google_accounts.json → supprimé
    g_accounts = agy_google_accounts_path()
    if g_accounts.exists():
        g_accounts.unlink()
        _info(f"Fichier Google accounts supprimé : {g_accounts}")

    if profiles:
        _ok(f"Credentials AGY effacés sur {len(profiles)} profil(s) + fichiers live réinitialisés.")
    else:
        _ok("Fichiers live AGY réinitialisés (aucun profil stocké à effacer).")


@agy_app.command("switch")
def agy_switch_cmd(
    name: str, kill: bool = typer.Option(False, "--kill", help="Tue les process agy actifs avant de switcher.")
) -> None:
    """Bascule AGY vers le profil <name> (commande explicite, alternative à `agy <name>`)."""
    _agy_switch_impl(name, kill)


def _agy_switch_impl(name: str, kill: bool) -> None:
    store = _store()
    from cli_switch.agy import process_detection

    agy_running = process_detection.any_agy_running()
    try:
        killed = agy_switcher.switch_to(store, name, kill_running=kill)
    except CliSwitchError as e:
        _fail(str(e))
        return
    if killed:
        _info(f"Process agy tués : {', '.join(map(str, killed))}")
    _ok(f"AGY → profil '{name}' actif (token écrit sur le disque).")
    restarted_ls = list(agy_switcher.LAST_RESTARTED_DESKTOP_PIDS)
    if restarted_ls:
        _info(
            f"Antigravity Desktop language_server rechargé à chaud (PID {', '.join(map(str, restarted_ls))})."
        )

    # Rappel du mécanisme réel (cf. MECHANISM_AGY.md) : un process `agy` déjà
    # lancé garde son compte en mémoire — le swap disque ne l'affecte pas.
    # La façon de continuer sur le nouveau compte SANS perdre le contexte et
    # SANS re-login navigateur : lancer un process neuf qui reprend la
    # conversation. `agy` persiste les conversations et sait les reprendre.
    if agy_running and not kill:
        _info(
            "Un process `agy` tourne déjà : il garde son ancien compte. Pour "
            "continuer sur le nouveau compte en gardant ta conversation, ouvre "
            "un NOUVEAU terminal et lance :  " + typer.style("agy --continue", bold=True) + "\n"
            "  (reprend la dernière conversation, sur le compte que tu viens de "
            "sélectionner, sans navigateur). Voir MECHANISM_AGY.md pour le détail."
        )


# ── proxy AGY multi-compte (hot-swap de toutes les sessions) ──────────────

from cli_switch.agy import proxy as agy_proxy_mod  # noqa: E402


def _proxy_pidfile(store: Store):
    return store.data_dir / "agy_proxy.pid"


def _proxy_port_file(store: Store):
    return store.data_dir / "agy_proxy.port"


def _proxy_running_pid(store: Store) -> int | None:
    pf = _proxy_pidfile(store)
    if not pf.exists():
        return None
    try:
        pid = int(pf.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None
    from cli_switch.claude.process_detection import is_pid_alive

    return pid if is_pid_alive(pid) else None


@agy_proxy_app.command("start")
def agy_proxy_start(
    port: int = typer.Option(agy_proxy_mod.DEFAULT_PORT, "--port"),
    upstream: str = typer.Option(agy_proxy_mod.DEFAULT_UPSTREAM, "--upstream"),
) -> None:
    """Démarre le proxy AGY en arrière-plan. Lance ensuite tes sessions avec
    `cli-switch agy run`, et bascule tout le monde avec `cli-switch agy use`."""
    import subprocess
    import sys

    store = _store()
    if _proxy_running_pid(store):
        _warn(f"Le proxy tourne déjà (PID {_proxy_running_pid(store)}).")
        return
    # compte courant par défaut : celui déjà actif, sinon premier profil AGY.
    if not agy_proxy_mod.AgyProxy(store).current_account():
        default = store.current_agy() or next(
            (n for n in store.list_profiles() if store.profile(n).agy_live_token.exists()), None
        )
        if default:
            from cli_switch.locking import atomic_write_text

            atomic_write_text(store.data_dir / "agy_proxy_current", default)
    # Refuser tôt si le port est déjà pris (par un autre process que le nôtre).
    import socket
    import time

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        in_use = probe.connect_ex(("127.0.0.1", port)) == 0
    finally:
        probe.close()
    if in_use:
        _fail(f"Le port {port} est déjà occupé. Choisis un autre port avec --port.")
        return

    log_path = store.data_dir / "agy_proxy.log"
    logf = open(log_path, "a")
    # `start_new_session` (setsid) est POSIX ONLY — silencieusement ignoré
    # sur Windows (vérifié : `subprocess._execute_child` y nomme ce
    # paramètre `unused_start_new_session`). Sans détachement réel sur
    # Windows, le proxy resterait rattaché à notre process et pourrait
    # mourir avec `cli-switch agy proxy start` au lieu de survivre en fond.
    if sys.platform == "win32":
        popen_kwargs = {
            "creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        }
    else:
        popen_kwargs = {"start_new_session": True}
    proc = subprocess.Popen(
        [sys.executable, "-m", "cli_switch.agy.proxy", "--port", str(port), "--upstream", upstream],
        stdout=logf, stderr=subprocess.STDOUT, **popen_kwargs,
    )
    # Vérifier qu'il a réellement démarré (Popen réussit même si le proxy
    # crashe au bind). On sonde le port pendant ~3s.
    started = False
    for _ in range(15):
        time.sleep(0.2)
        if proc.poll() is not None:
            break  # process déjà mort
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                started = True
                break
        finally:
            s.close()
    if not started:
        _fail(f"Le proxy n'a pas démarré (voir {log_path}).")
        return

    from cli_switch.locking import atomic_write_text

    atomic_write_text(_proxy_pidfile(store), str(proc.pid))
    atomic_write_text(_proxy_port_file(store), str(port))
    _ok(f"Proxy AGY démarré (PID {proc.pid}, port {port}, upstream {upstream}).")
    _info(f"Compte courant : {agy_proxy_mod.AgyProxy(store).current_account() or '(aucun)'}")
    _info("Lance tes sessions avec : cli-switch agy run   — puis bascule avec : cli-switch agy use <compte>")


@agy_proxy_app.command("stop")
def agy_proxy_stop() -> None:
    """Arrête le proxy AGY."""
    import os
    import signal

    store = _store()
    pid = _proxy_running_pid(store)
    if not pid:
        _info("Le proxy n'est pas en cours d'exécution.")
        _proxy_pidfile(store).unlink(missing_ok=True)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as e:
        _fail(f"Impossible d'arrêter le proxy (PID {pid}) : {e}")
        return
    _proxy_pidfile(store).unlink(missing_ok=True)
    _ok(f"Proxy AGY arrêté (PID {pid}).")


@agy_proxy_app.command("status")
def agy_proxy_status() -> None:
    """Affiche l'état du proxy AGY."""
    store = _store()
    pid = _proxy_running_pid(store)
    if not pid:
        _info("Proxy AGY : arrêté.")
        return
    port = ""
    if _proxy_port_file(store).exists():
        port = _proxy_port_file(store).read_text(encoding="utf-8").strip()
    current = agy_proxy_mod.AgyProxy(store).current_account()
    _ok(f"Proxy AGY : actif (PID {pid}, port {port}).")
    _info(f"Compte courant : {current or '(aucun)'}")


@agy_app.command("use")
def agy_use(account: str) -> None:
    """Bascule TOUTES les sessions agy (lancées via le proxy) sur <compte>,
    à chaud, sans redémarrage."""
    store = _store()
    if not store.profile(account).agy_live_token.exists():
        _fail(f"Le profil '{account}' n'a pas de compte AGY enregistré.")
        return
    from cli_switch.locking import atomic_write_text

    atomic_write_text(store.data_dir / "agy_proxy_current", account)
    store.set_current_agy(account)
    email = ""
    if store.profile(account).agy_email.exists():
        email = store.profile(account).agy_email.read_text(encoding="utf-8").strip()
    _ok(f"Compte AGY courant → '{account}'{f' ({email})' if email else ''}.")
    if _proxy_running_pid(store):
        _info("Toutes les sessions agy lancées via le proxy basculent à leur prochaine requête.")
    else:
        _warn("Le proxy n'est pas démarré — lance `cli-switch agy proxy start` d'abord.")


@agy_app.command(
    "run",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def agy_run(ctx: typer.Context) -> None:
    """Lance `agy` à travers le proxy (pose CLOUD_CODE_URL). Tout argument
    supplémentaire est transmis à `agy` (ex: cli-switch agy run -- --continue)."""
    import os

    store = _store()
    pid = _proxy_running_pid(store)
    if not pid:
        _fail("Le proxy AGY n'est pas démarré. Lance : cli-switch agy proxy start")
        return
    port = agy_proxy_mod.DEFAULT_PORT
    if _proxy_port_file(store).exists():
        port = int(_proxy_port_file(store).read_text(encoding="utf-8").strip())
    import shutil

    if shutil.which("agy") is None:
        _fail("Binaire `agy` introuvable dans le PATH.")
        return
    env = dict(os.environ)
    env["CLOUD_CODE_URL"] = f"http://127.0.0.1:{port}"
    extra = list(ctx.args)
    _info(f"Lancement de `agy` via le proxy (compte courant : "
          f"{agy_proxy_mod.AgyProxy(store).current_account() or '(aucun)'}).")
    try:
        os.execvpe("agy", ["agy", *extra], env)
    except OSError as e:
        _fail(f"Impossible de lancer `agy` : {e}")


# ── codex login/switch/current ───────────────────────────────────────────


@codex_app.command("login")
def codex_login(
    name: str,
    current: bool = typer.Option(
        False,
        "--current",
        "-c",
        help="Capturer le compte DÉJÀ connecté sans relancer `codex login`.",
    ),
) -> None:
    """Connecte un nouveau compte Codex ou capture le compte actif sous le profil <name>."""
    store = _store()
    if not current:
        try:
            codex_switcher.run_codex_login()
        except RuntimeError as e:
            _fail(str(e))
            return
    try:
        email = codex_switcher.capture_current(store, name)
    except CliSwitchError as e:
        _fail(str(e))
        return
    store.set_current_codex(name)
    _ok(f"Compte Codex '{email}' enregistré sous le profil '{name}'.")


@codex_app.command("add")
def codex_add(name: str) -> None:
    """Enregistre le compte Codex ACTUELLEMENT connecté sous le profil <name>."""
    codex_login(name=name, current=True)


@codex_app.command("current")
def codex_current() -> None:
    """Affiche le profil Codex actif."""
    store = _store()
    name = store.current_codex()
    email = codex_credentials.current_codex_email()
    if name:
        typer.echo(f"Profil Codex actif : {name} ({email or 'email non détecté'})")
    else:
        typer.echo(f"Profil Codex actif : (aucun) ({email or 'aucun compte'})")


@codex_app.command("switch")
def codex_switch_cmd(name: str) -> None:
    """Bascule Codex vers le profil <name>."""
    store = _store()
    try:
        codex_switcher.switch(store, name)
    except CliSwitchError as e:
        _fail(str(e))
        return
    _ok(f"Codex → profil '{name}' actif.")


@codex_app.command("rm")
def codex_rm(name: str, yes: bool = typer.Option(False, "-y", "--yes")) -> None:
    """Supprime les credentials Codex d'un profil."""
    store = _store()
    if not store.profile_exists(name):
        _fail(f"Profil '{name}' introuvable.")
        return
    if not yes and not typer.confirm(f"Supprimer les credentials Codex du profil '{name}' ?", default=False):
        raise typer.Exit(code=1)
    store.clear_codex_side(name)
    store.remove_profile_if_empty(name)
    _ok(f"Credentials Codex supprimés du profil '{name}'.")


# ── commandes combinées (les outils) ────────────────────────────────────



@app.command("login")
def login_both(name: str) -> None:
    """Enregistre le compte courant pour Claude ET AGY sous le profil <name>."""
    claude_login(name)
    agy_login(name)


@app.command("list")
def list_profiles(
    only: str | None = typer.Option(
        None, "--only", help="Limiter l'affichage à une famille : claude, agy ou codex."
    ),
) -> None:
    """Liste les profils et leurs comptes (alias : `ls`). Par famille : `claude ls`, `agy ls`, `codex ls`."""
    if only is not None and only not in ("claude", "agy", "codex"):
        _fail(f"Famille inconnue « {only} » (attendu : claude, agy, codex).")
        return
    store = _store()
    profiles = store.list_profiles()
    if not profiles:
        _info("Aucun profil enregistré.")
        return
    current_claude = store.current_claude()
    current_agy = store.current_agy()
    current_codex = store.current_codex()
    for name in profiles:
        p = store.profile(name)
        claude_email = p.claude_email.read_text(encoding="utf-8").strip() if p.claude_email.exists() else "-"
        agy_email = p.agy_email.read_text(encoding="utf-8").strip() if p.agy_email.exists() else "-"
        codex_email = p.codex_email.read_text(encoding="utf-8").strip() if p.codex_email.exists() else "-"
        marker_claude = "●" if current_claude == name else " "
        marker_agy = "●" if current_agy == name else " "
        marker_codex = "●" if current_codex == name else " "
        cols = {
            "claude": f"claude[{marker_claude}] {claude_email:25}",
            "agy": f"agy[{marker_agy}] {agy_email:25}",
            "codex": f"codex[{marker_codex}] {codex_email}",
        }
        if only:
            if cols_email_empty(only, claude_email, agy_email, codex_email):
                continue  # profil sans compte dans cette famille : on ne l'affiche pas
            typer.echo(f"{name:15}  {cols[only].rstrip()}")
        else:
            typer.echo(f"{name:15}  {cols['claude']}  {cols['agy']}  {cols['codex']}")


def cols_email_empty(family: str, claude_email: str, agy_email: str, codex_email: str) -> bool:
    return {"claude": claude_email, "agy": agy_email, "codex": codex_email}[family] == "-"


@app.command("current")
def show_current() -> None:
    """Affiche les profils actifs (Claude, AGY, Codex)."""
    store = _store()
    typer.echo(f"Claude : {store.current_claude() or '(aucun)'}")
    typer.echo(f"AGY    : {store.current_agy() or '(aucun)'}")
    typer.echo(f"Codex  : {store.current_codex() or '(aucun)'}")



@app.command("rm")
def remove_profile(name: str, yes: bool = typer.Option(False, "-y", "--yes")) -> None:
    """Supprime un profil entier (Claude + AGY + Codex). Confirmation sauf avec -y."""
    store = _store()
    if not store.profile_exists(name):
        _fail(f"Profil '{name}' introuvable.")
        return
    if not yes and not typer.confirm(f"Supprimer le profil '{name}' (Claude + AGY) ?", default=False):
        raise typer.Exit(code=1)
    store.remove_profile(name)
    _ok(f"Profil '{name}' supprimé.")


@app.command("add")
def add_both(name: str) -> None:
    """Enregistre un nouveau compte (Claude + AGY) sous <name> sans changer
    les comptes actuellement actifs."""
    claude_add(name)
    agy_add(name)


@app.command("sync")
def sync_both(
    name: str | None = typer.Argument(None, help="Nom du profil à synchroniser (par défaut: profils actifs)."),
) -> None:
    """Capture et sauvegarde les sessions actives (Claude ET AGY) sans redemander de login."""
    try:
        claude_sync(name)
    except typer.Exit:
        pass
    try:
        agy_sync(name)
    except typer.Exit:
        pass


@app.command("save", hidden=True)
def save_both(
    name: str | None = typer.Argument(None),
) -> None:
    """Alias pour 'sync'."""
    sync_both(name)


@app.command("clear")
def clear_all(
    yes: bool = typer.Option(False, "-y", "--yes", help="Pas de confirmation."),
    claude_only: bool = typer.Option(False, "--claude", help="Efface uniquement la partie Claude de tous les profils."),
    agy_only: bool = typer.Option(False, "--agy", help="Efface uniquement la partie AGY de tous les profils."),
) -> None:
    """Supprime TOUS les profils enregistrés.

    Sans option : efface Claude ET AGY.
    --claude    : efface uniquement les credentials Claude.
    --agy       : efface uniquement les credentials AGY.
    """
    store = _store()
    profiles = store.list_profiles()
    if not profiles:
        _info("Aucun profil à effacer.")
        return

    if claude_only and agy_only:
        _fail("--claude et --agy sont mutuellement exclusifs. Utilise l'un ou l'autre.")
        return

    if claude_only:
        label = "la partie Claude de TOUS les profils"
    elif agy_only:
        label = "la partie AGY de TOUS les profils"
    else:
        label = "TOUS les profils (Claude + AGY)"

    if not yes and not typer.confirm(f"⚠ Supprimer {label} ?", default=False):
        raise typer.Exit(code=1)

    count = 0
    for name in profiles:
        if claude_only:
            if store.profile(name).claude_credentials.exists():
                store.clear_claude_side(name)
                store.remove_profile_if_empty(name)
                count += 1
        elif agy_only:
            if store.profile(name).agy_live_token.exists():
                store.clear_agy_side(name)
                store.remove_profile_if_empty(name)
                count += 1
        else:
            store.remove_profile(name)
            count += 1

    import json as _json
    from cli_switch.paths import (
        claude_credentials_path, claude_global_config_path,
        agy_live_token_path, agy_google_accounts_path,
    )

    def _reset_claude_live() -> None:
        live = claude_credentials_path()
        if live.exists():
            empty = {"claudeAiOauth": {"accessToken": "", "refreshToken": "",
                                        "expiresAt": 0, "scopes": [], "subscriptionType": ""}}
            live.write_text(_json.dumps(empty, indent=2), encoding="utf-8")
            _info(f"Fichier live réinitialisé : {live}")
        # Ne PAS supprimer ~/.claude.json — contient projets/onboarding.
        # On efface seulement oauthAccount pour déconnecter l'identité.
        _gcfg = claude_global_config_path()
        if _gcfg.exists():
            try:
                _d = _json.loads(_gcfg.read_text(encoding="utf-8"))
                _d.pop("oauthAccount", None)
                _gcfg.write_text(_json.dumps(_d, indent=2), encoding="utf-8")
                _info(f"oauthAccount effacé dans : {_gcfg} (fichier conservé)")
            except Exception:
                pass

    def _reset_agy_live() -> None:
        agy_credentials.clear_live_token_files()
        agy_credentials.clear_keyring_token()
        agy_google_accounts_path().unlink(missing_ok=True)
        _info("Fichiers live AGY supprimés.")

    if claude_only:
        store.current_claude_path.unlink(missing_ok=True)
        _reset_claude_live()
        _ok(f"Credentials Claude effacés sur {count} profil(s) + fichiers live réinitialisés.")
    elif agy_only:
        store.current_agy_path.unlink(missing_ok=True)
        _reset_agy_live()
        _ok(f"Credentials AGY effacés sur {count} profil(s) + fichiers live réinitialisés.")
    else:
        store.current_claude_path.unlink(missing_ok=True)
        store.current_agy_path.unlink(missing_ok=True)
        _reset_claude_live()
        _reset_agy_live()
        _ok(f"{count} profil(s) supprimé(s) — Claude + AGY réinitialisés à neuf.")


@app.command("export")
def export_profiles(out: str = typer.Argument("cli-switch-export.tar.gz")) -> None:
    """Exporte tous les profils dans une archive .tar.gz."""
    import tarfile

    store = _store()
    with tarfile.open(out, "w:gz") as tar:
        tar.add(store.profiles_dir, arcname="profiles")
        for extra in (store.current_claude_path, store.current_agy_path):
            if extra.exists():
                tar.add(extra, arcname=extra.name)
    _ok(f"Profils exportés dans '{out}'.")


@app.command("import")
def import_profiles(in_file: str, yes: bool = typer.Option(False, "-y", "--yes")) -> None:
    """Importe des profils depuis une archive .tar.gz (fusionne avec l'existant)."""
    import tarfile
    from pathlib import Path

    if not Path(in_file).exists():
        _fail(f"Archive '{in_file}' introuvable.")
        return
    if not yes and not typer.confirm("⚠ L'importation va fusionner les profils existants. Continuer ?", default=False):
        raise typer.Exit(code=1)
    store = _store()
    with tarfile.open(in_file, "r:gz") as tar:
        tar.extractall(store.data_dir, filter="data")
    _ok("Importation terminée.")


@app.command("rename")
def rename_profile(old: str, new: str) -> None:
    """Renomme le profil <old> en <new>."""
    store = _store()
    try:
        store.rename_profile(old, new)
    except FileExistsError as e:
        _fail(str(e))
        return
    _ok(f"Profil '{old}' renommé en '{new}'.")


@app.command("switch")
def switch_both(name: str) -> None:
    """Bascule Claude, AGY et Codex vers le profil <name> (ceux configurés)."""
    store = _store()

    claude_ok = store.profile(name).claude_credentials.exists()
    agy_ok = store.profile(name).agy_live_token.exists()
    codex_ok = store.profile(name).codex_auth.exists()
    if not claude_ok and not agy_ok and not codex_ok:
        _fail(f"Profil '{name}' introuvable (aucun credential Claude, AGY ou Codex).")
        return

    if claude_ok:
        try:
            warning = claude_switcher.switch_to(store, name)
            if warning:
                _info(f"Sessions actives détectées — daemon stoppé, switch effectué sur '{name}'.")
        except LiveProcessConflict:
            claude_switcher.switch_to(store, name, force=True)
        except DaemonRestartFailed as e:
            _warn(str(e))
        except CliSwitchError as e:
            _warn(f"Claude : {e}")
    else:
        _info("Aucun compte Claude enregistré pour ce profil, ignoré.")

    if agy_ok:
        _agy_switch_impl(name, kill=False)
    else:
        _info("Aucun compte AGY enregistré pour ce profil, ignoré.")

    if codex_ok:
        try:
            codex_switcher.switch(store, name)
            _ok(f"Codex → profil '{name}' actif.")
        except CliSwitchError as e:
            _warn(f"Codex : {e}")
    else:
        _info("Aucun compte Codex enregistré pour ce profil, ignoré.")


# ── Instance & Run (Multi-comptes isolés) ───────────────────────────────────


@instance_app.command("setup")
def instance_setup(name: str) -> None:
    """Prépare ou met à jour l'arborescence d'une instance isolée pour un profil."""
    from cli_switch.instances import setup_instance

    try:
        path = setup_instance(name)
        _ok(f"Instance '{name}' prête dans : {path}")
    except Exception as e:
        _fail(str(e))


@instance_app.command("path")
def instance_path(name: str) -> None:
    """Affiche le chemin absolu de l'instance d'un profil."""
    from cli_switch.instances import get_instance_dir

    typer.echo(str(get_instance_dir(name)))


@instance_app.command("list")
def instance_list() -> None:
    """Liste les instances isolées existantes."""
    from cli_switch.instances import instances_base_dir

    base = instances_base_dir()
    if not base.exists():
        typer.echo("(aucune instance)")
        return
    instances = [d.name for d in base.iterdir() if d.is_dir()]
    if not instances:
        typer.echo("(aucune instance)")
        return
    for inst in sorted(instances):
        typer.echo(f"- {inst} ({base / inst})")


@app.command("run", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def app_run(
    ctx: typer.Context,
    profile: str = typer.Option(..., "--profile", "-p", help="Nom du profil d'instance."),
    provider: str = typer.Option("all", "--provider", help="Provider ('codex', 'agy', ou 'all')."),
) -> None:
    """Exécute une commande dans l'environnement isolé d'un profil sans toucher aux sessions globales."""
    from cli_switch.instances import run_in_instance

    cmd = ctx.args
    if not cmd:
        _fail("Aucune commande spécifiée.")
    code = run_in_instance(profile, cmd, provider=provider)
    raise typer.Exit(code=code)


@codex_app.command("run", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def codex_run(
    ctx: typer.Context,
    profile: str = typer.Option(..., "--profile", "-p", help="Nom du profil d'instance Codex."),
) -> None:
    """Exécute une commande sous Codex isolé avec le profil spécifié."""
    from cli_switch.instances import run_in_instance

    cmd = ctx.args
    if not cmd:
        _fail("Aucune commande spécifiée.")
    code = run_in_instance(profile, cmd, provider="codex")
    raise typer.Exit(code=code)


@agy_app.command("run", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def agy_run(
    ctx: typer.Context,
    profile: str = typer.Option(..., "--profile", "-p", help="Nom du profil d'instance AGY."),
) -> None:
    """Exécute une commande sous AGY isolé avec le profil spécifié."""
    from cli_switch.instances import run_in_instance

    cmd = ctx.args
    if not cmd:
        _fail("Aucune commande spécifiée.")
    code = run_in_instance(profile, cmd, provider="agy")
    raise typer.Exit(code=code)


# ── dispatch `cli-switch <nom>` / `cli-switch claude <nom>` / `cli-switch agy <nom>` / `cli-switch codex <nom>` ──
#
# Typer/Click ne permet pas de mélanger proprement un argument positionnel
# optionnel sur un groupe ET des sous-commandes du même groupe (ambiguïté :
# "list" serait interprété comme la valeur de l'argument plutôt que comme la
# sous-commande "list"). On réécrit donc argv en amont : si le premier mot
# (ou le second, sous "claude"/"agy"/"codex") n'est pas une sous-commande connue,
# on insère "switch" avant, pour retomber sur les vraies commandes ci-dessus.

_TOP_LEVEL_COMMANDS = {
    "login", "add", "list", "ls", "current", "rm", "rename", "switch", "clear",
    "export", "import", "claude", "agy", "codex", "instance", "run", "sync", "save",
}
_SUB_COMMANDS = {"login", "add", "switch", "current", "rm", "clear", "proxy", "use", "run", "setup", "path", "list", "ls", "sync", "save"}



_HELP_WORDS = {"help", "-h", "--help"}


def _closest_command(word: str, candidates: set[str]) -> str | None:
    """Renvoie la commande connue la plus proche de `word` (faute de frappe),
    ou None si rien de suffisamment ressemblant."""
    import difflib

    matches = difflib.get_close_matches(word, sorted(candidates), n=1, cutoff=0.7)
    return matches[0] if matches else None


def _typo_error(argv: list[str]) -> str | None:
    """Détecte une commande mal tapée AVANT que le dispatch « mot nu = switch »
    ne la transforme en erreur incompréhensible.

    Règle d'or : un switch de profil implicite (`cli-switch jfe`,
    `cli-switch claude jfe`) ne prend qu'UN SEUL nom de profil. Donc un mot
    inconnu SUIVI d'autres mots est presque toujours une faute de frappe sur
    une commande (`claud login jfe` → l'utilisateur voulait `claude login jfe`).
    On renvoie alors un message clair (avec suggestion), plutôt que le cryptique
    « Got unexpected extra argument(s) ». On n'intercepte JAMAIS un simple
    `cli-switch <profil>` légitime (un seul token)."""
    if not argv or argv[0].startswith("-") or argv[0] in _HELP_WORDS:
        return None
    # Niveau supérieur : mot inconnu + arguments → typo de commande.
    if argv[0] not in _TOP_LEVEL_COMMANDS and len(argv) >= 2:
        suggestion = _closest_command(argv[0], _TOP_LEVEL_COMMANDS)
        hint = f" Vouliez-vous dire « cli-switch {suggestion} {' '.join(argv[1:])} » ?" if suggestion else ""
        return (
            f"« {argv[0]} » n'est pas une commande connue, et un switch de profil "
            f"ne prend qu'un seul nom (reçu : {' '.join(argv)})." + hint
        )
    # Groupe claude/agy/codex : sous-commande inconnue SUIVIE d'arguments → typo.
    if (
        argv[0] in ("claude", "agy", "codex")
        and len(argv) >= 3
        and not argv[1].startswith("-")
        and argv[1] not in _SUB_COMMANDS
    ):
        suggestion = _closest_command(argv[1], _SUB_COMMANDS)
        hint = f" Vouliez-vous dire « cli-switch {argv[0]} {suggestion} {' '.join(argv[2:])} » ?" if suggestion else ""
        return (
            f"« {argv[1]} » n'est pas une sous-commande de « {argv[0]} », et un "
            f"switch ne prend qu'un seul nom de profil." + hint
        )
    return None


def _rewrite_argv(argv: list[str]) -> list[str]:
    if not argv:
        return argv
    # `help` / `-h` / `--help` au premier niveau → afficher l'aide (et non
    # traiter le mot comme un nom de profil).
    if argv[0] in _HELP_WORDS:
        return ["--help", *argv[1:]]
    if argv[0].startswith("-"):
        return argv
    argv = list(argv)
    # `ls` = `list` ; `<famille> ls|list` = liste filtrée sur la famille.
    if argv[0] == "ls":
        return ["list", *argv[1:]]
    if argv[0] in ("claude", "agy", "codex") and len(argv) >= 2 and argv[1] in ("ls", "list"):
        return ["list", "--only", argv[0], *argv[2:]]
    if argv[0] not in _TOP_LEVEL_COMMANDS:
        return ["switch", *argv]
    if argv[0] in ("claude", "agy", "codex") and len(argv) >= 2:
        # `claude help` / `codex --help` / `agy -h` → aide de la sous-commande.
        if argv[1] in _HELP_WORDS:
            return [argv[0], "--help", *argv[2:]]
        if not argv[1].startswith("-") and argv[1] not in _SUB_COMMANDS:
            return [argv[0], "switch", *argv[1:]]
    return argv



def main() -> None:
    import sys

    # Intercepte les fautes de frappe évidentes AVANT le dispatch « mot nu =
    # switch » (qui, sinon, produit un « Got unexpected extra argument(s) »
    # incompréhensible — cf. `claud login jfe`).
    typo = _typo_error(sys.argv[1:])
    if typo:
        # On est AVANT l'entrée dans l'app Typer : sortir via SystemExit (et
        # non typer.Exit, qui n'est intercepté qu'à l'intérieur de l'app et
        # laisserait fuiter un traceback).
        typer.secho(f"✖ {typo}", fg=typer.colors.RED, err=True)
        raise SystemExit(2)
    sys.argv[1:] = _rewrite_argv(sys.argv[1:])
    # Nom de programme cohérent dans l'aide, que l'on soit lancé via le
    # wrapper `cli-switch` ou `python -m cli_switch`.
    app(prog_name="cli-switch")


if __name__ == "__main__":
    main()
