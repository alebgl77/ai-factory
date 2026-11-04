# Security policy

**English** · [Français](#politique-de-sécurité-français)

Virtual AI Factory is an open-source (MIT) reference stack. This file explains how to report a
vulnerability, what we commit to, and how to verify a release before you run it. The security
architecture, the threat model and the controls matrix are in
[docs/SECURITY.md](docs/SECURITY.md), [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) and
[docs/SECURITY_CONTROLS.md](docs/SECURITY_CONTROLS.md).

## Supported versions

| Version | Security fixes |
|---|---|
| `main` (unreleased) | Yes: every fix lands here first |
| 1.0.x (latest release line) | Yes |
| Older releases | No: upgrade to the latest release |

A fix is released as a new patch version of the latest release line. The release notes and a
GitHub security advisory describe the impact and the upgrade path.

## Reporting a vulnerability

**Do not open a public issue, pull request or discussion for a vulnerability.**

Use GitHub private vulnerability reporting:
**[Report a vulnerability](https://github.com/alebgl77/ai-factory/security/advisories/new)**
(repository `alebgl77/ai-factory`, tab *Security*, *Report a vulnerability*). Only the
maintainers can see the report. Every deployment of the stack also publishes this contact in
`/.well-known/security.txt` (RFC 9116).

Please include:

- the affected component (API, edge, gateway callback, compose configuration, dashboard, CI
  workflows, release artefacts) and the version or commit;
- the configuration that matters (for example `FACTORY_AUTH`, `FACTORY_AUTH_MODE`, profiles);
- the steps to reproduce, the impact you observed, and a proof of concept if you have one;
- whether the issue is already public or known to others.

Never put real personal data, a real secret or a working key in a report. If the private form is
not available, open a public issue that only asks for a security contact, with no technical
detail.

## What we commit to

The project is maintained by its author and contributors on a best-effort basis. These are our
targets, counted from the receipt of a complete report:

| Step | Target |
|---|---|
| Acknowledgement | within **72 hours** |
| Triage: confirmed or not, severity (CVSS v4.0), affected versions | within **7 days** |
| Fix or documented mitigation, critical and high severity | target 30 days |
| Fix or documented mitigation, medium and low severity | next planned release, target 90 days |
| Coordinated disclosure | when the fix is released, or after 90 days by default, whichever comes first; we agree the date with you |

We keep you informed at each step, publish a GitHub security advisory (with a CVE when it
applies), and credit you unless you ask us not to.

## Scope

In scope: the code and configuration of this repository and the images built from it:

- the aggregation API (`api/`): authentication, RBAC, four-eyes rule, audit chain, Grafana
  proxy, input handling;
- the edge configuration (`compose/caddy/Caddyfile`): headers, identity header handling, SSO
  integration, request limits;
- the gateway callback (`compose/litellm/factory_callbacks.py`): sensitive-data routing,
  guardrails, PII redaction, metrics;
- the stack definition (`docker-compose.yml`, `compose/`): container hardening, networks, the
  Docker socket proxy, default settings;
- the dashboard (`dashboard/`), the simulators (`sim/`), the scripts (`scripts/`);
- the CI/CD workflows and release artefacts (`.github/`): workflow injection, token permissions,
  signatures, provenance, SBOMs.

Out of scope (please report to the project concerned):

- vulnerabilities in third-party software used as unmodified services or images (Caddy,
  LiteLLM, Langfuse, Grafana, Loki, Alloy, Prometheus, Qdrant, PostgreSQL, ClickHouse, Valkey,
  SeaweedFS, oauth2-proxy, Ollama, vLLM, NVIDIA exporters), unless our configuration makes them
  exploitable;
- the behaviour of the key-less demo by design: with `FACTORY_AUTH_MODE=none` every request acts
  as `admin` and the posture reports `status: demo`; the placeholder values of `.env.example`;
- the static demo on GitHub Pages and GitHub's own infrastructure;
- denial of service by traffic volume, social engineering, physical attacks, and findings that
  need a compromised Docker host or administrator.

## Safe harbour

We will not take legal action against you, nor ask anyone to, for security research that:

- is done in good faith, on your own deployment of the stack or on test data, never on someone
  else's deployment without their written permission;
- avoids privacy violations, data destruction and service disruption, and stops as soon as you
  reach data that is not yours;
- reports the vulnerability to us privately, as described above, and gives us reasonable time to
  fix it before any disclosure.

For such research we consider your activity authorised with respect to this project, and we will
say so publicly if a third party questions it. We cannot grant authorisation for systems we do
not own (for example a company that runs this stack, or GitHub): ask their owner. If in doubt,
ask us first through the private reporting form.

## Verify a release

Every release from `v1.0.0` on is built by
[`.github/workflows/release.yml`](.github/workflows/release.yml) on GitHub-hosted runners, from a
tag on the default branch, and published as `ghcr.io/alebgl77/<name>` (`api`, `dashboard`,
`mock-llm`, `traffic`, `gpu-sim`, `grafana-bootstrap`; `linux/amd64`). Before any push, each
image must pass a Trivy gate (no known fixable HIGH or CRITICAL vulnerability, no secret). Each
published image is then signed keyless with Sigstore cosign, carries a SLSA build provenance
attestation and two SBOM attestations (SPDX 2.3 and CycloneDX), and is verified by the workflow
itself with the script below. The release also ships the SBOM files, the attestation bundles and
a `SHA256SUMS` file that is signed keyless too.

Requirements: [cosign](https://github.com/sigstore/cosign) v3 and the
[GitHub CLI](https://cli.github.com/) (`gh attestation`). Pull and run images **by digest**: the
release notes list the digest of every image.

```sh
# 1. Signature: made by the release workflow of alebgl77/ai-factory, on a v* tag
cosign verify ghcr.io/alebgl77/api:1.0.0 \
  --certificate-identity-regexp '^https://github\.com/alebgl77/ai-factory/\.github/workflows/release\.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

# 2. SLSA build provenance, then the SBOM attestations
gh attestation verify oci://ghcr.io/alebgl77/api:1.0.0 --repo alebgl77/ai-factory
gh attestation verify oci://ghcr.io/alebgl77/api:1.0.0 --repo alebgl77/ai-factory \
  --predicate-type https://spdx.dev/Document/v2.3
gh attestation verify oci://ghcr.io/alebgl77/api:1.0.0 --repo alebgl77/ai-factory \
  --predicate-type https://cyclonedx.org/bom

# 3. Release assets (SBOMs, attestation bundles): signed checksums
gh release download v1.0.0 --repo alebgl77/ai-factory --dir release-v1.0.0
cd release-v1.0.0
cosign verify-blob SHA256SUMS --bundle SHA256SUMS.sigstore.json \
  --certificate-identity-regexp '^https://github\.com/alebgl77/ai-factory/\.github/workflows/release\.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
sha256sum -c SHA256SUMS
```

All image checks at once (signature, provenance, both SBOMs; signer workflow pinned, self-hosted
runners refused), the same script the release workflow runs before it publishes:

```sh
.github/scripts/verify_image.sh ghcr.io/alebgl77/api@sha256:<digest>
```

Replace `api` with the image you deploy and `1.0.0` with the version. Third-party images of the
stack (Caddy, LiteLLM, Langfuse, Grafana...) are not signed by this project: they are pinned by
version in `docker-compose.yml`, listed with their licence in `licenses/third_party.yaml`, and
their scan results are in [compose/README.md](compose/README.md#image-scan-trivy).

---

# Politique de sécurité (français)

[English](#security-policy) · **Français**

Virtual AI Factory est une stack de référence open source (MIT). Ce fichier explique comment
signaler une vulnérabilité, nos engagements, et comment vérifier une release avant de l'exécuter.
L'architecture de sécurité, le modèle de menaces et la matrice des contrôles sont dans
[docs/fr/SECURITY.md](docs/fr/SECURITY.md), [docs/fr/THREAT_MODEL.md](docs/fr/THREAT_MODEL.md) et
[docs/fr/SECURITY_CONTROLS.md](docs/fr/SECURITY_CONTROLS.md).

## Versions maintenues

| Version | Correctifs de sécurité |
|---|---|
| `main` (non publiée) | Oui : tout correctif y arrive d'abord |
| 1.0.x (dernière ligne de release) | Oui |
| Releases plus anciennes | Non : passez à la dernière release |

Un correctif est publié comme nouvelle version de correction de la dernière ligne de release. Les
notes de release et un avis de sécurité GitHub décrivent l'impact et la mise à jour.

## Signaler une vulnérabilité

**N'ouvrez ni issue, ni pull request, ni discussion publique pour une vulnérabilité.**

Utilisez le signalement privé de vulnérabilité de GitHub :
**[Signaler une vulnérabilité](https://github.com/alebgl77/ai-factory/security/advisories/new)**
(dépôt `alebgl77/ai-factory`, onglet *Security*, *Report a vulnerability*). Seuls les mainteneurs
voient le signalement. Chaque déploiement de la stack publie aussi ce contact dans
`/.well-known/security.txt` (RFC 9116).

Merci d'indiquer :

- le composant concerné (API, proxy de bord, callback de la passerelle, configuration compose,
  dashboard, workflows de CI, artefacts de release) et la version ou le commit ;
- la configuration utile (par exemple `FACTORY_AUTH`, `FACTORY_AUTH_MODE`, profils) ;
- les étapes de reproduction, l'impact constaté et une preuve de concept si vous en avez une ;
- si le problème est déjà public ou connu d'autres personnes.

Ne mettez jamais de données personnelles réelles, de secret réel ou de clé valide dans un
signalement. Si le formulaire privé n'est pas disponible, ouvrez une issue publique qui demande
seulement un contact sécurité, sans aucun détail technique.

## Nos engagements

Le projet est maintenu par son auteur et ses contributeurs, au mieux de leurs moyens. Voici nos
objectifs, comptés à partir de la réception d'un signalement complet :

| Étape | Objectif |
|---|---|
| Accusé de réception | sous **72 heures** |
| Tri : confirmé ou non, sévérité (CVSS v4.0), versions touchées | sous **7 jours** |
| Correctif ou contournement documenté, sévérité critique et haute | objectif 30 jours |
| Correctif ou contournement documenté, sévérité moyenne et basse | prochaine release prévue, objectif 90 jours |
| Divulgation coordonnée | à la publication du correctif, ou après 90 jours par défaut, au premier des deux termes ; la date est convenue avec vous |

Nous vous tenons informé à chaque étape, publions un avis de sécurité GitHub (avec un CVE quand
c'est pertinent) et vous créditons, sauf si vous préférez l'anonymat.

## Périmètre

Dans le périmètre : le code et la configuration de ce dépôt et les images construites à partir de
lui :

- l'API d'agrégation (`api/`) : authentification, RBAC, règle des quatre yeux, chaîne d'audit,
  proxy Grafana, traitement des entrées ;
- la configuration du proxy de bord (`compose/caddy/Caddyfile`) : en-têtes, traitement des
  en-têtes d'identité, intégration SSO, limites de requêtes ;
- le callback de la passerelle (`compose/litellm/factory_callbacks.py`) : routage des données
  sensibles, garde-fous, masquage des données personnelles, métriques ;
- la définition de la stack (`docker-compose.yml`, `compose/`) : durcissement des conteneurs,
  réseaux, proxy du socket Docker, réglages par défaut ;
- le dashboard (`dashboard/`), les simulateurs (`sim/`), les scripts (`scripts/`) ;
- les workflows de CI/CD et les artefacts de release (`.github/`) : injection dans les
  workflows, permissions des jetons, signatures, provenance, SBOM.

Hors périmètre (signalez-les au projet concerné) :

- les vulnérabilités des logiciels tiers utilisés comme services ou images non modifiés (Caddy,
  LiteLLM, Langfuse, Grafana, Loki, Alloy, Prometheus, Qdrant, PostgreSQL, ClickHouse, Valkey,
  SeaweedFS, oauth2-proxy, Ollama, vLLM, exporteurs NVIDIA), sauf si notre configuration les rend
  exploitables ;
- le comportement voulu de la démo sans clé : avec `FACTORY_AUTH_MODE=none`, toute requête agit
  comme `admin` et la posture indique `status: demo` ; les valeurs fictives de `.env.example` ;
- la démo statique sur GitHub Pages et l'infrastructure de GitHub ;
- le déni de service par volume de trafic, l'ingénierie sociale, les attaques physiques, et ce qui
  suppose un hôte Docker ou un administrateur déjà compromis.

## Sphère de sécurité (safe harbour)

Nous n'engagerons aucune poursuite contre vous, et ne demanderons à personne de le faire, pour une
recherche de sécurité qui :

- est menée de bonne foi, sur votre propre déploiement de la stack ou sur des données de test,
  jamais sur le déploiement d'un tiers sans son autorisation écrite ;
- évite toute atteinte à la vie privée, destruction de données ou interruption de service, et
  s'arrête dès que vous atteignez des données qui ne sont pas les vôtres ;
- nous signale la vulnérabilité en privé, comme décrit plus haut, et nous laisse un délai
  raisonnable pour la corriger avant toute divulgation.

Pour une telle recherche, nous considérons votre activité comme autorisée vis-à-vis de ce projet,
et nous le dirons publiquement si un tiers la conteste. Nous ne pouvons pas autoriser de tests sur
des systèmes qui ne nous appartiennent pas (par exemple une entreprise qui exploite cette stack, ou
GitHub) : demandez à leur propriétaire. En cas de doute, demandez-nous d'abord via le formulaire
privé.

## Vérifier une release

Chaque release à partir de `v1.0.0` est construite par
[`.github/workflows/release.yml`](.github/workflows/release.yml) sur des runners hébergés par
GitHub, depuis un tag de la branche par défaut, et publiée sous `ghcr.io/alebgl77/<nom>` (`api`,
`dashboard`, `mock-llm`, `traffic`, `gpu-sim`, `grafana-bootstrap` ; `linux/amd64`). Avant toute
publication, chaque image doit passer une barrière Trivy (aucune vulnérabilité HIGH ou CRITICAL
connue et corrigeable, aucun secret). Chaque image publiée est ensuite signée sans clé avec
Sigstore cosign, porte une attestation de provenance SLSA et deux attestations SBOM (SPDX 2.3 et
CycloneDX), et le workflow la vérifie lui-même avec le script ci-dessous. La release contient
aussi les fichiers SBOM, les bundles d'attestation et un fichier `SHA256SUMS`, lui aussi signé
sans clé.

Prérequis : [cosign](https://github.com/sigstore/cosign) v3 et la
[CLI GitHub](https://cli.github.com/) (`gh attestation`). Tirez et exécutez les images **par
digest** : les notes de release donnent le digest de chaque image. Les commandes sont celles de la
[section anglaise](#verify-a-release) ; en une fois, pour une image :

```sh
.github/scripts/verify_image.sh ghcr.io/alebgl77/api@sha256:<digest>
```

Remplacez `api` par l'image déployée et `1.0.0` par la version. Les images tierces de la stack
(Caddy, LiteLLM, Langfuse, Grafana...) ne sont pas signées par ce projet : elles sont épinglées par
version dans `docker-compose.yml`, listées avec leur licence dans `licenses/third_party.yaml`, et
leurs résultats d'analyse figurent dans [compose/README.fr.md](compose/README.fr.md).
