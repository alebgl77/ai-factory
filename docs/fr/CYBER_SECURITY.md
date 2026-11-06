# Opérations Cyber et preuves d’audit

[English](../CYBER_SECURITY.md)

La brique Cyber facultative fournit au SOC des détections locales et des exports JSONL vers son SIEM. Elle donne aux auditeurs clients une méthode reproductible de vérification d’un journal scellé. Le rapport de vulnérabilités présente séparément les composants déployés et les processus métier qui en dépendent.

## Activer la collecte pour un espace de travail

L’audit plateforme enregistre les opérations de gouvernance prises en charge dans `audit.jsonl`, sous le répertoire d’état. Workbench ajoute un journal à sa base SQLite. Il enregistre les propositions et révisions de dossiers, leurs retraits et blocages, les décisions et leurs canaux, les prises en charge, exécutions, blocages et résultats ERP incertains, ainsi que les points de reprise CRM. L’événement `execution.dispatch_authorized` consigne l’autorisation finale avant une nouvelle écriture ERP. L’événement de fin d’exécution confirme le résultat. Les événements contiennent des références et des métadonnées de révision. Le schéma écarte les prompts, les textes de justification, les jetons et les données métier. Les changements d’état Workbench et leurs entrées de journal partagent une transaction SQLite.

La journalisation Workbench est désactivée par défaut. Définissez `FACTORY_WORKBENCH_CYBER_AUDIT=on` pour l’initialiser sur une base sans politique lorsque Workbench est actif, ou passez `cyber_audit=True` à l’initialisation du SDK Workbench. La politique persiste dans la base. Un opérateur peut la modifier explicitement après l’initialisation de la base par Workbench. L’activation et la désactivation sont journalisées. La collecte commence à l’activation.

Depuis le répertoire `api/` du dépôt, exécutez la commande avec un compte opérateur autorisé à écrire dans la base Workbench.

```bash
python -m app.cyber journal --path /data/workbench-protected_demo.sqlite3 --enabled on
# Pour suspendre la collecte de cette base, utilisez --enabled off.
```

Conservez `FACTORY_WORKBENCH_MODE=off` dans la configuration de production entreprise qui l’exige. L’audit plateforme y reste une source de preuves Cyber. L’activation du journal Workbench concerne une instance Workbench configurée séparément. Consultez le [guide de durcissement](SECURITY.md).

## Sceller et vérifier un journal

Utilisez un collecteur ou un hôte de vérification séparé, avec un accès en lecture seule aux données d’audit. Gardez sa clé HMAC et le dernier scellé de confiance hors du périmètre de l’application qui écrit le journal. Fournissez au moins 32 octets aléatoires binaires dans un fichier à accès restreint. Réservez ce fichier au propriétaire sous POSIX et à l’identité du collecteur via une ACL sous Windows. Séparez les emplacements des clés et des scellés. Conservez les clés nécessaires à la vérification des scellés historiques.

Attribuez un nom de flux distinct et stable à chaque source et déploiement. Cet exemple suppose que le collecteur dispose déjà de la source, du fichier de clé et d’un répertoire de scellés protégé. Pour l’audit plateforme, utilisez `--kind platform --path /data/audit.jsonl` avec son propre nom de flux.

```bash
cd api
python -m app.cyber seal --kind workbench --path /data/workbench-protected_demo.sqlite3 \
  --stream customer-a-workbench --key-file /secure/keys/cyber-01.key --key-id cyber-01 \
  --output /evidence/checkpoints/0001.json

python -m app.cyber verify --kind workbench --path /data/workbench-protected_demo.sqlite3 \
  --stream customer-a-workbench --key-file /secure/keys/cyber-01.key \
  --checkpoint /evidence/checkpoints/0001.json
```

Le premier scellé établit le point de départ de confiance. Pour chaque scellé suivant, passez le dernier scellé conservé indépendamment via `--previous` et choisissez un nouveau fichier de sortie. Lors d’une rotation, utilisez les nouveaux `--key-file` et `--key-id` et indiquez l’ancienne clé avec `--previous-key-file`. Un scellé authentifie le préfixe du journal jusqu’au numéro de séquence enregistré. La vérification contrôle ce préfixe et la chaîne locale. `unsigned_tail` compte les événements plus récents hors du préfixe authentifié.

