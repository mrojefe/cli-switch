# Mécanisme AGY (Antigravity CLI) — comment le switch de compte fonctionne réellement

Document de référence issu d'une investigation approfondie (5 agents de
recherche + analyse statique du binaire `agy`, 2026-07-04/05). À lire avant de
toucher à la logique AGY de `cli-switch`.

> **⚡ TL;DR — LA solution multi-session existe : proxy local + `CLOUD_CODE_URL`.**
> Voir la section « SOLUTION RETENUE » plus bas. Le reste du document explique
> pourquoi les approches naïves (swap fichier, injection) ne marchent pas, ce
> qui justifie le proxy.

---

## ⚡ SOLUTION RETENUE — proxy local partagé + `CLOUD_CODE_URL`

**Objectif atteint : basculer TOUTES les sessions `agy` ouvertes sur un autre
compte, à chaud, sans redémarrage ni re-login.** C'est le comportement Claude,
pour toutes les sessions à la fois.

### Le levier (confirmé dans le binaire)

Le binaire `agy` contient : `Overriding CloudCodeServerURL via CLOUD_CODE_URL
environment variable: %q`. On peut donc rediriger tout le trafic upstream d'une
session vers un endpoint custom :

```bash
export CLOUD_CODE_URL="http://localhost:<port>"   # accepte http:// (pas de TLS requis)
agy   # cette session envoie désormais son trafic CloudCode au proxy
```

- Le saut `agy → proxy` est en **HTTP clair** → **aucun cert MITM, aucune
  interception TLS** nécessaire.
- Pas de cert-pinning sur le chemin googleapis (utilise `SystemCertPool`).
- `CLOUD_CODE_URL` est lu **au démarrage** de chaque `agy` (init AuthProvider).

### L'architecture

1. Un **proxy local** (port fixe) détient les comptes AGY (les `refresh_token`
   sont déjà stockés dans les profils `cli-switch`, fichier `agy_live_token`).
2. Toutes les sessions `agy` sont lancées avec `CLOUD_CODE_URL` → proxy.
3. Le proxy, à chaque requête : lit le « compte courant » (un fichier éditable
   à chaud), mint/récupère l'`access_token` de ce compte (via
   `https://oauth2.googleapis.com/token`, client_id/secret Antigravity connus),
   **remplace le header `Authorization`** d'`agy` par ce token (+ éventuellement
   `x-goog-user-project` du bon projet), et forwarde à l'upstream réel
   (`cloudcode-pa.googleapis.com` ou `daily-cloudcode-pa.googleapis.com` selon
   le compte — vu dans les logs).
4. `cli-switch agy use <compte>` réécrit le fichier « compte courant » → **toutes
   les sessions ouvertes basculent à leur requête suivante.**

### Pourquoi ça bascule TOUTES les sessions d'un coup

Le chemin Gemini/CloudCode qu'utilise `agy` (`/v1internal:generateContent`,
`:streamGenerateContent` — **REST/JSON+SSE**, pas gRPC pour le chat) n'a
**aucune adhérence de session** côté proxy : le compte est choisi par requête.
Un « pool » d'un seul compte courant ⇒ toutes les requêtes de toutes les
sessions utilisent ce compte. Change le compte courant ⇒ tout bascule.

### La contrainte honnête

`CLOUD_CODE_URL` est lu au **démarrage**. Il faut donc lancer les sessions
**à travers le proxy** (alias/wrapper `agy` qui pose la variable). Les sessions
lancées AVANT le proxy ne sont pas rattrapées par cette voie — SAUF via la
piste bonus ci-dessous.

### 🎁 Bonus (à tester) — retrofit des sessions déjà lancées

