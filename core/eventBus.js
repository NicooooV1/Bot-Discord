// ===================================
// Ultra Suite — EventBus interne
// Découple les modules entre eux
// ===================================

const { EventEmitter } = require('events');
const { createModuleLogger } = require('./logger');

const log = createModuleLogger('EventBus');

class EventBus extends EventEmitter {
  constructor() {
    super();
    this.setMaxListeners(50);
  }

  /**
   * Émet un événement avec log
   */
  dispatch(event, data) {
    log.debug(`Dispatch: ${event}`, { keys: data ? Object.keys(data) : [] });
    this.emit(event, data);
  }
}

// Singleton
const bus = new EventBus();

module.exports = bus;