Conservez les scellés dans un stockage administré séparément avec des contrôles d’immutabilité et une rétention adaptés à votre politique de conservation des preuves. Protégez également le journal source et la clé nécessaire à sa vérification. Un scellé détenu indépendamment permet de détecter une réécriture ou une troncature de son préfixe authentifié. Les modifications de la fin non scellée sont hors de cette garantie. Le HMAC est symétrique. Quiconque détient la clé peut aussi produire un scellé. Réservez-la aux opérateurs habilités à vérifier la source. [Guide OWASP sur la journalisation](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html) · [Guide NIST sur la gestion des journaux](https://csrc.nist.gov/pubs/sp/800/92/final)

## Transmettre les preuves au SOC et à l’audit client

Utilisez la même source, le même flux, la même clé et le même scellé que pour la vérification. Acheminez l’export JSONL vers le SIEM via votre collecteur et votre transport approuvés. Pour une vérification d’audit, conservez une copie de la source et du scellé pour le vérificateur habilité. L’export minimisé sert à l’investigation et au partage selon vos règles d’accès. Son champ `hash` référence l’enregistrement source. Le scellé authentifie la source.

```bash
python -m app.cyber export --kind workbench --path /data/workbench-protected_demo.sqlite3 \
  --stream customer-a-workbench --key-file /secure/keys/cyber-01.key \
  --checkpoint /evidence/checkpoints/0001.json --after 0 --limit 1000

python -m app.cyber detect --kind workbench --path /data/workbench-protected_demo.sqlite3 \
  --stream customer-a-workbench --key-file /secure/keys/cyber-01.key \
  --checkpoint /evidence/checkpoints/0001.json --after 0 --limit 1000 --fail-on high
```

Utilisez `event_id`, composé du flux et de la séquence, pour dédupliquer les exports et relier les alertes à leurs preuves. Enregistrez durablement la dernière séquence après confirmation de l’ingestion. Pour les détections, utilisez `scanned_through` comme prochaine valeur de `--after` et continuez tant que `more` vaut vrai, même sur une page sans alerte. Les références d’acteur et de dossier sont des pseudonymes liés au flux et à la clé. Elles peuvent changer après rotation.

`detect` applique des règles déterministes. Les blocages ERP, les exécutions incertaines et la désactivation du journal ont une sévérité haute. Les conflits CRM et les décisions plateforme refusées ont une sévérité moyenne. Les approbations enregistrées via un service sont informatives. Le planificateur local ou le collecteur peut transmettre les alertes JSON et surveiller les codes de sortie. Un échec d’intégrité produit une erreur critique sur stderr et bloque l’export.

| Sortie `app.cyber` | Signification |
| --- | --- |
| `0` | Commande terminée |
| `1` | Échec d’intégrité ou de vérification du scellé |
| `2` | Erreur de configuration, d’entrée ou d’accès aux fichiers |
| `3` | Une alerte atteint le seuil configuré par `detect --fail-on` |

Pour un audit client, citez le flux, la plage de séquences scellées et le résultat de la vérification. Pour le SOC, citez `evidence_id` et l’heure enregistrée de l’événement. Les preuves couvrent les opérations capturées et les périodes d’activation. Utilisez séparément les journaux d’authentification et la supervision de la plateforme pour l’activité HTTP hors de ces événements. La réponse à incident et la remédiation relèvent d’une décision opérateur.

## Suivre les CVE des composants déployés

Copiez l’[exemple d’inventaire](../../examples/cyber-inventory.example.json) dans un fichier géré par les opérateurs, remplacez les versions et alias d’exemple par ceux des déploiements observés, puis définissez `"enabled": true`. Laissez `false` pour écarter cette analyse à l’installation. Renseignez les URL de paquets (PURL) avec leur version exacte dans `packages`. Déclarez les liens entre l’environnement IA, les outils connectés et les processus qui en dépendent dans `depends_on`.

Générez un rapport JSON Trivy sur l’artefact déployé avec l’inventaire des paquets, puis transmettez ce rapport au corrélateur local. Fournissez `--kev` uniquement si vous disposez d’une copie locale approuvée du catalogue CISA.

```bash
# Analysez l’image déployée, idéalement épinglée par son empreinte.
trivy image --scanners vuln --list-all-pkgs --format json --output /evidence/trivy.json IMAGE
# Depuis api/
python -m app.cyber_cve --inventory /config/cyber-inventory.json \
  --trivy /evidence/trivy.json --kev /evidence/known-exploited-vulnerabilities.json \
  --format jsonl --fail-on high
```

Le corrélateur utilise les PURL versionnées exactes et suit les dépendances déclarées pour identifier les processus touchés. Il conserve les CVE sans correspondance. La couverture `observed` atteste de la présence d’un paquet. Les composants à version absente ou inconnue nécessitent une vérification séparée. Une vulnérabilité exploitée selon le catalogue CISA augmente la priorité indépendamment de la sévérité du scanner. Les résultats dépendent de l’exactitude de l’inventaire, de la couverture de Trivy et de l’actualité des rapports et du catalogue fournis. Le corrélateur fonctionne hors ligne. [Documentation Trivy](https://www.trivy.dev/docs/latest/guide/configuration/reporting/) · [Catalogue CISA KEV](https://github.com/cisagov/kev-data)

`app.cyber_cve` renvoie `0` en cas de succès ou de désactivation, `1` si un résultat atteint le seuil de priorité configuré et `2` en cas d’erreur d’entrée.

## Triage assisté par un agent analyste habilité

Installez manuellement le [skill de triage Cyber](../../api/ai_factory_workbench/agent_skills/ai-factory-cyber-triage/SKILL.md) dans un environnement analyste habilité. Fournissez à l’agent les exports de preuves vérifiés et normalisés ainsi que le rapport de corrélation approuvé. Le skill distingue les faits des inférences, cite les preuves et produit une note en lecture seule pour examen humain.
