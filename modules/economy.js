// ============================================================
// Module Manifest : Économie
// Système économique avec daily, shop, et échanges
// ============================================================

module.exports = {
  id: 'economy',
  name: 'Économie',
  emoji: '💰',
  description: 'Monnaie virtuelle, daily/weekly, boutique, échanges entre membres.',
  category: 'engagement',

  dependencies: [],
  requiredPermissions: [
    'SendMessages',
    'EmbedLinks',
  ],

  configSchema: {
    currencyName: {
      type: 'string',
      maxLength: 50,
      required: false,
      default: 'pièces',
      label: 'Nom de la monnaie',
      description: 'Nom affiché de la monnaie virtuelle.',
    },
    currencySymbol: {
      type: 'string',
      maxLength: 10,
      required: false,
      default: '💰',
      label: 'Symbole',
      description: 'Emoji ou symbole de la monnaie.',
    },
    dailyAmount: {
      type: 'integer',
      min: 1,
      max: 100000,
      required: false,
      default: 100,
      label: 'Montant daily',
      description: 'Montant reçu avec /daily.',
    },
    weeklyAmount: {
      type: 'integer',
      min: 1,
      max: 1000000,
      required: false,
      default: 500,
      label: 'Montant weekly',
      description: 'Montant reçu avec /weekly.',
    },
    startBalance: {
      type: 'integer',
      min: 0,
      max: 100000,
      required: false,
      default: 0,
      label: 'Solde initial',
      description: 'Solde de départ pour les nouveaux membres.',
    },
    robEnabled: {
      type: 'boolean',
      required: false,
      default: true,
      label: 'Vol activé',
      description: 'Permettre aux membres de voler d\'autres membres.',
    },
    robChance: {
      type: 'integer',
      min: 1,
      max: 100,
      required: false,
      default: 40,
      label: 'Chance de vol (%)',
      description: 'Pourcentage de chance de réussir un vol.',
    },
    robMax: {
      type: 'integer',
      min: 1,
      max: 100,
      required: false,
      default: 30,
      label: 'Vol max (%)',
      description: 'Pourcentage maximum du solde qui peut être volé.',
    },
  },

  commands: ['balance', 'daily', 'weekly', 'pay', 'rob', 'shop', 'ecoleaderboard', 'ecoadmin'],
  events: [],
  jobs: [],
};
