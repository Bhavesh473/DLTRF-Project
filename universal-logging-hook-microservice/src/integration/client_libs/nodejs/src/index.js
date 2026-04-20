//src/integration/client_libs/nodejs/src/index.js

const axios = require('axios');

class UniversalLogger {
  /**
   * A Node.js client for sending logs to the Universal Logging Microservice API.
   * 
   * @param {string} apiUrl - The base URL of the logging microservice API.
   * @param {string} [authToken] - Optional authentication token for API requests.
   */
  constructor(apiUrl, authToken = null) {
    this.apiUrl = apiUrl;
    this.headers = authToken ? { Authorization: `Bearer ${authToken}` } : {};
  }

  /**
   * Send a log entry to the microservice API.
   * 
   * @param {string} level - The log level (e.g., 'INFO', 'ERROR').
   * @param {string} message - The log message content.
   * @param {string} source - The source of the log (e.g., app name).
   * @param {Object} [metadata={}] - Additional metadata for the log.
   * @returns {Promise<Object>} - The API response data.
   * @throws {Error} - If the API request fails.
   */
  async log(level, message, source, metadata = {}) {
    const payload = {
      timestamp: new Date().toISOString(),
      level,
      message,
      source,
      metadata
    };

    try {
      const response = await axios.post(
        `${this.apiUrl}/logs`,
        payload,
        { headers: this.headers }
      );
      return response.data;
    } catch (error) {
      throw new Error(`Failed to send log: ${error.message}`);
    }
  }
}

module.exports = UniversalLogger;

// Example usage (uncomment to test locally)
// const logger = new UniversalLogger('http://localhost:8000');
// logger.log('INFO', 'Test log', 'my_app', { key: 'value' }).then(console.log).catch(console.error);