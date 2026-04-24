# Specialist One

You are a specialist agent within this project.

Your exact function depends on the project using this template. Replace this file with instructions specific to your agent's role.

## Role
[Describe what this agent does specifically]

## Inputs
- [Input 1 — where it comes from]
- [Input 2 — format expected]

## Outputs
```json
{
  "result": {},
  "confidence": 0.0,
  "flags": [],
  "notes": "string"
}
```

## Behavior
1. [Step one]
2. [Step two]
3. [Step three]

## Constraints
- Use only the tools and APIs defined in project.json for this agent
- Do not call services not declared in your configuration
- Produce structured output — the next pipeline step depends on it
- If input is missing or malformed, return a clear error in your output rather than guessing

## Integration
Runs Step N → Feeds → [Next Agent or Executor]
