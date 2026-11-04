# Sécurité

*[English version](../SECURITY.md)*

## Principes

- **Aucun secret dans le dépôt** : les valeurs viennent de `.env` (non versionné). `.env.example`
  ne contient que des valeurs fictives. Le CI lance gitleaks à chaque push.
- **Aucun jeton dans le navigateur** : le dashboard ne parle qu'à l'API d'agrégation, en même
  origine via le proxy du VPS. Le jeton Grafana (compte de service *Viewer*) est lu côté serveur
  dans `GRAFANA_TOKEN_FILE` et n'apparaît dans aucune réponse.
- **Proxy Grafana restreint** : uniquement des GET vers une liste blanche de chemins Grafana, et
  des requêtes PromQL *nommées* définies côté serveur. Le navigateur ne peut pas envoyer de PromQL
  arbitraire.
- **Surface exposée minimale** : seul `edge` publie des ports. LiteLLM, Prometheus, Qdrant,
  ClickHouse, Postgres, Valkey et SeaweedFS restent sur le réseau interne.
- **Données sensibles** : les classes C3 et C4 ne sont routées que vers l'alias `chat-sensitive`,
  servi par le GPU local. La politique de placement met la charge en file d'attente plutôt que de
  l'envoyer au cloud. Le dashboard signale toute réservation d'un use case sensible hors du Lab
  sécurisé.
- **Routage des données sensibles appliqué à la passerelle** : le callback MIT de LiteLLM
  redirige toute requête étiquetée C3/C4 vers `chat-sensitive` et masque son contenu dans
  Langfuse (`FACTORY_ENFORCE_SENSITIVE_ROUTING`).
- **Réseaux** : `factory` est interne, sans accès Internet ; seuls `edge`, `litellm`, Ollama et
  vLLM rejoignent le réseau `egress`.
- **Pas d'apprentissage sur les données** : `fine_tuning: false`. L'API refuse toute action
  `retrain` ou `fine_tune` dans la boucle de données.
- **La langue n'élargit pas la surface** : `?lang=en|fr` ne fait que choisir des textes de la
  configuration et du catalogue de messages (`api/app/i18n.py`) ; une valeur inconnue retombe
  sur la langue par défaut.

## Durcissement recommandé sur un VPS

1. Mettre `FACTORY_HOST` sur un nom de domaine réel : Caddy obtient alors un certificat TLS
   automatiquement.
2. Activer l'authentification du proxy (`FACTORY_AUTH=basic`, `FACTORY_BASIC_AUTH_USER`,
   `FACTORY_BASIC_AUTH_HASH` généré par `caddy hash-password`) ou placer un fournisseur OIDC
   (oauth2-proxy, Authelia…) devant `edge`.
3. Changer **tous** les mots de passe et clés de `.env.example`, par exemple avec `openssl rand -hex 32`.
4. Restreindre les ports entrants à 80 et 443, et relier le GPU local au VPS par un tunnel chiffré
   (WireGuard) plutôt que d'exposer vLLM.
5. Sauvegarder les volumes `postgres-data`, `clickhouse-data`, `seaweedfs-data` et `api-state`.

## Signaler une vulnérabilité

Ouvrez un signalement privé (*Security advisory*) sur le dépôt. Ne publiez ni preuve d'exploitation
ni secret dans une issue publique.
