# cli-switch

Bascule entre plusieurs comptes **Claude Code** et **Antigravity (AGY)** sur
une même machine, en Python, cross-platform (Linux/WSL/macOS ; support
Windows présent mais chemins AGY non encore validés — voir `paths.py`).

Remplace l'ancien script bash (`~/Desktop/ARCHIVE/claude-account-switch`),
réécrit après avoir identifié de vrais bugs sur la partie AGY (mauvais
fichier de credentials swappé, commande `agy auth login` inexistante) et
étudié deux projets de référence (`_reference/claude-swap`,
`_reference/AntigravityManager`).

## Installation

```bash
cd cli-switch
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Usage

```bash
cli-switch login <nom>          # enregistre le compte courant (Claude + AGY)
cli-switch <nom>                # bascule Claude ET AGY vers ce profil
cli-switch claude <nom>         # bascule Claude uniquement
cli-switch agy <nom>            # bascule AGY uniquement
cli-switch list                 # liste les profils enregistrés
cli-switch current              # affiche les profils actifs
cli-switch rm <nom>             # supprime un profil
cli-switch rename <a> <b>       # renomme un profil
```

## Points importants (découverts pendant l'investigation)

- **Claude** : ne touche jamais le credential actif si une instance Claude
  Code est vivante (session PID / lockfile IDE) — avertit et demande
  confirmation avant d'écraser, plutôt que de le faire silencieusement.
- **Claude — identité** : au switch, on swappe les tokens ET on réinjecte le
  bloc `oauthAccount` (identité) dans `~/.claude.json`, en préservant
  projets/historique. Le Claude moderne cross-vérifie identité↔token : sans
  ça il se déclare « aucun compte » et redemande un login. Cette identité est
  capturée au `login` ; un profil migré de l'ancien claude-switch (sans
  identité stockée) exigera un login une fois, puis
  `cli-switch claude login <nom>` pour la mémoriser.
- **AGY** : `agy` n'a **aucune** sous-commande login/logout. Le lien
  d'authentification ne s'affiche que si `agy` démarre sans session valide
  chargée. `cli-switch agy login <nom>` évince donc temporairement le token
  actif, attend que vous lanciez `agy` vous-même dans un autre terminal, puis
  capture le nouveau token une fois connecté.
- Aucun process n'est tué automatiquement par défaut (ni Claude, ni AGY) —
  un switch ne change que les fichiers sur disque, effectif au prochain
  lancement. Utilisez `cli-switch agy <nom> --kill` pour forcer l'arrêt des
  process `agy` actifs si besoin.

## AGY : hot-swap de TOUTES les sessions à chaud (proxy — recommandé)

Le vrai équivalent du comportement Claude pour AGY : un **proxy local** par
lequel passent toutes tes sessions `agy`. Basculer le compte au proxy fait
basculer **toutes les sessions ouvertes d'un coup, sans redémarrage**
(validé en conditions réelles, cf. `MECHANISM_AGY.md`).

```bash
cli-switch agy proxy start        # démarre le proxy (une seule fois)
cli-switch agy run                # lance agy À TRAVERS le proxy (au lieu de `agy`)
cli-switch agy run -- --continue  # idem en reprenant la dernière conversation
# ... quand un quota est épuisé :
cli-switch agy use <compte>       # bascule TOUTES les sessions à chaud
cli-switch agy proxy status       # état du proxy + compte courant
cli-switch agy proxy stop         # arrêt
```

Comment ça marche : `cli-switch agy run` pose `CLOUD_CODE_URL` vers le proxy.
Le proxy détient tes comptes (refresh_tokens des profils cli-switch), mint un
access_token frais pour le « compte courant » et l'injecte dans chaque requête.
`cli-switch agy use <compte>` change le compte courant → toutes les sessions
basculent à leur requête suivante. Le token OAuth propre d'`agy` n'est plus ce
qui décide du quota — c'est le proxy.

> Note : le **banner** d'`agy` affiche son identité OAuth locale, qui peut
> différer du compte réellement utilisé (celui du proxy). Le quota consommé
> est bien celui sélectionné par `cli-switch agy use`.

Alternative sans proxy (une session à la fois) ci-dessous.

## AGY : continuer sa session après un switch (sans proxy)

Contrairement à Claude Code, un process `agy` **déjà lancé ne relit jamais**
ses credentials — il garde son compte en mémoire jusqu'à la fin du process
(mécanisme confirmé par reverse-engineering, voir `MECHANISM_AGY.md`). Le swap
de fichier ne peut donc pas basculer une fenêtre `agy` déjà ouverte.

**La bonne façon de continuer sur le nouveau compte sans perdre le contexte :**

```bash
cli-switch agy <nom>     # écrit le token du compte cible sur le disque
# puis, dans un NOUVEAU terminal :
agy --continue           # reprend la dernière conversation, sur le nouveau compte
```

`agy` persiste toutes les conversations sur disque ; `--continue` (ou
`--conversation <id>`) les reprend à l'identique. Résultat : même conversation,
nouveau compte/quota, **sans navigateur** (le token stocké est réutilisé).

Voir `MECHANISM_AGY.md` pour l'analyse complète (pourquoi le hot-swap dans la
même fenêtre n'est pas possible sans re-login navigateur, et l'option avancée
via la RPC locale `Login`).

## Tests

```bash
pip install -e ".[dev]"
pytest
```