Le serveur local de chaque session expose la RPC **`SetCloudCodeURL`**
(`exa.language_server_pb.LanguageServerService/SetCloudCodeURL`, confirmée dans
le binaire). Avec `ANTIGRAVITY_LS_ADDRESS` + `ANTIGRAVITY_CSRF_TOKEN` (variables
d'env de la session), on pourrait **rediriger une session DÉJÀ lancée vers le
proxy à chaud**, sans la relancer. RPC mutante → à tester avec le consentement
de l'utilisateur. Si ça marche : même les sessions déjà ouvertes deviennent
rattrapables.

### Risque à connaître

Mutualiser plusieurs comptes Google derrière un endpoint unique = pooling, vu
d'un mauvais œil par Google (risque de flag de compte). Risque **opérationnel**,
pas technique.

### Précédents / outils existants

- `frieser/antigravity-proxy` — passerelle multi-comptes déjà faite (rotation
  Hybrid/Sticky/Round-Robin), clients via base URL. Option « brancher l'existant ».
- `Draculabo/AntigravityManager` `src/modules/proxy-gateway/` — le proxy à
  « account-lease » de référence (dans `_reference/`).
- `CLOUD_CODE_URL` : confirmé binaire. `GOOGLE_GEMINI_BASE_URL`+`GEMINI_API_KEY` :
  voie alternative (chemin API-key Gemini), moins directe que `CLOUD_CODE_URL`.

### ✅ PREUVE EN CONDITIONS RÉELLES (2026-07-05)

Testé sur le VPS avec un proxy PoC (`scratchpad/agy_proxy_poc.py`) et de vraies
sessions `agy` :

- **Routage** : lancé `CLOUD_CODE_URL=http://127.0.0.1:8899 agy` → 100 % du
  trafic CloudCode d'`agy` (loadCodeAssist, fetchUserInfo, retrieveUserQuota…,
  streamGenerateContent) passe par le proxy, réponses 200 OK.
- **Injection** : le proxy mint un access_token frais depuis le refresh_token
  stocké du compte courant et remplace le header Authorization d'`agy`. `agy`
  réel l'accepte (200 OK partout).
- **Comptes distincts = quotas distincts** : loadCodeAssist via le proxy
  renvoie un projet GCP différent selon le token injecté
  (compte A → `projet-gcp-A` ; compte B → `projet-gcp-B`).
- **HOT-SWAP LIVE CONFIRMÉ** : une session `agy` déjà lancée, en train de
  poller `listExperiments` chaque minute, est passée de `compte=ajl` à
  `compte=meo` **automatiquement** quand le fichier « compte courant » a été
  modifié — **même PID, aucun redémarrage**. C'est le comportement Claude
  recherché, pour AGY.
- ⚠️ Nuance : le **banner** d'`agy` affiche son identité OAuth locale (token
  sur disque), qui peut différer du compte réellement injecté par le proxy.
  Le compte/quota EFFECTIF est celui du proxy (prouvé via les projets GCP),
  pas celui du banner. À garder en tête pour l'UX (afficher le compte proxy).

### Décision de build (en attente du choix utilisateur)

- **(A) Proxy intégré à cli-switch** (recommandé) : proxy Python léger réutilisant
  les profils stockés ; commandes `cli-switch agy proxy start` + `cli-switch agy
  use <compte>`. Reverse-proxy REST transparent vers cloudcode-pa, swap du header
  `Authorization` + `x-goog-user-project`.
- **(B) Brancher `frieser/antigravity-proxy`** : plus rapide, dépendance externe.
- **(C) Test minimal d'abord** : mini-proxy jetable pour prouver `CLOUD_CODE_URL`
  en 15 min avant de tout construire.

---

## Contexte historique (pourquoi les approches naïves échouent)

Document de référence issu d'une investigation approfondie. À lire avant de
toucher à la logique AGY de `cli-switch`.

## Le problème

L'utilisateur veut, quand un compte AGY atteint son quota, **continuer sa
session de travail sur un autre compte sans perdre le contexte** — comme le
permet Claude Code (qui relit ses credentials à chaud en cours de session).

## Ce qui NE marche PAS (prouvé, définitif)

**Swapper le fichier de token sur disque pendant qu'un process `agy` tourne
n'a aucun effet sur ce process.** Raisons confirmées :

- `agy` charge le fichier `~/.gemini/antigravity-cli/antigravity-oauth-token`
  **une seule fois au démarrage**, dans un `oauth2.ReuseTokenSource` (cache
  mémoire). Vérifié : le process ne tient PAS le fichier ouvert
  (`/proc/<pid>/fd` ne montre que le log).
- Pendant la session, le token vit en mémoire. Au refresh (expiration ~1h),
  il utilise le `refresh_token` **en mémoire**, jamais relu du disque, puis
  réécrit le nouveau token sur le disque (`persistingTokenSource`). C'est un
  hook mémoire→disque à sens unique.
