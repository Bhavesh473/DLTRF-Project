<?php
// src/integration/client_libs/php/src/UniversalLogger.php

namespace Universal\Logger;

use GuzzleHttp\Client;
use GuzzleHttp\Exception\RequestException;
use DateTime;

class UniversalLogger
{
    /**
     * A PHP client for sending logs to the Universal Logging Microservice API.
     *
     * @var Client
     */
    private $client;

    /**
     * @var string
     */
    private $apiUrl;

    /**
     * Constructor.
     *
     * @param string $apiUrl The base URL of the logging microservice API.
     * @param string|null $authToken Optional authentication token for API requests.
     */
    public function __construct(string $apiUrl, ?string $authToken = null)
    {
        $this->apiUrl = $apiUrl;
        $headers = $authToken ? ['Authorization' => "Bearer $authToken"] : [];
        $this->client = new Client(['headers' => $headers]);
    }

    /**
     * Send a log entry to the microservice API.
     *
     * @param string $level The log level (e.g., 'INFO', 'ERROR').
     * @param string $message The log message content.
     * @param string $source The source of the log (e.g., app name).
     * @param array $metadata Additional metadata for the log (optional).
     *
     * @return array The API response data.
     *
     * @throws \Exception If the API request fails.
     */
    public function log(string $level, string $message, string $source, array $metadata = []): array
    {
        $payload = [
            'timestamp' => (new DateTime())->format(DateTime::ATOM),
            'level' => $level,
            'message' => $message,
            'source' => $source,
            'metadata' => $metadata
        ];

        try {
            $response = $this->client->post(
                $this->apiUrl . '/logs',
                ['json' => $payload]
            );
            return json_decode($response->getBody()->getContents(), true);
        } catch (RequestException $e) {
            throw new \Exception("Failed to send log: " . $e->getMessage());
        }
    }
}

// Example usage (uncomment to test locally)
// $logger = new UniversalLogger('http://localhost:8000');
// $logger->log('INFO', 'Test log', 'my_app', ['key' => 'value']); 