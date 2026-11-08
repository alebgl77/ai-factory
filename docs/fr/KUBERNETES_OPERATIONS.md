# Exploiter AI Factory sur Kubernetes

[English](../KUBERNETES_OPERATIONS.md) · **Français** · [Installation](KUBERNETES.md)

## État et restauration de la Factory

Le PVC de la Factory contient le répertoire d’état de l’API, les bases SQLite et le journal `audit.jsonl` chaîné par hachage. Conservez une copie versionnée des valeurs Helm et des références d’images de la release. Sauvegardez le volume selon votre objectif de point de reprise. Stockez des copies chiffrées dans un domaine de panne distinct. Sauvegardez séparément les Secrets externes ou leur source et réservez l’accès au groupe d’exploitation.

Pour une sauvegarde à froid, suspendez tout contrôleur susceptible de relancer la charge, réduisez le Deployment à zéro réplique et attendez l’arrêt du Pod. Prenez un instantané CSI ou utilisez le mécanisme de sauvegarde du stockage tant que le PVC est au repos, puis remettez le Deployment en service. Pour une sauvegarde de fichiers, archivez ensemble tout le contenu du répertoire d’état, fichiers cachés compris. Excluez la racine de montage de l’archive. Une copie à chaud des fichiers SQLite pris isolément peut omettre des transactions ou diverger du journal d’audit.

Restaurez vers un nouveau PVC dans un espace de noms isolé. Affectez le nom du PVC restauré à `persistence.existingClaim` et démarrez avec les versions du chart et des images relevées à la sauvegarde. Pour une archive de fichiers, préservez les droits et le propriétaire du volume de destination lors de l’extraction, par exemple avec `--no-same-owner --no-same-permissions` de GNU tar sous l’UID de la charge. Vérifiez la santé de la Factory, relisez un élément connu de l’état restauré et, si le journal est présent, lancez `python -m app.audit verify /data/audit.jsonl` dans le conteneur API. Relevez la durée de restauration et la fenêtre de perte de données. Terminez la mise à niveau éventuelle avant de basculer le trafic. Restaurez et vérifiez les services externes selon leurs propres procédures.

## Télémétrie externe et stockage objet

Quand ils sont configurés, le chart utilise les services Prometheus, Grafana, Langfuse, LiteLLM et Qdrant fournis par l’exploitant. Provisionnez, mettez à jour et sauvegardez ces services séparément. Pour restaurer Langfuse, vérifiez que sa base, les données ClickHouse, le stockage objet et les identifiants correspondent au même déploiement et au même point de reprise. Appliquez les règles de rétention et de résidence des environnements destinataires aux métriques, traces et journaux.

Si vous utilisez SeaweedFS pour Langfuse, conservez les données sur un stockage persistant et placez l’identité S3 générée dans un volume privé et éphémère sous `/run/seaweedfs`, inscriptible par l’UID 1000. Le script de démarrage Compose reconstruit l’identité et remplace le fichier précédent de manière atomique, même si un démarrage antérieur a laissé le répertoire en mode `0500`. Si vous déployez un script équivalent sous Kubernetes, fournissez les deux identifiants S3 via un Secret existant. Coordonnez la rotation avec les clients Langfuse, redémarrez les charges concernées et vérifiez l’écriture et la lecture authentifiées dans le bucket prévu.

## Envoyer les journaux des Pods à Loki

Renseignez `logging.enabled: true` dans un espace de noms dédié pour déployer Alloy. Partez de l’[exemple de valeurs](../../deploy/helm/ai-factory/values-logging.example.yaml). Créez le Secret référencé dans l’espace de noms de la release avec votre gestionnaire de secrets. Les clés par défaut sont `username` et `password`. Renseignez `logging.loki.caKey` avec la clé du bundle PEM pour une CA privée, ou laissez la valeur vide pour utiliser le magasin de confiance de l’image. Configurez le point de réception HTTPS et `logging.loki.tenantId` si la destination le demande. Redémarrez le Deployment Alloy après modification du Secret pour appliquer les nouvelles variables et les montages.