- Confirmé côté Go : `oauth2.ReuseTokenSource` ne relit jamais sa source
  après construction (issue golang/go#77502, ouverte depuis ~10 ans).
- Conséquence : un fichier swappé sous un process vivant est ignoré, et pire,
  le process réécrit l'ancien compte par-dessus au refresh suivant.

L'injection mémoire (ptrace/gdb) est théoriquement possible mais trop
fragile/risquée (binaire Go strippé, GC mobile) — écartée.

## Ce qui EXISTE mais est limité

Chaque process `agy` vivant expose un serveur **Connect/gRPC local**
(`exa.language_server_pb.LanguageServerService`) sur deux ports localhost
(un HTTPS/HTTP2, un HTTP simple), **sans aucune barrière d'authentification**.
Méthodes d'auth confirmées : `Login`, `AuthLogout`, `GetAuthStatus`,
`HasAuthToken`, etc. (`LoginWithBrowser` existe mais le binaire dit
explicitement *"LoginWithBrowser is deprecated and no longer supported"*).

- **`Login` déclenche le flow OAuth interactif dans le process déjà lancé.**
  Son message `LoginRequest` ne contient QUE `is_gcp_tos` (bool) et
  `additional_scopes` (liste) — **aucun champ token**. Donc on ne peut PAS
  injecter un token stocké : `Login` ne fait que lancer le login navigateur.
- Aucune RPC n'accepte un token OAuth consumer pré-obtenu. `MigrateApiKey`
  ne gère que les **clés API** (type d'auth différent, pas un compte Pro).
- Le handler `applyAuthResult` mute le token en mémoire **sur place** (même
  objet `AuthProvider`), donc appeler `Login` puis se connecter avec un autre
  compte **change bien le compte du process vivant sans redémarrage** — mais
  au prix d'un login navigateur interactif à chaque bascule.

Preuve dans les logs (`cli.log`, PID 1389936, sans redémarrage) : le process
a démarré non-authentifié, s'est authentifié en `compte@example.com` en
pleine session à 19:25, et a continué à tourner/rafraîchir ensuite.

## La solution pratique retenue : switch disque + `agy --continue`

`agy` **persiste toutes les conversations sur disque**
(`~/.gemini/antigravity-cli/conversations/*.db`, `history.jsonl`) et sait les
reprendre :
- `agy --continue` (ou `-c`) : reprend la conversation la plus récente.
- `agy --conversation <id>` : reprend une conversation par ID.

Donc le workflow qui préserve **le compte ET le contexte**, **sans navigateur**
(on a des tokens valides stockés) :

1. `cli-switch agy <name>` → écrit le token du profil cible sur le disque.
2. Nouveau terminal → `agy --continue` → un process neuf lit le token du
   compte cible ET reprend la même conversation.

Le seul "coût" par rapport au hot-swap de Claude : c'est un nouveau process
(nouveau terminal), pas la même fenêtre. Mais la conversation continue à
l'identique, sur le quota du nouveau compte, sans re-login.

### Option avancée (hot-swap dans la MÊME fenêtre)

Pour rester dans la fenêtre `agy` déjà ouverte, la seule voie est la RPC
`Login` sur le serveur local du process → OAuth navigateur en tant que compte
cible. Préserve la session en mémoire, mais interactif à chaque bascule.
Endpoint (port = fd 11/12 du PID, via `ss -tlnp | grep pid=<PID>`) :
`POST http://127.0.0.1:<port>/exa.language_server_pb.LanguageServerService/Login`
(mutant — nécessite le consentement de l'utilisateur avant tout test).

## Résumé décisionnel

| Approche | Même fenêtre ? | Sans navigateur ? | Contexte gardé ? | Statut |
|----------|:--:|:--:|:--:|--------|
| Swap fichier disque (process vivant) | — | — | — | **Impossible** (prouvé) |
| Injection token via RPC | oui | oui | oui | **N'existe pas** (LoginRequest sans champ token) |
| RPC `Login` (OAuth navigateur) | oui | non | oui | Possible, interactif |
| **Switch disque + `agy --continue`** | non (new term.) | **oui** | **oui** | **Recommandé** |
| Injection mémoire ptrace/gdb | oui | oui | oui | Écarté (fragile/risqué) |
