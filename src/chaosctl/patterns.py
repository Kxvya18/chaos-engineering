"""Catalog of industry-standard resilience patterns the AI may recommend (and must cite by id).

References are public, vendor-neutral or widely adopted sources. The AI cannot invent a pattern id;
`incident.validate` rejects unknown ids.
"""

from __future__ import annotations

PATTERNS: dict[str, dict[str, object]] = {
    "DATA_PERSISTENCE": {
        "name": "Durable state (append-only persistence on a volume)",
        "when": "A datastore restarts but its in-memory data is lost.",
        "refs": ["https://valkey.io/topics/persistence/",
                 "https://docs.docker.com/engine/storage/volumes/"],
    },
    "REPLICATION_FAILOVER": {
        "name": "Replication with automatic failover",
        "when": "A single datastore instance is a single point of failure.",
        "refs": ["https://valkey.io/topics/replication/",
                 "https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html"],
    },
    "SELF_HEALING_RESTART": {
        "name": "Supervisor restart policy / self-healing workload",
        "when": "A crashed process is not brought back automatically.",
        "refs": ["https://docs.docker.com/engine/containers/start-containers-automatically/",
                 "https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/"],
    },
    "HEALTH_PROBES": {
        "name": "Liveness / readiness / startup probes",
        "when": "A service is unhealthy but still receives traffic, or has no health check at all.",
        "refs": ["https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/",
                 "https://docs.docker.com/reference/dockerfile/#healthcheck"],
    },
    "DEPENDENCY_ORDERING": {
        "name": "Start/route only when dependencies are healthy",
        "when": "A service starts or serves before its datastore is ready.",
        "refs": ["https://docs.docker.com/compose/how-tos/startup-order/"],
    },
    "TIMEOUTS_RETRIES_BACKOFF": {
        "name": "Timeouts, bounded retries, exponential backoff with jitter",
        "when": "Calls hang or fail on transient errors.",
        "refs": ["https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/",
                 "https://learn.microsoft.com/en-us/azure/architecture/patterns/retry"],
    },
    "CIRCUIT_BREAKER": {
        "name": "Circuit breaker",
        "when": "A failing dependency keeps being called and drags callers down.",
        "refs": ["https://learn.microsoft.com/en-us/azure/architecture/patterns/circuit-breaker"],
    },
    "BULKHEAD": {
        "name": "Bulkhead isolation",
        "when": "One slow dependency exhausts shared threads/connections.",
        "refs": ["https://learn.microsoft.com/en-us/azure/architecture/patterns/bulkhead"],
    },
    "GRACEFUL_DEGRADATION": {
        "name": "Graceful degradation (hard dependency -> soft dependency)",
        "when": "A non-critical dependency failure breaks a whole user journey.",
        "refs": ["https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/"
                 "rel_mitigate_interaction_failure_graceful_degradation.html"],
    },
    "LOAD_SHEDDING": {
        "name": "Load shedding / rate limiting",
        "when": "Traffic beyond capacity degrades every request instead of rejecting some.",
        "refs": ["https://aws.amazon.com/builders-library/using-load-shedding-to-avoid-overload/"],
    },
    "CAPACITY_RIGHTSIZING": {
        "name": "Right-size resources / autoscale",
        "when": "CPU or memory limits cause throttling or OOM kills.",
        "refs": ["https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/"],
    },
    "SLO_ALERTING": {
        "name": "SLO-based alerting on customer journeys",
        "when": "Infrastructure checks stay green while customers are affected.",
        "refs": ["https://sre.google/workbook/alerting-on-slos/"],
    },
}

PATTERN_IDS = sorted(PATTERNS)
