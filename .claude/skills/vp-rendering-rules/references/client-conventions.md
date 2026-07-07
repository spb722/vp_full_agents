# Client Conventions

- Omantel names Customer 360 as `360_PROFILE`.
- Omantel may split recharge and subscription facts out of Common Segment Fact.
- Airtel day windows may use N+1 lower bounds for bounded data usage examples;
  keep this behavior in renderer logic where explicitly configured by seed or
  client convention.
- Do not hardcode client-specific conventions in prompts. Put them in
  deterministic renderer and validation code.

