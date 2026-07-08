#!/usr/bin/env bash
#
# Installe cli-switch (Python) sur cette machine.
# Usage : git clone <repo> && cd cli-switch && ./install.sh
#
# Idempotent : peut être relancé sans risque (met à jour l'existant).
set -euo pipefail

echo ""
echo "  cli-switch — comptes Claude Code + Antigravity (AGY)"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
BIN_DIR="${HOME}/.local/bin"
WRAPPER="${BIN_DIR}/cli-switch"

# ── 1. Dépendances ────────────────────────────────────────────────────────────
echo "  Vérification de l'environnement..."

if ! command -v python3 &>/dev/null; then
  echo "  ✖ python3 est requis mais introuvable." >&2
  exit 1
fi
PYVER="$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
echo "  ✔ python3 ${PYVER}"

# python venv module (parfois absent sur Debian/Ubuntu → paquet python3-venv)
if ! python3 -c 'import venv' &>/dev/null; then
  echo "  ✖ Le module 'venv' de Python est absent." >&2
  echo "    Installez-le, ex. Debian/Ubuntu : sudo apt install python3-venv" >&2
  exit 1
fi

command -v claude &>/dev/null && echo "  ✔ claude OK" \
  || echo "  ⚠ 'claude' absent du PATH — les fonctions Claude seront indisponibles."
command -v agy &>/dev/null && echo "  ✔ agy OK" \
  || echo "  ⚠ 'agy' absent du PATH — les fonctions AGY seront indisponibles."

echo ""

# ── 2. Environnement virtuel + installation du paquet ─────────────────────────
if [[ ! -d "${VENV_DIR}" ]]; then
  echo "  Création de l'environnement virtuel (${VENV_DIR})..."
  python3 -m venv "${VENV_DIR}"
fi
echo "  Installation du paquet cli-switch (+ dépendances)..."
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip
"${VENV_DIR}/bin/pip" install --quiet -e "${SCRIPT_DIR}"
echo "  ✔ Paquet installé dans le venv"

# Vérif import
"${VENV_DIR}/bin/python" -c "import cli_switch" \
  && echo "  ✔ cli_switch importable" \
  || { echo "  ✖ Échec de l'import cli_switch." >&2; exit 1; }

echo ""

# ── 3. Wrapper global (indépendant de l'activation du venv) ───────────────────
mkdir -p "${BIN_DIR}"
# Nettoyage d'éventuels anciens symlinks (bash) cassés ou obsolètes.
for old in "${WRAPPER}" "${BIN_DIR}/claude-switch" "${HOME}/bin/claude-switch"; do
  [[ -L "${old}" || -f "${old}" ]] && rm -f "${old}" || true
done

cat > "${WRAPPER}" <<EOF
#!/usr/bin/env bash
# Wrapper global vers le cli-switch Python (généré par install.sh).
exec "${VENV_DIR}/bin/python" -m cli_switch "\$@"
EOF
chmod +x "${WRAPPER}"
echo "  ✔ Commande globale installée : ${WRAPPER}"

# Alias de compatibilité claude-switch → même wrapper.
ln -sf "${WRAPPER}" "${BIN_DIR}/claude-switch"
echo "  ✔ Alias de compatibilité : ${BIN_DIR}/claude-switch"

# ── 4. PATH ───────────────────────────────────────────────────────────────────
echo ""
if [[ ":${PATH}:" != *":${BIN_DIR}:"* ]]; then
  echo "  ⚠ ${BIN_DIR} n'est pas dans votre \$PATH."
  echo "    Ajoutez à votre ~/.bashrc ou ~/.zshrc :"
  echo "      export PATH=\"\$HOME/.local/bin:\$PATH\""
  echo "    puis rechargez le shell (source ~/.zshrc)."
else
  echo "  ✔ \$PATH OK — ${BIN_DIR} est accessible"
fi

# ── 5. Migration depuis l'ancien claude-switch (profils Claude) ───────────────
OLD_DIR="${HOME}/.claude-switch"
if [[ -d "${OLD_DIR}/accounts" ]]; then
  echo ""
  echo "  ℹ Anciens comptes claude-switch détectés (${OLD_DIR}/accounts)."
  echo "    Les profils déjà présents dans ~/.cli-switch sont conservés."
  # La migration des noms de fichiers (claude.json → claude_credentials.json)
  # est automatique au premier lancement (Store._migrate_legacy_filenames).
  # Ici on importe les comptes de l'ANCIEN emplacement ~/.claude-switch.
  read -r -p "  Importer les profils Claude de ${OLD_DIR} ? [y/N] " reply
  if [[ "${reply}" =~ ^[Yy]$ ]]; then
    "${VENV_DIR}/bin/python" - "${OLD_DIR}" <<'PY'
import json, shutil, sys
from pathlib import Path
old = Path(sys.argv[1])
from cli_switch.store import Store
st = Store()
n = 0
for f in (old / "accounts").glob("*.json"):
    name = f.stem
    prof = st.profile(name)
    prof.root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(f, prof.claude_credentials)
    email_file = old / "accounts" / f"{name}.email"
    if email_file.exists():
        shutil.copy2(email_file, prof.claude_email)
    n += 1
cur = old / "current"
if cur.exists():
    st.set_current_claude(cur.read_text().strip())
print(f"  ✔ {n} profil(s) Claude importé(s).")
print("  ⚠ Identité (oauthAccount) non incluse : au 1er switch vers un de ces")
print("    profils, Claude demandera un login une fois, puis")
print("    `cli-switch claude login <nom>` mémorisera l'identité.")
PY
  else
    echo "    Ignoré. (Import manuel possible plus tard.)"
  fi
fi

# ── 6. Résumé ─────────────────────────────────────────────────────────────────
echo ""
echo "  🎉 Installation terminée."
echo ""
echo "  Claude :"
echo "    cli-switch claude login <nom>   # enregistrer le compte connecté"
echo "    cli-switch claude <nom>         # basculer (à chaud)"
echo ""
echo "  AGY (hot-swap multi-session via proxy) :"
echo "    cli-switch agy login <nom>      # enregistrer un compte"
echo "    cli-switch agy proxy start      # démarrer le proxy (une fois)"
echo "    cli-switch agy run              # lancer agy via le proxy"
echo "    cli-switch agy use <nom>        # basculer TOUTES les sessions"
echo ""
echo "  Général : cli-switch list | current | --help"
echo ""
