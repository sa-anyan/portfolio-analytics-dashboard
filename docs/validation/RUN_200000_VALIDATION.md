# Run the 200,000-scenario validation

From the project folder:

```bash
python -m portfolio_analytics.validation.mass_validation_lab
```

You will see progress such as:

```text
[ 10,000/200,000] accuracy=... scored=... rate=... ETA=... min
```

The run intentionally includes portfolio-wide damage levels of:

`1, 2, 3, 5, 10, 25, 50, 100, 250, 500, 750, 1000, 1200` missing cells.

The key reusable file is:

```text
validation_outputs/validated_cleaning_policy.json
```

You do not manually copy its contents. `app.py` looks for it automatically and adds **Validated Cases** and **Validation Accuracy %** to repair suggestions.

The 20% holdout matters: those rows are not used when the reference pattern profile is learned, so the benchmark is less vulnerable to simply memorising the exact row being repaired.
