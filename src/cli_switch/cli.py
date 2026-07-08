"""Interface en ligne de commande de cli-switch."""

from __future__ import annotations

import typer

from cli_switch.agy import switcher as agy_switcher
from cli_switch.claude import switcher as claude_switcher
from cli_switch.exceptions import CliSwitchError, LiveProcessConflict
from cli_switch.store import Store

# `-h` en plus de `--help` partout (sans ça, seul --help marche, ce qui
# surprend puisqu'un mot nu est interprété comme un nom de profil).
_HELP_OPTS = {"help_option_names": ["-h", "--help"]}

app = typer.Typer(
    help="Bascule entre plusieurs comptes Claude Code et Antigravity (AGY).",
    no_args_is_help=True,
    context_settings=_HELP_OPTS,
)
claude_app = typer.Typer(help="Commandes spécifiques à Claude Code.", context_settings=_HELP_OPTS)
agy_app = typer.Typer(help="Commandes spécifiques à AGY (Antigravity).", context_settings=_HELP_OPTS)
agy_proxy_app = typer.Typer(
    help="Proxy AGY multi-compte (hot-swap de toutes les sessions).",
    context_settings=_HELP_OPTS,
)
app.add_typer(claude_app, name="claude")
app.add_typer(agy_app, name="agy")
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
        claude_switcher.login(store, name)
    except CliSwitchError as e:
        _fail(str(e))
        return
    # Restaurer l'ancien actif — mais SEULEMENT s'il a encore des credentials
    # Claude. Cas limite : si `name` capturait le MÊME compte que l'actif,
    # login() a dédoublonné et vidé le côté Claude de l'ancien profil ; tenter
    # de re-switcher dessus lèverait alors ProfileNotFoundError (crash observé).
    can_restore = (
        active_before
        and active_before != name
        and store.profile(active_before).claude_credentials.exists()
    )
    if can_restore:
        try:
            claude_switcher.switch_to(store, active_before, force=True)
            _ok(f"Profil Claude '{name}' enregistré ; compte précédent '{active_before}' restauré comme actif.")
            return
        except CliSwitchError as e:
            _warn(f"Profil '{name}' enregistré, mais restauration de '{active_before}' impossible : {e}")
            return
    _ok(f"Profil Claude '{name}' enregistré et actif.")


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


