# billing-api

Handles charges and refunds.

## Events

billing-api depends on Kafka to publish charge events.

Charges older than 90 days are archived.
