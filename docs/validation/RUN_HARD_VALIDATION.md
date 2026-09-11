# v3.25 hard validation

From the project folder run:

```bash
python -m portfolio_analytics.validation.hard_validation_lab
```

This runs 20,000 held-out mixed-corruption scenarios. It deliberately includes
wrong-but-present values and contradictions, so do **not** expect 100%.

For a shorter Intel Mac run:

```bash
python -m portfolio_analytics.validation.hard_validation_lab --scenarios 5000
```

The run automatically overwrites:

```text
validation_outputs/validated_cleaning_policy.json
```

The Streamlit app loads that policy automatically the next time you run:

```bash
streamlit run app.py
```

No manual copying of report values is required.
