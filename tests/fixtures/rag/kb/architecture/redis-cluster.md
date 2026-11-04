# Redis cluster

Кластер Redis из трёх узлов, maxmemory 16 GB, политика вытеснения allkeys-lru.

## Обслуживание

Обновления Redis идут по одному узлу, снимок создаётся раз в час.

Настройки Redis лежат в ansible/redis.yml.
