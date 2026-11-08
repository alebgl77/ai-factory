# Déployer AI Factory sur Kubernetes

[English](../KUBERNETES.md) · **Français** · [Documentation sur GitHub](https://github.com/alebgl77/ai-factory/blob/main/docs/fr/README.md)

Le chart Helm exécute le tableau de bord, l’API et Caddy dans un même Pod. Le profil privé ajoute oauth2-proxy. Seul Caddy est exposé par le Service. Les autres conteneurs écoutent sur l’interface de bouclage et partagent la frontière de confiance du Pod. Utilisez une release par espace de noms et une release privée par organisation.

| Profil | Accès | Données et services |
|---|---|---|
| `demo` | `kubectl port-forward` local, sans authentification | Instantané fictif, Workbench de démonstration et état API local |
| `private` | Ingress HTTPS, OIDC et politique BU obligatoire | Pilote privé avec services de télémétrie et de modèles fournis et activés séparément par l’exploitant |

Pour la pile simulée complète et ses dépendances, suivez le [guide Compose](../DEPLOYMENT.md). L’exploitant Kubernetes fournit le contrôleur Ingress, le fournisseur d’identité, le stockage CSI, l’application des NetworkPolicy et les intégrations externes. L’API a un seul processus d’écriture et un PVC. Prévoyez une interruption lors du remplacement du Pod ou de la mise à niveau de la release.

## Lancer une démo locale

Depuis un clone Git, utilisez Docker, Python 3, Helm 3, kubectl et kind. Le CI utilise Helm 3.22.1, kind 0.33.0 et Kubernetes 1.37.0. Le script de préparation assemble le chart à partir des fichiers de configuration et des ressources suivis par le dépôt.

```bash
kind create cluster --name factory-demo \
  --image kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5
docker build -t ai-factory/api:local api/
docker build -t ai-factory/dashboard:local dashboard/
kind load docker-image --name factory-demo ai-factory/api:local ai-factory/dashboard:local

chart_dir="$(mktemp -d)"
python3 scripts/package_helm.py "$chart_dir"
helm lint --strict "$chart_dir/ai-factory"
kubectl create namespace factory-demo
kubectl label namespace factory-demo pod-security.kubernetes.io/enforce=restricted
helm upgrade --install factory "$chart_dir/ai-factory" -n factory-demo --wait
kubectl -n factory-demo port-forward --address 127.0.0.1 svc/factory-ai-factory 8080:8080
```

Ouvrez `http://127.0.0.1:8080/`. Dans un autre terminal, vérifiez `/healthz`, `/api/v1/health` et `/api/v1/snapshot` sur la même origine. Les droits de redirection de port et d’exécution dans un Pod peuvent contourner la frontière Ingress. Réservez-les aux exploitants habilités à consulter les données de la release.

## Installer un pilote privé

1. Vérifiez le bundle et ses images. Si le bundle inclut les artefacts Helm, associez `deploy/helm/ai-factory-${TAG#v}.tgz` et `deploy/helm/values.release.json` du même bundle vérifié. Les valeurs épinglent l’API, le tableau de bord, Caddy, oauth2-proxy et Alloy facultatif aux digests de `release-stack.json`. Les images de release ciblent Linux amd64. Pour une installation à partir des sources, préparez le chart depuis le clone comme pour la démo et fournissez des références d’images compatibles épinglées par digest. [Vérification de release](../DEPLOYMENT.md#vérifier-et-installer-une-release)
2. Créez un espace de noms dédié avec Pod Security au niveau Restricted et un CNI qui applique les NetworkPolicy. Provisionnez un PVC inscriptible pour un seul processus d’écriture via le pilote CSI. Vérifiez les droits sur les fichiers pour l’UID et le GID 10001, le mode d’accès et les garanties de verrouillage et de durabilité nécessaires à SQLite et aux fichiers d’état.
3. Configurez le contrôleur Ingress et l’entrée DNS de l’origine HTTPS. Provisionnez le Secret TLS et configurez la redirection HTTP vers HTTPS avant transfert des requêtes. La section TLS de l’Ingress configure la terminaison. Le contrôleur impose l’accès HTTPS. Restreignez l’accès au Service aux Pods approuvés du contrôleur. Conservez l’en-tête Host canonique et transférez les requêtes HTTP sur le port 8080. Maintenez le contrôleur et le backend dans la frontière de confiance retenue.
4. Enregistrez un client OIDC avec le rappel `https://<factory-host>/oauth2/callback`. L’émetteur HTTPS doit être joignable par le navigateur et le cluster. Placez `client-id`, `client-secret` et `cookie-secret` dans un Secret existant. Générez 32 octets aléatoires pour le secret de cookie et encodez-les en base64url. Pour une CA privée, placez le certificat sous `ca.crt` dans un Secret existant et renseignez `sso.providerCA.existingSecret`.
5. Créez un Secret séparé contenant la politique BU sous `business-units.yaml`. Faites correspondre les groupes de l’IdP à `api.roleMap` et à la politique. Ajoutez le scope de groupes ou la configuration des claims du fournisseur si nécessaire. Faites tourner les identifiants via le gestionnaire de secrets et redémarrez le Pod après modification des Secrets utilisés comme variables d’environnement ou lus au démarrage. [Politique d’exemple](../../config/business-units.example.yaml) · [Sécurité par BU](../BU_SECURITY.md)

Créez le fichier de valeurs opérateur avec les noms réels des Secrets et des sélecteurs étroits. Pour une installation signée, laissez les clés `images` et `logging.alloy.image` hors de ce fichier. Chargez le fichier `values.release.json` correspondant en dernier pour donner la priorité à ses digests vérifiés. L’exemple suppose que l’émetteur est servi dans le cluster et joignable sous le même nom DNS depuis le navigateur et le Pod. Pour un émetteur externe au cluster, utilisez les CIDR autorisés ou votre passerelle de sortie contrôlée en TCP 443.

```yaml
profile: private
ingress:
  enabled: true
  className: internal
  host: factory.example.org
  tlsSecretName: factory-tls
sso:
  issuerURL: https://login.example.org
  existingSecret: factory-oidc
  scope: openid email profile groups
  allowedEmailDomains: [example.org]
  allowedGroups: [factory-ops, factory-finance]
businessUnits:
  existingSecret: factory-business-units
api:
  roleMap: ["factory-ops:maintainer", "factory-finance:viewer"]
persistence:
  existingClaim: factory-state
networkPolicy:
  ingressPeers:
    - namespaceSelector:
        matchLabels: {kubernetes.io/metadata.name: ingress-system}
      podSelector:
        matchLabels: {app.kubernetes.io/name: ingress-controller}
  dnsPeers:
    - namespaceSelector:
        matchLabels: {kubernetes.io/metadata.name: kube-system}
      podSelector:
        matchLabels: {k8s-app: kube-dns}
  egressRules:
    - to:
        - namespaceSelector:
            matchLabels: {kubernetes.io/metadata.name: identity}
          podSelector:
            matchLabels: {app.kubernetes.io/name: oidc}
      ports: [{protocol: TCP, port: 443}]
```

```bash
helm upgrade --install factory "deploy/helm/ai-factory-${TAG#v}.tgz" -n factory \
  -f /secure/factory-private.yaml -f deploy/helm/values.release.json --wait --timeout 5m
kubectl -n factory rollout status deploy/factory-ai-factory
```

Avant la publication du bundle correspondant, qualifiez le commit source audité exact. Le socle testé est `27800aa798d7bf21e7bec4ab57ffa4461e0465c3`. Préparez le chart depuis ce clone comme pour la démo locale et utilisez `"$chart_dir/ai-factory"` à la place du chart empaqueté. Remplacez `values.release.json` par un fichier opérateur qui épingle les images du build et de la frontière approuvées par digest. Gardez ce fichier de valeurs d’images en dernier. [Qualification depuis les sources](../DEPLOYMENT.md#qualifier-depuis-les-sources)

Sur l’Ingress cible, vérifiez le refus d’accès anonyme à l’API, une authentification réelle et le refus de lecture et d’écriture inter-BU avec des comptes synthétiques. Qualifiez la politique TLS, la gestion des adresses clientes, les limites de requêtes et les sélecteurs réseau. Vérifiez que le contrôleur approuvé atteint le Service et qu’un Pod non autorisé est refusé. Utilisez les valeurs `telemetry` et des règles de sortie propres à chaque service externe. L’API omet la télémétrie des sources indisponibles et le profil privé utilise le mode de données réelles. Vérifiez pour chaque source active les identifiants, la confiance HTTPS, le chemin réseau et l’état opérationnel.

Si une source de télémétrie de l’API utilise une CA privée, renseignez `telemetry.caBundle.existingSecret` et placez le bundle PEM complet sous `ca-bundle.crt`, avec les racines publiques nécessaires aux autres sources. Redémarrez le Pod après rotation. Le moteur Portfolio facultatif attend `https://litellm:<port>`, un certificat valable pour le nom de Service `litellm` et un Secret opérateur référencé par `api.portfolioRunner.existingSecret`.

### Activer l’Observatoire privé

L’Observatoire est désactivé par défaut. Activez-le avec le profil privé et associez explicitement chaque groupe autorisé du fournisseur d’identité à `api.roleMap` et à la politique de BU. Le filtre de groupes OAuth reste applicable. La clé `shadow_teams` limite les lecteurs à leurs équipes. L’import et les propositions de politique exigent un périmètre global et le rôle adapté issus du même groupe autorisé. Confiez les décisions à un groupe global distinct d’approbateurs.

```yaml
api:
  shadowAI:
    enabled: true
    groups: [factory-ops]
    privacyThreshold: 7
```

Le seuil accepte les entiers de 5 à 100. Toute modification s’applique aux imports agrégés futurs. Intégrez le seuil à votre politique de collecte et vérifiez les règles de visibilité avec des données synthétiques avant d’importer des données réelles. Consultez la [sécurité par BU](../BU_SECURITY.md) et les [preuves d’audit](CYBER_SECURITY.md).

## Préserver l’état lors des mises à niveau et réinstallations

Relevez les valeurs Helm, la release source et le nom du PVC monté sur `/data`. Arrêtez les écritures et sauvegardez tout le répertoire avant un changement de version. Conservez ensemble les fichiers cachés, l’état, le journal d’audit et les bases SQLite. Testez la restauration sur un nouveau PVC avant de basculer le Deployment. Réutilisez le même profil avec un PVC donné. Pour changer de profil, créez un déploiement distinct et planifiez une migration de données explicite. [Sauvegarde, restauration et logs Alloy](KUBERNETES_OPERATIONS.md)

Le PVC créé par le chart porte l’annotation `helm.sh/resource-policy: keep`. Avant une désinstallation prévue, relevez son nom avec `kubectl -n factory get pvc -l app.kubernetes.io/instance=factory` et vérifiez la sauvegarde. Pour réinstaller sur ce PVC, confirmez que l’ancien Pod a disparu et que le PVC reste Bound, puis renseignez son nom exact dans `persistence.existingClaim`. La nouvelle release utilise le PVC conservé sans le créer ni le gérer. Renseignez toujours `persistence.existingClaim` pour un PVC fourni par l’exploitant. Conservez l’espace de noms et vérifiez la politique de rétention du PV. La suppression de l’espace de noms supprime les objets PVC indépendamment des annotations Helm. N’appliquez un retour arrière du code sur l’état existant qu’après avoir vérifié la compatibilité de format. Si elle est incertaine, restaurez le point validé sur un PVC séparé.

## Ce que vérifie le CI

Le job `Kubernetes (Helm and kind)` prépare et empaquette le chart, exécute le lint Helm strict et les cas négatifs de rendu, puis valide les schémas côté serveur dans un espace de noms Restricted. Il construit les images de l’API et du tableau de bord depuis les sources. Il vérifie les ressources du tableau de bord, la santé de l’API et l’instantané à travers Caddy. Il vérifie ensuite une écriture d’état et la chaîne d’audit après redémarrage du conteneur API, remplacement du Pod et restauration sur un deuxième PVC.

Le profil privé teste le flux OAuth2 par code avec PKCE et un Dex de test sous TLS. Il vérifie le filtrage des groupes, le cloisonnement BU et les tentatives de falsification d’en-têtes d’identité et de proxy avant et après connexion. Il vérifie l’écoute de l’API depuis l’espace réseau du Pod et la NetworkPolicy avec un proxy TLS de test autorisé et un Pod client refusé. Ce proxy simule le trajet Ingress. Qualifiez le contrôleur Ingress et le pilote de stockage de votre cible.

Le job vérifie également la livraison Alloy vers un Loki jetable sous TLS avec authentification Basic, le périmètre de collecte par release, les étiquettes, le masquage et les permissions Kubernetes du collecteur. [Preuves et périmètre du CI](https://github.com/alebgl77/ai-factory/blob/main/docs/fr/README.md#choisir-un-environnement)
