# FMEA — AI-generated, chaos-validated

Generated 2026-10-10T09:29:35+00:00 · method: rule-based · AI not used: AI not requested (--no-ai)

Scales (1 best … 10 worst): **severity** 1 = no customer-visible effect ... 5 = a low-value journey degraded ... 8 = cart or checkout fails ... 10 = checkout fails or customer data is lost; **occurrence** 1 = practically impossible in production ... 5 = plausible a few times per year ... 10 = expected often (e.g. tight memory limit, single instance, no restart); **detection** 1 = existing health checks/alerts certainly detect it before customers notice ... 10 = current monitoring cannot detect it (customers notice first)

| Component | Failure mode | Effect | S | O | D | RPN | Measured S | Measured D | Measured RPN | Run |
|---|---|---|---|---|---|---|---|---|---|---|
| valkey-cart | slow network | slow network of valkey-cart degrades cart, checkout | 10 | 6 | 9 | **540** | — | — | — |  |
| valkey-cart | CPU starvation | CPU starvation of valkey-cart degrades cart, checkout | 10 | 6 | 8 | **480** | — | — | — |  |
| checkout | slow network | slow network of checkout degrades checkout | 8 | 5 | 9 | **360** | — | — | — |  |
| currency | slow network | slow network of currency degrades checkout | 8 | 5 | 9 | **360** | — | — | — |  |
| product-catalog | slow network | slow network of product-catalog degrades browse, cart, checkout, product, recommendations | 8 | 5 | 9 | **360** | — | — | — |  |
| shipping | slow network | slow network of shipping degrades checkout | 8 | 5 | 9 | **360** | — | — | — |  |
| checkout | CPU starvation | CPU starvation of checkout degrades checkout | 8 | 5 | 8 | **320** | — | — | — |  |
| currency | CPU starvation | CPU starvation of currency degrades checkout | 8 | 5 | 8 | **320** | — | — | — |  |
| product-catalog | CPU starvation | CPU starvation of product-catalog degrades browse, cart, checkout, product, recommendations | 8 | 5 | 8 | **320** | — | — | — |  |
| shipping | CPU starvation | CPU starvation of shipping degrades checkout | 8 | 5 | 8 | **320** | — | — | — |  |
| valkey-cart | hang (process frozen) | hang (process frozen) of valkey-cart degrades cart, checkout | 10 | 6 | 5 | **300** | — | — | — |  |
| cart | slow network | slow network of cart degrades cart, checkout | 8 | 4 | 9 | **288** | — | — | — |  |
| cart | crash / unavailable | crash / unavailable of cart degrades cart, checkout | 10 | 4 | 5 | **200** | — | — | — |  |

## Selected for injection (by the planner)

1. `network-delay:valkey-cart` — highest risk score among network-delay failures (rule-based fallback)  
   Hypothesis: slow network of valkey-cart degrades cart, checkout; other journeys stay within baseline; service recovers within 10 s of rollback
2. `cpu-throttle:checkout` — highest risk score among cpu-throttle failures (rule-based fallback)  
   Hypothesis: CPU starvation of checkout degrades checkout; other journeys stay within baseline; service recovers within 10 s of rollback
3. `container-stop:cart` — highest risk score among container-stop failures (rule-based fallback)  
   Hypothesis: crash / unavailable of cart degrades cart, checkout; other journeys stay within baseline; service recovers within 10 s of rollback