@claude_app.command("switch")
def claude_switch_cmd(name: str) -> None:
    """Bascule Claude vers le profil <name>."""
    store = _store()
    # Profil sans identité stockée (ex: migré de l'ancien claude-switch qui ne
    # stockait que les credentials) → Claude dira « aucun compte » après le
    # swap. On prévient AVANT et on propose de continuer.
    has_identity = store.profile(name).claude_oauth_account.exists()
    try:
        warning = claude_switcher.switch_to(store, name)
    except LiveProcessConflict as e:
        _warn(str(e))
        if not typer.confirm("Continuer quand même ?", default=False):
            raise typer.Exit(code=1)
        warning = claude_switcher.switch_to(store, name, force=True)
    except CliSwitchError as e:
        _fail(str(e))
        return
    if warning:
        _warn(
            f"Claude Code était actif ({warning.describe()}) — ce process ne "
            "verra le changement qu'au prochain lancement."
        )
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
    proc = subprocess.Popen(
        [sys.executable, "-m", "cli_switch.agy.proxy", "--port", str(port), "--upstream", upstream],
        stdout=logf, stderr=subprocess.STDOUT, start_new_session=True,
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


# ── commandes combinées (les deux outils) ────────────────────────────────


@app.command("login")
def login_both(name: str) -> None:
    """Enregistre le compte courant pour Claude ET AGY sous le profil <name>."""
    claude_login(name)
    agy_login(name)


@app.command("list")
def list_profiles() -> None:
    store = _store()
    profiles = store.list_profiles()
    if not profiles:
        _info("Aucun profil enregistré.")
        return
    current_claude = store.current_claude()
    current_agy = store.current_agy()
    for name in profiles:
        p = store.profile(name)
        claude_email = p.claude_email.read_text(encoding="utf-8").strip() if p.claude_email.exists() else "-"
        agy_email = p.agy_email.read_text(encoding="utf-8").strip() if p.agy_email.exists() else "-"
        marker_claude = "●" if current_claude == name else " "
        marker_agy = "●" if current_agy == name else " "
        typer.echo(f"{name:20}  claude[{marker_claude}] {claude_email:35}  agy[{marker_agy}] {agy_email}")


@app.command("current")
def show_current() -> None:
    store = _store()
    typer.echo(f"Claude : {store.current_claude() or '(aucun)'}")
    typer.echo(f"AGY    : {store.current_agy() or '(aucun)'}")


@app.command("rm")
def remove_profile(name: str, yes: bool = typer.Option(False, "-y", "--yes")) -> None:
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


@app.command("clear")
def clear_all(yes: bool = typer.Option(False, "-y", "--yes")) -> None:
    """Supprime TOUS les profils enregistrés (Claude + AGY)."""
    store = _store()
    if not yes and not typer.confirm("⚠ Supprimer TOUS les profils ?", default=False):
        raise typer.Exit(code=1)
    store.clear_all()
    _ok("Tous les profils supprimés.")


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
    store = _store()
    try:
        store.rename_profile(old, new)
    except FileExistsError as e:
        _fail(str(e))
        return
    _ok(f"Profil '{old}' renommé en '{new}'.")


@app.command("switch")
def switch_both(name: str) -> None:
    """Bascule Claude ET AGY vers le profil <name> (ceux configurés)."""
    store = _store()

    claude_ok = store.profile(name).claude_credentials.exists()
    agy_ok = store.profile(name).agy_live_token.exists()
    if not claude_ok and not agy_ok:
        _fail(f"Profil '{name}' introuvable (ni Claude ni AGY).")
        return

    if claude_ok:
        try:
            warning = claude_switcher.switch_to(store, name)
            if warning:
                _warn(f"Claude Code était actif ({warning.describe()}).")
        except LiveProcessConflict as e:
            _warn(str(e))
            if typer.confirm("Continuer quand même pour Claude ?", default=False):
                claude_switcher.switch_to(store, name, force=True)
        except CliSwitchError as e:
            _warn(f"Claude : {e}")
    else:
        _info("Aucun compte Claude enregistré pour ce profil, ignoré.")

    if agy_ok:
        _agy_switch_impl(name, kill=False)
    else:
        _info("Aucun compte AGY enregistré pour ce profil, ignoré.")


# ── dispatch `cli-switch <nom>` / `cli-switch claude <nom>` / `cli-switch agy <nom>` ──
#
# Typer/Click ne permet pas de mélanger proprement un argument positionnel
# optionnel sur un groupe ET des sous-commandes du même groupe (ambiguïté :
# "list" serait interprété comme la valeur de l'argument plutôt que comme la
# sous-commande "list"). On réécrit donc argv en amont : si le premier mot
# (ou le second, sous "claude"/"agy") n'est pas une sous-commande connue,
# on insère "switch" avant, pour retomber sur les vraies commandes ci-dessus.

_TOP_LEVEL_COMMANDS = {
    "login", "add", "list", "current", "rm", "rename", "switch", "clear",
    "export", "import", "claude", "agy",
}
_SUB_COMMANDS = {"login", "add", "switch", "current", "rm", "proxy", "use", "run"}


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
    # Groupe claude/agy : sous-commande inconnue SUIVIE d'arguments → typo.
    if (
        argv[0] in ("claude", "agy")
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
    if argv[0] not in _TOP_LEVEL_COMMANDS:
        return ["switch", *argv]
    if argv[0] in ("claude", "agy") and len(argv) >= 2:
        # `claude help` / `claude -h` / `agy --help` → aide de la sous-commande.
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
