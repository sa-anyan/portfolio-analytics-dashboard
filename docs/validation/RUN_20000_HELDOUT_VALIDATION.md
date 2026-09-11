# Run the 20,000-scenario held-out validation

From the project folder:

```bash
python -m portfolio_analytics.validation.mass_validation_lab
```

The script will:
1. Load the perfect 150-row ground-truth portfolio.
2. Hold out 20% of rows from learning.
3. Generate 20,000 deterministic damaged portfolio scenarios.
4. Include catastrophic scenarios with 1,000-1,200 simultaneous missing cells.
5. Repair or escalate using the real repair engine.
6. Score decisions only on held-out rows.
7. Save `validation_outputs/validated_cleaning_policy.json` automatically.

You do not need to inspect the CSV reports manually for the app to use the validated policy.
