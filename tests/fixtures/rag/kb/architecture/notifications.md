# notifications-worker

Sends email and push messages.

## Pipeline

notifications-worker depends on Kafka to read order events.

The worker retries every message three times.
