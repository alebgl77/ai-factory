/* SPDX-License-Identifier: MIT */
(() => {
  'use strict';
  const bilingual = (en, fr) => ({ en, fr });
  const guided = window.FactoryDemoDecision;
  const samples = [
    {
      id: guided.id, revision: guided.revision, kind: 'action', priority: 85,
      title: bilingual('Aster opportunity · prepare an order', 'Opportunité Aster · préparer une commande'),
      reason: bilingual('The CRM opportunity is marked won. Review the exact draft before the simulated ERP records it.',
        'L’opportunité CRM est gagnée. Examinez le brouillon exact avant son enregistrement dans l’ERP simulé.'),
      action: { type: 'erp.order.create_draft', fields: { customer: 'Aster', opportunity: guided.opportunity, amount: '2 450 EUR' } },
      evidence: [bilingual(`${guided.evidenceLabel.en} · ${guided.opportunity}`, `${guided.evidenceLabel.fr} · ${guided.opportunity}`),
        bilingual('ERP customer · ASTER-014', 'Client ERP · ASTER-014'),
        bilingual('Expected change · one new draft order', 'Modification prévue · une nouvelle commande brouillon')]
    },
    {
      id: 'DEMO-105', kind: 'strategic', priority: 70,
      title: bilingual('Extend the pilot to another region', 'Étendre le pilote à une autre région'),
      reason: bilingual('The scope change needs a decision from the pilot owner.',
        'Le changement de périmètre nécessite une décision du responsable du pilote.'),
      action: null, evidence: [bilingual('The current scope covers one region.', 'Le périmètre actuel couvre une région.')]
    },
    {
      id: 'DEMO-106', kind: 'undocumented', priority: 63,
      title: bilingual('Define the split-order rule', 'Définir la règle des commandes fractionnées'),
      reason: bilingual('The published procedure leaves this case open.',
        'La procédure publiée laisse ce cas ouvert.'),
      action: null, evidence: [bilingual('No applicable step was found in the sample procedure.',
        'Aucune étape applicable trouvée dans la procédure de démonstration.')]
    }
  ];

  function createDemo() {
    const cases = new Map(samples.map(item => {
      const revision = item.revision ?? 1;
      return [item.id, { ...item, status: 'waiting_review', revision, proposal_hash: `demo-${item.id}-${revision}` }];
    }));
    let sequence = 106;
    const present = (item, lang) => ({
      ...item,
      title: item.title[lang],
      reason: item.reason[lang],
      evidence: item.evidence.map(line => line[lang])
    });
    return {
      cases(lang) {
        return { mode: 'demo', capabilities: { can_decide: true, chat_source: 'simulation' },
          cases: [...cases.values()].map(item => present(item, lang)) };
      },
      decide(id, input, lang) {
        const item = cases.get(id);
        if (!item || item.status !== 'waiting_review' || item.revision !== input.revision ||
            item.proposal_hash !== input.proposal_hash) throw Object.assign(new Error('stale'), { status: 409 });
        if (input.decision === 'approve' && item.kind === 'action') {
          item.status = 'executed';
          item.receipt = { id: `ERP-DRAFT-${id.split('-').pop()}`, executed_at: new Date().toISOString() };
        } else {
          item.status = { approve: 'decided', reject: 'rejected', request_changes: 'changes_requested' }[input.decision];
        }
        item.decision = input.decision;
        item.rationale = input.rationale;
        item.decided_at = new Date().toISOString();
        return present(item, lang);
      },
      feedback(id, input) {
        cases.get(id).feedback = input;
        return { recorded: true };
      },
      history(id) {
        const item = cases.get(id);
        return {
          decisions: item.decision ? [{
            decision: item.decision, revision: item.revision, rationale: item.rationale, created_at: item.decided_at
          }] : [],
          feedback_counts: item.feedback ? { [item.feedback.rating]: 1 } : {},
          my_feedback: item.feedback
        };
      },
      chat(input, lang) {
        const text = input.message.normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase();
        const strategic = /strateg|budget|scope|perimetre|priority|priorite/.test(text);
        const undocumented = /undocumented|non document|unknown|inconnu|human review|revue humaine|humain/.test(text);
        if (strategic || undocumented) {
          const id = `DEMO-${++sequence}`;
          cases.set(id, {
            id, kind: strategic ? 'strategic' : 'undocumented', priority: 90, status: 'waiting_review',
            revision: 1, proposal_hash: `demo-${id}-1`, action: null,
            title: bilingual('Review requested in chat', 'Revue demandée dans la conversation'),
            reason: bilingual('The agent requested a human decision.', 'L’agent a demandé une décision humaine.'),
            evidence: [bilingual(input.message, input.message)]
          });
          return {
            answer: bilingual('I sent this choice to the decision inbox. Review the question there.',
              'J’ai transmis ce choix à la boîte de décision. Retrouvez la question dans le dossier.')[lang],
            escalated: true, case_id: id, source: 'simulation'
          };
        }
        const answers = {
          operations: bilingual('Check the CRM event and ERP customer reference before reviewing the proposal.',
            'Vérifiez l’événement CRM et la référence du client ERP avant d’examiner la proposition.'),
          finance: bilingual('Check the customer, currency and amount against the original opportunity.',
            'Comparez le client, la devise et le montant avec l’opportunité d’origine.'),
          governance: bilingual('Review the evidence and give a reason in the decision inbox.',
            'Vérifiez les preuves et indiquez un motif dans la boîte de décision.')
        };
        return { answer: answers[input.agent][lang], escalated: false, source: 'simulation' };
      }
    };
  }

  window.createCompanionDemo = createDemo;
})();