Déclarez les couples CIDR et port TCP précis pour l’API Kubernetes et Loki. L’adresse virtuelle et le port du Service peuvent différer de la destination observée par le CNI après traduction d’adresse. Vérifiez les trajets autorisés et refusés sur le CNI cible. Pour DNS, configurez l’espace de noms et les étiquettes des Pods résolveurs ou indiquez des adresses explicites comme les IP NodeLocal DNS dans `logging.egress.dnsCIDRs`. Alloy n’a aucune autorisation d’entrée et son point d’état écoute sur le bouclage. La politique du collecteur est indépendante de celle du Pod Factory. En démo, la journalisation ajoute une exception de sortie propre à Alloy lorsqu’elle est activée. [Kubernetes NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/)

Alloy sélectionne les étiquettes de Pods de la release Factory et suit les logs via `pods/log`. Le Role autorise la lecture des métadonnées et des journaux de Pods dans l’espace de noms. Le RBAC Kubernetes n’applique pas de sélection par étiquette pour ces lectures. Un token Alloy compromis peut donc lire les logs d’autres Pods de cet espace de noms. Dédiez l’espace de noms à la release et à sa frontière de confiance. Vérifiez que `kubectl auth can-i get pods --subresource=log -n "$NS" --as="system:serviceaccount:$NS:$ALLOY"` renvoie `yes` et que les vérifications équivalentes sur les Secrets ou sur les logs de Pods d’un autre espace de noms renvoient `no`. [Source des logs Kubernetes dans Alloy](https://grafana.com/docs/alloy/latest/reference/components/loki/loki.source.kubernetes/)

L’étiquette `service` contient le nom du conteneur et `project` le nom de la release Helm. La source Kubernetes Alloy reçoit une réponse de log combinée, sans provenance stdout/stderr pour une étiquette `stream`. Le pipeline masque les mêmes motifs de tokens reconnaissables que sous Compose et écarte les lignes de plus de 128 Kio. Utilisez le masquage comme seconde couche après l’expurgation côté application. La position de lecture est éphémère. La livraison peut perdre ou dupliquer des lignes après un redémarrage, une éviction ou une panne prolongée de la destination. Configurez la rétention, les accès, les alertes et le transfert durable au SIEM côté destination. Transmettez les fichiers d’audit séparément via l’export approuvé ou la sauvegarde.

### Vérifier la livraison et le masquage

Utilisez un espace de noms jetable et un tenant ou point de réception Loki de test. Préparez le chart, puis installez-le avec vos valeurs de journalisation et le Secret référencé. Faites correspondre `NS` et `RELEASE` à l’installation et renseignez `ALLOY` avec le nom du Deployment du collecteur. Le nom par défaut se termine par `-ai-factory-alloy`. Attendez le succès de `kubectl -n "$NS" rollout status "deployment/$ALLOY"`, puis exécutez la commande Bash suivante. Les données émises sont synthétiques.

```bash
marker="factory-log-probe-$(date +%s)"
for project in "$RELEASE" unrelated-release; do
  kubectl -n "$NS" apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: probe-$project
  labels:
    app.kubernetes.io/name: ai-factory
    app.kubernetes.io/instance: $project
    app.kubernetes.io/component: factory
spec:
  restartPolicy: Never
  automountServiceAccountToken: false
  securityContext:
    runAsNonRoot: true
    runAsUser: 65532
    seccompProfile: {type: RuntimeDefault}
  containers:
    - name: probe
      image: busybox:1.37.0
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities: {drop: [ALL]}
      command: [sh, -ec]
      args:
        - echo 'level=INFO $marker release=$project sk-syntheticprobe123'; sleep 120
EOF
done
kubectl -n "$NS" wait --for=condition=Ready --timeout=90s "pod/probe-$RELEASE" pod/probe-unrelated-release
printf '%s\n' "$marker"
```

Dans Grafana Explore, interrogez la destination de test avec `{factory="ai-factory-reference",service="probe"} |= "<marqueur affiché>"`. Après réception du premier lot, vérifiez la présence d’une ligne avec `project` égal à `$RELEASE`, `level="info"` et le token synthétique remplacé par `***`. Confirmez avec `kubectl -n "$NS" logs pod/probe-unrelated-release` que le Pod de l’autre release a bien émis sa ligne, puis vérifiez son absence dans Loki. Supprimez les Pods de test après ce contrôle.
